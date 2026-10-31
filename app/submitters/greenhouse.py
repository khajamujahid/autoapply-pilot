from .base import BaseSubmitter


class GreenhouseSubmitter(BaseSubmitter):
    ats = "greenhouse"
    submit_selectors = ["button:has-text('Submit application')", "button[type=submit]", "#submit_app"]

    def open(self, page):
        super().open(page)
        if not page.locator("#first_name").count():
            btn = page.locator("button:has-text('Apply'), a:has-text('Apply')")
            if btn.count():
                btn.first.click(); page.wait_for_timeout(1500)

    def fill_form(self, page):
        d = self.d
        self.fill(page, "#first_name", d.get("first_name"), "first_name")
        self.fill(page, "#last_name", d.get("last_name"), "last_name")
        self.fill(page, "#email", d.get("email"), "email")
        self.fill(page, "#phone", d.get("phone"), "phone")
        self.upload(page, "input[type=file]#resume, #resume input[type=file], input[type=file]", self.resume_pdf)
        page.wait_for_timeout(1000)
        cl = page.locator("#cover_letter_text, textarea[name*='cover_letter']")
        if not cl.count():
            m = page.locator("button:has-text('Enter manually')")
            if m.count():
                m.last.click(); page.wait_for_timeout(500)
        t = page.locator("#cover_letter_text, textarea[id*='cover' i], textarea[name*='cover' i]")
        if t.count() and self.ans.cl:
            t.first.fill(self.ans.cl, timeout=5000); self.filled.append("cover_letter")
