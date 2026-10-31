"""Shared Playwright engine. Fills every field it can understand; clicks submit only when dry_run is False."""
import re
import time
from playwright.sync_api import sync_playwright, Page
from ..config import DATA_DIR, HEADLESS
from ..answerer import Answerer


# Collects radio / checkbox groups and their question text from the DOM.
GROUPS_JS = r"""
() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g,' ').trim();
  const labelFor = inp => {
    if (inp.id) { const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`); if (l) return txt(l); }
    const l = inp.closest('label'); if (l) return txt(l);
    return inp.getAttribute('aria-label') || inp.value || '';
  };
  const groups = {};
  document.querySelectorAll('input[type=radio], input[type=checkbox]').forEach((inp, i) => {
    const box = inp.closest('label') || inp;
    if (!vis(box) && !vis(inp.parentElement)) return;
    const key = (inp.type) + ':' + (inp.name || ('noname'+i));
    if (!groups[key]) {
      const fs = inp.closest('fieldset');
      let q = fs ? txt(fs.querySelector('legend')) : '';
      if (!q) {
        let c = inp.parentElement;
        for (let k = 0; k < 6 && c; k++, c = c.parentElement) {
          const lab = c.querySelector(':scope > label, :scope > .text, :scope > div > label, :scope > legend, :scope > .application-label');
          if (lab && !lab.contains(inp)) { q = txt(lab); break; }
        }
      }
      groups[key] = {key, type: inp.type, name: inp.name, question: q, options: []};
    }
    groups[key].options.push({label: labelFor(inp), id: inp.id, value: inp.value, checked: inp.checked});
  });
  return Object.values(groups);
}
"""


class SubmitError(Exception):
    def __init__(self, code, msg, shots=None):
        super().__init__(msg); self.code, self.shots = code, shots or []


class NeedsManual(SubmitError):
    pass


class BaseSubmitter:
    ats = "base"
    submit_selectors = ["button[type=submit]", "input[type=submit]", "button:has-text('Submit')"]

    def __init__(self, app, prof, answerer: Answerer, resume_pdf, credentials=None, dry_run=True):
        self.app, self.prof, self.ans, self.resume_pdf = app, prof, answerer, resume_pdf
        self.d = prof.data or {}
        self.creds, self.dry_run = credentials or {}, dry_run
        self.out = DATA_DIR / "artifacts" / f"u{app.user_id}" / f"app_{app.id}"; self.out.mkdir(parents=True, exist_ok=True)
        self.shots, self.filled, self.log = [], [], []

    # ---------- low-level helpers ----------
    def shot(self, page: Page, name):
        path = str(self.out / f"{int(time.time())}_{name}.png")
        page.screenshot(path=path, full_page=True); self.shots.append(path); return path

    def fill(self, page, selector, value, label=None):
        if not value:
            return False
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible() and not loc.first.input_value():
                loc.first.fill(str(value), timeout=5000); self.filled.append(label or selector); return True
        except Exception as e:
            self.log.append(f"fill {selector}: {str(e)[:120]}")
        return False

    def upload(self, page, selector, path, label="resume"):
        loc = page.locator(selector)
        if path and loc.count():
            loc.first.set_input_files(path); self.filled.append(label); return True
        return False

    def label_of(self, page, el, container_fallback=True):
        eid = el.get_attribute("id")
        if eid:
            lab = page.locator(f"label[for='{eid}']")
            if lab.count():
                return re.sub(r"\s+", " ", lab.first.inner_text()).strip()
        direct = el.get_attribute("aria-label") or el.evaluate("e => (e.closest('label')||{}).innerText || ''") \
            or el.get_attribute("placeholder")
        if not direct and el.get_attribute("aria-labelledby"):
            direct = el.evaluate("e => e.getAttribute('aria-labelledby').split(' ').map(i => (document.getElementById(i)||{}).innerText||'').join(' ')")
        if not direct and container_fallback:
            # nearest question container whose text is short enough to be ONE question
            direct = el.evaluate("""e => { let c = e.parentElement;
                for (let k = 0; k < 4 && c; k++, c = c.parentElement) {
                  const t = (c.innerText || '').trim();
                  if (t && c.querySelectorAll('input,textarea,select').length === 1) return t.split('\\n')[0];
                } return ''; }""")
        return re.sub(r"\s+", " ", direct or "").strip()[:300]

    # ---------- generic question filling ----------
    def fill_text_questions(self, page):
        for el in page.locator("input[type=text]:not([role=combobox]):visible, input:not([type]):not([role=combobox]):visible,"
                               " input[type=url]:visible, input[type=number]:visible, textarea:visible").all():
            try:
                if el.input_value() or (el.get_attribute("name") or "").startswith("g-recaptcha"):
                    continue
                if el.get_attribute("aria-hidden") == "true" or el.get_attribute("tabindex") == "-1" or \
                        el.evaluate("e => !!e.closest('[class*=select__],[class*=Select],[role=listbox]')"):
                    continue
                q = self.label_of(page, el)
                if not q or re.search(r"search|captcha", q, re.I):
                    continue
                a = self.ans.text(q)
                if a:
                    el.fill(a, timeout=5000); self.filled.append(f"Q: {q[:50]}")
            except Exception as e:
                self.log.append(f"text q: {str(e)[:120]}")

    def fill_native_selects(self, page):
        for el in page.locator("select:visible").all():
            try:
                cur = el.evaluate("e => e.selectedIndex > 0 ? e.options[e.selectedIndex].text : ''")
                if cur:
                    continue
                opts = el.evaluate("e => Array.from(e.options).map(o => o.text.trim())")
                q = self.label_of(page, el)
                a = self.ans.choose(q, opts)
                if a:
                    el.select_option(label=a); self.filled.append(f"Select: {q[:40]} -> {a}")
            except Exception as e:
                self.log.append(f"select: {str(e)[:120]}")

    def fill_comboboxes(self, page):
        for el in page.locator("input[role=combobox]:visible").all():
            try:
                if el.input_value():
                    continue
                # react-select shows the chosen value in a sibling, not the input
                has_val = el.evaluate("e => !!(e.closest('[class*=control]')||e.parentElement).querySelector('[class*=single-value],[class*=singleValue],[class*=multi-value]')")
                if has_val:
                    continue
                q = self.label_of(page, el)
                if not q:
                    continue
                el.click(timeout=3000); page.wait_for_timeout(500)
                opts_loc = page.locator("[role=option]:visible")
                opts = [re.sub(r"\s+", " ", t).strip() for t in opts_loc.all_inner_texts()][:200]
                if not opts or len(opts) > 150:  # long lists (countries, schools): type to filter
                    guess = self.ans.text(q)
                    if guess:
                        el.fill(guess); page.wait_for_timeout(700)
                        opts = [re.sub(r"\s+", " ", t).strip() for t in page.locator("[role=option]:visible").all_inner_texts()][:50]
                a = self.ans.choose(q, opts) if opts else None
                if a:
                    page.locator("[role=option]:visible", has_text=a).first.click(timeout=3000)
                    self.filled.append(f"Select: {q[:40]} -> {a}")
                else:
                    el.press("Escape")
            except Exception as e:
                self.log.append(f"combobox: {str(e)[:120]}")
                try: page.keyboard.press("Escape")
                except Exception: pass

    def fill_choice_groups(self, page):
        for g in page.evaluate(GROUPS_JS):
            try:
                if any(o["checked"] for o in g["options"]):
                    continue
                labels = [o["label"] for o in g["options"]]
                q = g["question"] or g["name"]
                if g["type"] == "checkbox" and len(g["options"]) == 1:
                    # lone checkbox: consent/acknowledgement
                    a = self.ans.choose(q + " " + labels[0], ["Yes", "No"])
                    picks = labels if a == "Yes" else []
                elif g["type"] == "checkbox":
                    picks = self.ans.choose(q, labels, multi=True) or []
                else:
                    a = self.ans.choose(q, labels); picks = [a] if a else []
                for p in picks:
                    o = next(o for o in g["options"] if o["label"] == p)
                    loc = page.locator(f"[id='{o['id']}']") if o["id"] else page.locator(
                        f"input[name='{g['name']}'][value='{o['value']}']")
                    try:
                        loc.first.check(timeout=3000, force=True)
                    except Exception:
                        page.get_by_text(p, exact=True).first.click(timeout=3000)
                if picks:
                    self.filled.append(f"{g['type'].title()}: {q[:40]} -> {', '.join(picks)[:60]}")
            except Exception as e:
                self.log.append(f"choice: {str(e)[:120]}")

    def captcha_present(self, page):
        loc = page.locator("iframe[src*='bframe'], iframe[src*='hcaptcha.com'], iframe[src*='challenges.cloudflare']")
        return any(l.is_visible() for l in loc.all())

    # ---------- flow ----------
    def open(self, page):
        page.goto(self.app.apply_url or self.app.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        for sel in ("button:has-text('Accept')", "button:has-text('Accept all')", "#onetrust-accept-btn-handler"):
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                try: b.first.click(timeout=2000)
                except Exception: pass

    def fill_form(self, page):
        raise NotImplementedError

    def upload_cover_letter_file(self, page):
        """If the form has a cover-letter FILE field, upload a PDF of the cover letter."""
        if not self.ans.cl or "cover_letter" in self.filled:
            return
        for el in page.locator("input[type=file]").all():
            try:
                lab = el.evaluate("e => { let c=e; for (let k=0;k<6&&c;k++,c=c.parentElement){ const t=(c.innerText||'').toLowerCase(); if (t.includes('cover letter')) return t.length<400; } return false; }")
                if lab and not el.evaluate("e => e.files && e.files.length > 0"):
                    from ..pdf import text_to_pdf
                    p = text_to_pdf(self.ans.cl, str(self.out / "cover_letter.pdf"))
                    el.set_input_files(p); self.filled.append("cover_letter_file"); page.wait_for_timeout(800); return
            except Exception as e:
                self.log.append(f"cl upload: {str(e)[:100]}")

    def fill_generic(self, page):
        self.upload_cover_letter_file(page)
        self.fill_text_questions(page)
        self.fill_native_selects(page)
        self.fill_comboboxes(page)
        self.fill_choice_groups(page)

    def submit(self, page):
        for sel in self.submit_selectors:
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                b.first.click(); page.wait_for_timeout(6000); return
        raise SubmitError("SUBMIT_BUTTON_NOT_FOUND", "Could not find the submit button")

    def run(self) -> dict:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US",
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
            page = ctx.new_page()
            try:
                self.open(page)
                self.fill_form(page)
                self.fill_generic(page)
                page.wait_for_timeout(800)
                self.shot(page, "filled")
                if self.dry_run:
                    return self._result(False)
                self.submit(page)
                self.shot(page, "after_submit")
                if self.captcha_present(page):
                    raise NeedsManual("CAPTCHA", "A CAPTCHA appeared on submit. Open the link, solve it and submit manually.")
                body = page.inner_text("body").lower()
                if re.search(r"thank you|application (has been )?(received|submitted)|we.ve received", body):
                    return self._result(True)
                if re.search(r"required|can't be blank|is invalid|please (fill|complete)", body):
                    raise NeedsManual("VALIDATION", "The form reported missing/invalid fields after submit.")
                return self._result(True, confirmed=False)
            except SubmitError as e:
                try: self.shot(page, "error")
                except Exception: pass
                e.shots = self.shots; raise
            except Exception as e:
                try: self.shot(page, "error")
                except Exception: pass
                raise SubmitError("SUBMITTER_ERROR", str(e)[:500], self.shots)
            finally:
                browser.close()

    def _result(self, submitted, confirmed=True):
        return {"submitted": submitted, "confirmed": confirmed, "screenshots": self.shots, "filled": self.filled, "log": self.log}
