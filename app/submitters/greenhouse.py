from .base import BaseSubmitter


class GreenhouseSubmitter(BaseSubmitter):
    ats = "greenhouse"

    def open(self, page):
        super().open(page)
        btn = page.locator("button:has-text('Apply'), a:has-text('Apply')")
        if not page.locator("#first_name").count() and btn.count():
            btn.first.click(); page.wait_for_timeout(1500)

    def fill_form(self, page):
        p = self.p
        self.fill(page, "#first_name", p.first_name, "first_name")
        self.fill(page, "#last_name", p.last_name, "last_name")
        self.fill(page, "#email", p.email, "email")
        self.fill(page, "#phone", p.phone, "phone")
        self.upload(page, "input[type=file]#resume, #resume input[type=file], input[type=file]", self.resume_pdf)
        # cover letter: prefer a "enter manually" textarea, else upload is skipped
        cl = page.locator("#cover_letter_text, textarea[name*='cover_letter']")
        if cl.count():
            cl.first.fill(self.cl); self.filled.append("cover_letter")
        else:
            m = page.locator("button:has-text('Enter manually')")
            if m.count():
                m.last.click(); page.wait_for_timeout(500)
                t = page.locator("textarea#cover_letter_text, textarea[id*='cover' i], textarea:visible:not([name*='recaptcha'])")
                if t.count():
                    t.first.fill(self.cl, timeout=5000); self.filled.append("cover_letter")
        self.fill(page, "input[id*='linkedin' i], input[aria-label*='LinkedIn' i]", p.linkedin, "linkedin")
