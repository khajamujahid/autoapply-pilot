"""Universal form filler for any ATS without a dedicated submitter (iCIMS, SmartRecruiters, Taleo, SuccessFactors,
UltiPro/UKG, Avature, ContactHR, Workable, BambooHR, JazzHR, company career sites ...).
Finds the real application form (clicks 'Apply', follows new tabs and iframes), fills it from the profile,
Application Questions and AI answers, uploads the tailored resume and walks multi-step wizards.
Never bypasses CAPTCHAs or logins: those stop with a clear, actionable reason."""
import re
from urllib.parse import urlparse
from .base import BaseSubmitter, SubmitError, NeedsManual

APPLY_RX = re.compile(r"^\s*(apply( now| for this (job|position|role)| online| here)?|i'?m interested|start (your )?application|submit (an )?application)\s*$", re.I)
NEXT_RX = re.compile(r"^\s*(next|continue|save (and|&) continue|proceed|next step)\s*$", re.I)

# Visible inputs with a best-effort label, for the standard contact fields.
FIELDS_JS = r"""() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const out = [];
  document.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox]):not([type=submit]):not([type=button]), textarea').forEach((e, i) => {
    if (!vis(e) || e.value) return;
    let l = '';
    if (e.id) { const x = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (x) l = x.innerText; }
    if (!l) { const x = e.closest('label'); if (x) l = x.innerText; }
    const key = [l, e.getAttribute('aria-label'), e.placeholder, e.name, e.id, e.getAttribute('autocomplete'), e.type].join(' | ');
    e.setAttribute('data-ap-idx', String(i));
    out.push({idx: String(i), key: key.replace(/\s+/g, ' ').slice(0, 300)});
  });
  return out; }"""

STD = [  # (regex on label/name/id/autocomplete, profile key)
    (r"first.?name|given.?name|fname|\bfirst\b", "first_name"), (r"last.?name|family.?name|surname|lname|\blast\b", "last_name"),
    (r"full.?name|^\s*name\s*[*|]|your name|legal name\b(?!.*(first|last))", "full_name"), (r"e-?mail", "email"),
    (r"phone|mobile|\btel\b", "phone"), (r"linkedin", "linkedin"), (r"github", "github"),
    (r"website|portfolio|personal (site|url)", "website"), (r"\bcity\b", "city"), (r"\bstate\b|province|region", "state"),
    (r"zip|postal", "zip"), (r"street|address( line)? ?1|^address", "address"), (r"country", "country"),
    (r"current (company|employer)", "current_company"), (r"current (job )?title", "current_title"),
]


class UniversalSubmitter(BaseSubmitter):
    ats = "universal"
    submit_selectors = ["button:visible:has-text('Submit Application')", "button:visible:has-text('Submit application')",
                        "input[type=submit][value*='Submit' i]:visible", "button[type=submit]:visible:has-text('Submit')",
                        "button:visible:has-text('Submit')", "button:visible:has-text('Send application')",
                        "button:visible:has-text('Apply')", "button[type=submit]:visible", "input[type=submit]:visible"]

    def _has_form(self, page):
        try:
            return page.locator("input[type=file], input[type=email], input[name*='email' i], input[id*='email' i], "
                                "input[autocomplete='given-name']").count() > 0
        except Exception:
            return False

    def _login_wall(self, page):
        try:
            pw = page.locator("input[type=password]:visible").count()
        except Exception:
            return False
        return pw > 0 and not page.locator("input[type=file]").count()

    def _settle(self, page):
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def _closed(self, page):
        if "error=true" in page.url:
            return True
        try:
            body = page.inner_text("body", timeout=3000).lower()[:4000]
        except Exception:
            return False
        return bool(re.search(r"no longer (accepting|available|open)|position has been filled|job (is )?(not found|closed|expired)"
                              r"|this (job|posting) (has )?(expired|closed)|page (you.re looking for )?(not found|doesn.t exist)", body))

    def open(self, page):
        super().open(page)
        self._settle(page)
        for _ in range(3):
            if self._has_form(page):
                break
            # form inside an iframe (ContactHR, iCIMS, some career sites): load the frame directly
            fr = next((f for f in page.frames[1:] if f.url.startswith("http") and
                       f.locator("input[type=file], input[type=email]").count()), None) if page.frames else None
            if fr:
                page.goto(fr.url, wait_until="domcontentloaded", timeout=45000); page.wait_for_timeout(1500); continue
            btn = None
            for el in page.locator("a:visible, button:visible, input[type=button]:visible, [role=button]:visible").all()[:400]:
                try:
                    txt = (el.inner_text(timeout=500) or el.get_attribute("value") or "").strip()
                except Exception:
                    continue
                if APPLY_RX.match(txt):
                    btn = el; break
            if not btn:
                break
            href = btn.get_attribute("href") or ""
            try:
                with page.context.expect_page(timeout=4000) as newp:
                    btn.click(timeout=5000)
                np = newp.value; np.wait_for_load_state("domcontentloaded")
                url = np.url; np.close()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                if href.startswith("http") and urlparse(href).netloc != urlparse(page.url).netloc:
                    page.goto(href, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500); self._settle(page)
        self.log.append(f"form page: {page.url}")
        if self._closed(page):
            raise SubmitError("JOB_CLOSED", f"The posting is closed or removed ({page.url}). Nothing to submit.")

    def _cred(self, page):
        h = urlparse(page.url).netloc.lower().removeprefix("www.")
        for site, c in self.creds.items():
            s = site.lower().split(":")[-1]
            if s and (s in h or h.split(".")[0] == s):
                return c
        return None

    def fill_form(self, page):
        from ..ats import detect
        real = detect(page.url)[0]
        if real in ("greenhouse", "lever", "ashby", "workday"):  # the Apply button led to an ATS we handle natively
            from . import SUBMITTERS
            self.log.append(f"delegating to {real} submitter")
            self.submit_selectors = SUBMITTERS[real].submit_selectors + self.submit_selectors
            if real == "workday":
                self.app = type("A", (), {"url": page.url, "apply_url": page.url, "id": self.app.id, "user_id": self.app.user_id})()  # tenant comes from the real host
            return SUBMITTERS[real].fill_form(self, page)
        if self._login_wall(page):
            cred = self._cred(page)
            host = urlparse(page.url).netloc
            if not cred:
                raise NeedsManual("ACCOUNT_REQUIRED", f"{host} needs a candidate account before the form appears. Create one once, "
                                  f"then save it in Profile → Credentials with site '{host}' and click Retry.")
            self.fill(page, "input[type=email]:visible, input[name*='user' i]:visible, input[name*='email' i]:visible", cred[0], "login")
            self.fill(page, "input[type=password]:visible", cred[1], "password")
            page.locator("button[type=submit]:visible, input[type=submit]:visible, button:visible:has-text('Sign In'), "
                         "button:visible:has-text('Log in')").first.click(timeout=8000)
            page.wait_for_timeout(4000)
            if self.captcha_present(page):
                raise NeedsManual("CAPTCHA", f"{host} login shows a CAPTCHA")
            if self._login_wall(page):
                raise NeedsManual("LOGIN_FAILED", f"Saved login for {host} was rejected")
        if not self._has_form(page) and not page.locator("input:visible, textarea:visible").count():
            raise NeedsManual("NO_FORM_FOUND", f"No application form found at {page.url}. The site may need an account "
                              "or apply via email; open the link to finish.")
        for step in range(8):  # multi-step wizards
            self.fill_standard(page)
            self.upload_resume(page)
            self.fill_generic(page)
            if self._submit_visible(page):
                break
            nxt = next((b for b in page.locator("button:visible, input[type=button]:visible, a[role=button]:visible").all()[:200]
                        if NEXT_RX.match((b.inner_text(timeout=300) if b.evaluate("e=>e.tagName")!='INPUT' else b.get_attribute('value')) or "")), None)
            if not nxt:
                break
            miss = self.unfilled_required(page)
            if miss:
                self.missing = miss; break
            nxt.click(timeout=5000); page.wait_for_timeout(2500); self.shot(page, f"step{step + 1}")

    def _submit_visible(self, page):
        return any(page.locator(s).count() and re.search(r"submit|send", (page.locator(s).first.inner_text(timeout=300)
                   if "input" not in s else page.locator(s).first.get_attribute("value") or ""), re.I)
                   for s in self.submit_selectors[:6])

    def fill_standard(self, page):
        d = dict(self.d)
        d.setdefault("full_name", " ".join(x for x in [d.get("first_name"), d.get("last_name")] if x))
        loc = str(d.get("location") or "")
        parts = [x.strip() for x in loc.split(",")]
        d.setdefault("city", parts[0] if parts and parts[0] else "")
        d.setdefault("state", parts[1] if len(parts) > 1 else "")
        for f in page.evaluate(FIELDS_JS):
            key = f["key"].lower()
            for rx, k in STD:
                if re.search(rx, key) and d.get(k):
                    if self.fill(page, f"[data-ap-idx='{f['idx']}']", d[k], k):
                        break

    def upload_resume(self, page):
        if not self.resume_pdf:
            return
        files = page.locator("input[type=file]").all()
        for i, el in enumerate(files):
            try:
                if el.evaluate("e => e.files && e.files.length > 0"):
                    continue
                ctx = el.evaluate("e => { let c=e; for (let k=0;k<5&&c;k++,c=c.parentElement){ const t=(c.innerText||'').toLowerCase(); if (t.length>3) return t.slice(0,300);} return ''; }")
                if "cover" in ctx and "resume" not in ctx:
                    continue
                if re.search(r"resume|cv|curriculum|attach|upload", ctx) or i == 0:
                    el.set_input_files(self.resume_pdf); self.filled.append("resume"); page.wait_for_timeout(1200); return
            except Exception as e:
                self.log.append(f"upload: {str(e)[:100]}")
