"""Workday: best-effort. Needs an existing Workday account per tenant (email+password)."""
from .base import BaseSubmitter, SubmitError


class WorkdaySubmitter(BaseSubmitter):
    ats = "workday"
    submit_selectors = ["button[data-automation-id='bottom-navigation-next-button']:has-text('Submit')"]

    def fill_form(self, page):
        p = self.p
        if not (p.workday_email and p.workday_password):
            raise SubmitError("WORKDAY_PASSWORD_REQUIRED", "Workday needs an account email/password in the profile")
        page.locator("a[data-automation-id='adventureButton'], button:has-text('Apply')").first.click()
        page.wait_for_timeout(2500)
        manual = page.locator("a[data-automation-id='applyManually'], button:has-text('Apply Manually')")
        if manual.count():
            manual.first.click(); page.wait_for_timeout(2500)
        signin = page.locator("button[data-automation-id='signInLink'], button:has-text('Sign In')")
        if signin.count():
            signin.first.click(); page.wait_for_timeout(1500)
        self.fill(page, "input[data-automation-id='email']", p.workday_email, "wd_email")
        self.fill(page, "input[data-automation-id='password']", p.workday_password, "wd_password")
        self.shot(page, "login")
        if self.dry_run:
            return  # don't log in during dry-run; login itself is an account action
        page.locator("button[data-automation-id='signInSubmitButton'], div[aria-label='Sign In']").first.click()
        page.wait_for_timeout(5000)
        self.fill(page, "input[data-automation-id='legalNameSection_firstName']", p.first_name, "first_name")
        self.fill(page, "input[data-automation-id='legalNameSection_lastName']", p.last_name, "last_name")
        self.fill(page, "input[data-automation-id='phone-number']", p.phone, "phone")
        # Multi-page wizard: resume upload lives on "My Experience".
        nxt = page.locator("button[data-automation-id='bottom-navigation-next-button']")
        if nxt.count():
            nxt.first.click(); page.wait_for_timeout(3000)
            self.upload(page, "input[type=file]", self.resume_pdf)
        # Remaining pages (questions, EEO, review) vary per tenant -> left to REVIEW by a human.
