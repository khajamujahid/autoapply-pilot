from .base import BaseSubmitter


class AshbySubmitter(BaseSubmitter):
    ats = "ashby"
    submit_selectors = ["button:has-text('Submit Application')", "button[type=submit]"]

    def open(self, page):
        u = (self.app.apply_url or self.app.url).rstrip("/")
        self.app.apply_url = u if u.endswith("/application") else u + "/application"
        super().open(page); page.wait_for_timeout(2000)

    def fill_form(self, page):
        d = self.d
        self.fill(page, "input[name='_systemfield_name']", f"{d.get('first_name', '')} {d.get('last_name', '')}".strip(), "name")
        self.fill(page, "input[name='_systemfield_email']", d.get("email"), "email")
        self.fill(page, "input[type='tel']", d.get("phone"), "phone")
        self.upload(page, "input[type=file][id*='resume' i], input[type=file]", self.resume_pdf)
        page.wait_for_timeout(2500)
        # Ashby yes/no questions are rendered as button pairs
        for grp in page.locator("div:has(> button:text-is('Yes')):has(> button:text-is('No'))").all():
            try:
                q = grp.evaluate("e => (e.closest('[class*=fieldEntry],[class*=question]')||e.parentElement).innerText.split('\\n')[0]")
                a = self.ans.choose(q, ["Yes", "No"])
                if a:
                    grp.locator(f"button:text-is('{a}')").click(); self.filled.append(f"YesNo: {q[:40]} -> {a}")
            except Exception as e:
                self.log.append(f"ashby yes/no: {str(e)[:100]}")
