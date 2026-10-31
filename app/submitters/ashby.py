from .base import BaseSubmitter


class AshbySubmitter(BaseSubmitter):
    ats = "ashby"
    submit_selectors = ["button:has-text('Submit Application')", "button[type=submit]"]

    def open(self, page):
        u = self.app.url.rstrip("/")
        if not u.endswith("/application"):
            u += "/application"
        page.goto(u, wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(4000)

    def fill_form(self, page):
        p = self.p
        self.fill(page, "input[name='_systemfield_name']", f"{p.first_name} {p.last_name}", "name")
        self.fill(page, "input[name='_systemfield_email']", p.email, "email")
        self.fill(page, "input[type='tel']", p.phone, "phone")
        self.upload(page, "input[type=file][id*='resume' i], input[type=file]", self.resume_pdf)
        page.wait_for_timeout(2000)
        self.fill(page, "input[name*='linkedin' i]", p.linkedin, "linkedin")
