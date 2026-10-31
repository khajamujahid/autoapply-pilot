"""Shared Playwright filling logic. Never clicks submit unless dry_run is False."""
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, Page
from ..config import DATA_DIR, HEADLESS
from ..llm import answer_question


class SubmitError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg); self.code = code


class BaseSubmitter:
    ats = "base"
    submit_selectors = ["button[type=submit]", "input[type=submit]", "button:has-text('Submit')"]

    def __init__(self, app, profile, resume_pdf, cover_letter, dry_run=True):
        self.app, self.p, self.resume_pdf, self.cl, self.dry_run = app, profile, resume_pdf, cover_letter, dry_run
        self.out = DATA_DIR / "artifacts" / f"app_{app.id}"; self.out.mkdir(parents=True, exist_ok=True)
        self.shots, self.filled, self.log = [], [], []

    # ---- helpers -------------------------------------------------------
    def shot(self, page: Page, name):
        path = str(self.out / f"{int(time.time())}_{name}.png")
        page.screenshot(path=path, full_page=True); self.shots.append(path); return path

    def fill(self, page: Page, selector, value, label=None):
        if not value:
            return False
        loc = page.locator(selector)
        if loc.count() and loc.first.is_visible():
            try:
                loc.first.fill(str(value)); self.filled.append(label or selector); return True
            except Exception as e:
                self.log.append(f"fill fail {selector}: {e}")
        return False

    def upload(self, page: Page, selector, path, label="resume"):
        loc = page.locator(selector)
        if path and loc.count():
            loc.first.set_input_files(path); self.filled.append(label); return True
        return False

    def fill_by_label(self, page: Page):
        """Answer remaining visible empty text inputs/textareas by their label text."""
        for el in page.locator("input[type=text]:not([role=combobox]):visible, input:not([type]):not([role=combobox]):visible, textarea:visible").all():
            try:
                if el.input_value():
                    continue
                eid = el.get_attribute("id")
                label = ""
                if eid:
                    lab = page.locator(f"label[for='{eid}']")
                    label = lab.first.inner_text() if lab.count() else ""
                label = label or el.get_attribute("aria-label") or el.get_attribute("placeholder") or el.get_attribute("name") or ""
                label = re.sub(r"\s+", " ", label).strip()
                if not label or re.search(r"search|captcha|recaptcha", label, re.I):
                    continue
                if re.search(r"cover letter", label, re.I):
                    ans = self.cl
                else:
                    ans = answer_question(self.p, label)
                if ans and ans != "N/A":
                    el.fill(ans); self.filled.append(f"Q: {label[:60]}")
            except Exception as e:
                self.log.append(f"label fill skip: {e}")

    def fill_comboboxes(self, page: Page):
        """React-select style dropdowns (Greenhouse/Ashby): type the answer and pick the first option."""
        for el in page.locator("input[role=combobox]:visible").all():
            try:
                if el.get_attribute("aria-expanded") == "true" or el.input_value():
                    continue
                eid = el.get_attribute("id") or ""
                lab = page.locator(f"label[for='{eid}']") if eid else None
                label = (lab.first.inner_text() if lab is not None and lab.count() else el.get_attribute("aria-label") or "")
                label = re.sub(r"\s+", " ", label).strip()
                if not label or re.search(r"gender|race|hispanic|veteran|disability|ethnic", label, re.I):
                    continue  # leave voluntary EEO questions blank
                ans = answer_question(self.p, label)
                if not ans or ans == "N/A":
                    continue
                el.click(); el.fill(ans); page.wait_for_timeout(700)
                opt = page.locator("[role=option]:visible")
                if opt.count():
                    opt.first.click(); self.filled.append(f"Select: {label[:50]} -> {ans}")
                else:
                    el.press("Escape"); el.fill("")
            except Exception as e:
                self.log.append(f"combobox skip: {e}")

    # ---- flow ------------------------------------------------------------
    def open(self, page: Page):
        page.goto(self.app.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

    def fill_form(self, page: Page):
        raise NotImplementedError

    def submit(self, page: Page):
        for sel in self.submit_selectors:
            b = page.locator(sel)
            if b.count() and b.first.is_visible():
                b.first.click(); page.wait_for_timeout(5000); return True
        raise SubmitError("SUBMIT_BUTTON_NOT_FOUND", "Could not find submit button")

    def run(self) -> dict:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
            page = ctx.new_page()
            try:
                self.open(page)
                self.fill_form(page)
                self.fill_by_label(page)
                self.fill_comboboxes(page)
                page.wait_for_timeout(800)
                self.shot(page, "filled")
                if self.dry_run:
                    result = {"submitted": False}
                else:
                    self.submit(page)
                    self.shot(page, "after_submit")
                    body = page.inner_text("body").lower()
                    if re.search(r"captcha|verify you are human", body):
                        raise SubmitError("CAPTCHA", "Captcha required; finish manually")
                    result = {"submitted": True, "confirmed": bool(re.search(r"thank|received|submitted", body))}
            except SubmitError as e:
                try: self.shot(page, "error")
                except Exception: pass
                e.shots = self.shots
                raise
            except Exception as e:
                try: self.shot(page, "error")
                except Exception: pass
                err = SubmitError("SUBMITTER_ERROR", str(e)[:500]); err.shots = self.shots
                raise err
            finally:
                browser.close()
        result.update(screenshots=self.shots, filled=self.filled, log=self.log)
        return result
