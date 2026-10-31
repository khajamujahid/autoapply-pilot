from .base import BaseSubmitter


class LeverSubmitter(BaseSubmitter):
    ats = "lever"
    submit_selectors = ["#btn-submit", "button[data-qa='btn-submit']", "button[type=submit]"]

    def open(self, page):
        u = (self.app.apply_url or self.app.url).rstrip("/")
        self.app.apply_url = u if u.endswith("/apply") else u + "/apply"
        super().open(page)

    def fill_form(self, page):
        d = self.d
        self.upload(page, "input[name='resume']", self.resume_pdf)
        page.wait_for_timeout(3000)  # Lever parses the resume and may prefill
        name = f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()
        for sel, val, lab in (("input[name='name']", name, "name"), ("input[name='email']", d.get("email"), "email"),
                              ("input[name='phone']", d.get("phone"), "phone"), ("input[name='location']", d.get("location"), "location"),
                              ("input[name='org']", d.get("current_company"), "org"),
                              ("input[name='urls[LinkedIn]']", d.get("linkedin"), "linkedin"),
                              ("input[name='urls[GitHub]']", d.get("github"), "github"),
                              ("input[name='urls[Portfolio]']", d.get("website"), "portfolio"),
                              ("textarea[name='comments']", self.ans.cl, "cover_letter")):
            self.fill(page, sel, val, lab)
        loc = page.locator("input[name='location']")
        if loc.count():  # location autocomplete dropdown
            page.wait_for_timeout(800)
            opt = page.locator(".dropdown-location, .location-dropdown li, [role=option]").first
            if opt.count() and opt.is_visible():
                opt.click()
