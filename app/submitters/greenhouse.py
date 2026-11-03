from .base import BaseSubmitter


class GreenhouseSubmitter(BaseSubmitter):
    ats = "greenhouse"
    submit_selectors = ["button:has-text('Submit application')", "button[type=submit]", "#submit_app"]

    def open(self, page):
        import re
        u = self.app.apply_url or self.app.url
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", u) or re.search(r"for=([\w-]+)&token=(\d+)", u)
        try:
            super().open(page)
        except Exception:
            if not m:
                raise
        if not page.locator("#first_name").count() and m:
            # Boards that redirect to a company careers site: the embeddable application form always works.
            page.goto(f"https://job-boards.greenhouse.io/embed/job_app?for={m.group(1)}&token={m.group(2)}",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
        if not page.locator("#first_name").count():
            btn = page.locator("button:visible:has-text('Apply'), a:visible:has-text('Apply')")
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
