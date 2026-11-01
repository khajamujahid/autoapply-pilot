"""E2E for v1.2 documents: paste posting -> match view -> drafts -> chat refine -> undo/restore -> themed PDFs -> tracker views.
Run against a live server: python tests/e2e_documents.py http://localhost:8077 email password"""
import re, sys, httpx
base, email, pw = sys.argv[1], sys.argv[2], sys.argv[3]
c = httpx.Client(base_url=base, follow_redirects=True, timeout=120)
c.post("/login", data={"email": email, "password": pw})
JD = """Staff Accountant
Requirements:
- Bachelor's degree in Accounting or Finance
- 3+ years of experience with month-end close and account reconciliations
- Experience with NetSuite or another ERP
- Strong Excel skills (pivot tables, VLOOKUP)
- Knowledge of GAAP
- CPA certification preferred
- Experience with Workiva and SEC reporting
"""
r = c.post("/applications/paste", data={"title": "Staff Accountant", "company": "Acme Foods", "description": JD})
aid = int(re.search(r"/applications/(\d+)", str(r.url)).group(1)); print("✓ pasted posting -> application", aid)
m = c.get(f"/applications/{aid}?tab=match").text
cov = re.search(r"<b>(\d+) / (\d+)</b> requirements covered", m); print("✓ match view:", cov.group(0) if cov else "??")
print("  gaps:", m.count(">gap<"), "covered:", m.count(">covered<"))
d = c.get(f"/applications/{aid}?tab=docs").text
assert "Workiva" not in d.split("<h2>Job description")[0].split("Tailored resume")[1].split("Refine")[0], "invented a gap!"
print("✓ resume drafted, gap 'Workiva' not claimed")
for msg in ["make it shorter", "add Workiva", "highlight NetSuite"]:
    r = c.post(f"/applications/{aid}/chat", data={"kind": "resume", "message": msg}); print(f"  chat '{msg}':", re.findall(r'class="flash[^"]*">([^<]+)', r.text)[:1])
v = c.get(f"/applications/{aid}?tab=docs&kind=resume").text; print("✓ versions:", len(re.findall(r"<td>v\d+</td>", v)))
c.post(f"/applications/{aid}/version/resume/undo"); v2 = c.get(f"/applications/{aid}?tab=docs&kind=resume").text
print("✓ undo -> versions", len(re.findall(r"<td>v\d+</td>", v2)), "| top note:", re.findall(r"<td>v\d+</td><td>([^<]+)", v2)[0])
for th in ["classic", "modern", "minimal", "compact", "executive", "serif", "slate", "emerald", "centered", "mono"]:
    p = c.get(f"/applications/{aid}/pdf/resume?theme={th}"); assert p.content[:4] == b"%PDF", th
    open(f"/tmp/resume_{th}.pdf", "wb").write(p.content)
print("✓ 10 resume themes rendered to PDF"); assert c.get(f"/applications/{aid}/pdf/cover").content[:4] == b"%PDF"; print("✓ cover letter PDF")
e = c.get(f"/applications/{aid}?tab=docs&kind=email").text; print("✓ email draft:", "mailto:" in e)
for stg in ["APPLIED", "INTERVIEW"]:
    c.post(f"/applications/{aid}/action", data={"action": stg})
for view in ["board", "table", "funnel"]:
    t = c.get(f"/?view={view}").text; print(f"✓ tracker {view} view ok", "Interview" in t)
print("DONE", aid)
