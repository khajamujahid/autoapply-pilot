"""Workday (best-effort). Needs a saved credential for the tenant (Settings → Credentials, site 'workday:<tenant>' or 'workday')."""
from urllib.parse import urlparse
from .base import BaseSubmitter, SubmitError, NeedsManual


class WorkdaySubmitter(BaseSubmitter):
    ats = "workday"
    submit_selectors = ["button[data-automation-id='bottom-navigation-next-button']:has-text('Submit')"]

    def fill_form(self, page):
        tenant = urlparse(self.app.url).netloc.split(".")[0]
        cred = self.creds.get(f"workday:{tenant}") or self.creds.get("workday")
        if not cred:
            raise SubmitError("WORKDAY_PASSWORD_REQUIRED", f"Add a Workday credential for 'workday:{tenant}' in Profile → Credentials")
        page.locator("a[data-automation-id='adventureButton'], button:has-text('Apply')").first.click(timeout=15000)
        page.wait_for_timeout(2500)
        manual = page.locator("a[data-automation-id='applyManually'], button:has-text('Apply Manually')")
        if manual.count():
            manual.first.click(); page.wait_for_timeout(2500)
        signin = page.locator("button[data-automation-id='signInLink'], button:has-text('Sign In')")
        if signin.count():
            signin.first.click(); page.wait_for_timeout(1500)
        self.fill(page, "input[data-automation-id='email']", cred[0], "wd_email")
        self.fill(page, "input[data-automation-id='password']", cred[1], "wd_password")
        self.shot(page, "login")
        if self.dry_run:
            return
        page.locator("button[data-automation-id='signInSubmitButton'], div[aria-label='Sign In']").first.click()
        page.wait_for_timeout(6000)
        if self.captcha_present(page):
            raise NeedsManual("CAPTCHA", "Workday login shows a CAPTCHA")
        d = self.d
        self.fill(page, "input[data-automation-id='legalNameSection_firstName']", d.get("first_name"), "first_name")
        self.fill(page, "input[data-automation-id='legalNameSection_lastName']", d.get("last_name"), "last_name")
        self.fill(page, "input[data-automation-id='phone-number']", d.get("phone"), "phone")
        for _ in range(5):  # walk the wizard pages, filling what we understand
            self.fill_generic(page)
            up = page.locator("input[type=file]")
            if up.count():
                up.first.set_input_files(self.resume_pdf)
            nxt = page.locator("button[data-automation-id='bottom-navigation-next-button']")
            if not nxt.count() or "submit" in nxt.first.inner_text().lower():
                break
            nxt.first.click(); page.wait_for_timeout(3500)
