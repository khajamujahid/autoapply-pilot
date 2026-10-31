from .base import BaseSubmitter


class LeverSubmitter(BaseSubmitter):
    ats = "lever"
    submit_selectors = ["#btn-submit", "button[data-qa='btn-submit']", "button[type=submit]"]

    def open(self, page):
        if not self.app.url.rstrip("/").endswith("/apply"):
            self.app.url = self.app.url.rstrip("/") + "/apply"
        super().open(page)

    def fill_form(self, page):
        p = self.p
        self.upload(page, "input[name='resume']", self.resume_pdf)
        page.wait_for_timeout(2500)  # Lever parses the resume and may prefill
        self.fill(page, "input[name='name']", f"{p.first_name} {p.last_name}", "name")
        self.fill(page, "input[name='email']", p.email, "email")
        self.fill(page, "input[name='phone']", p.phone, "phone")
        self.fill(page, "input[name='location']", p.location, "location")
        self.fill(page, "input[name='org']", p.current_company, "org")
        self.fill(page, "input[name='urls[LinkedIn]']", p.linkedin, "linkedin")
        self.fill(page, "input[name='urls[GitHub]']", p.github, "github")
        self.fill(page, "input[name='urls[Portfolio]']", p.website, "portfolio")
        self.fill(page, "textarea[name='comments']", self.cl, "cover_letter")
