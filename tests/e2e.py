"""End-to-end test: two users, BYO-AI via a mock OpenAI-compatible server, discovery, review, dry-run apply.
Requires the app on :8077 and tests/mock_ai_server.py on :8099. Never submits (dry_run stays on)."""
import sys
import time
import httpx

BASE = "http://localhost:8077"
JOBS = sys.argv[1:] or []


def client():
    return httpx.Client(base_url=BASE, follow_redirects=True, timeout=60)


def signup(c, email, name):
    r = c.post("/signup", data={"email": email, "password": "placeholder-pass-123", "name": name})
    if "/login" in str(r.url):
        c.post("/login", data={"email": email, "password": "placeholder-pass-123"})
    assert c.get("/").status_code == 200 and email in c.get("/").text, "login failed"


def wait(c, want, ids, timeout=600):
    t = time.time()
    while time.time() - t < timeout:
        rows = {i: status(c, i) for i in ids}
        if all(v in want + ["FAILED", "NEEDS_MANUAL"] for v in rows.values()):
            return rows
        time.sleep(4)
    return rows


def status(c, i):
    import re
    h = c.get(f"/applications/{i}").text
    return re.search(r'pill s-(\w+)" style', h).group(1)


def app_ids(c):
    import re
    return sorted(set(int(x) for x in re.findall(r"/applications/(\d+)\"", c.get("/").text)))


A, B = client(), client()
signup(A, "alex.placeholder@example.com", "Alex Placeholder")
signup(B, "bailey.placeholder@example.com", "Bailey Placeholder")
print("✓ two users signed up")

# --- BYO-AI (custom OpenAI-compatible -> mock server) for user A
r = A.post("/api/settings/test-ai", json={"ai_provider": "custom", "ai_api_key": "test-key", "ai_model": "mock-1",
                                          "ai_base_url": "http://localhost:8099/v1"}).json()
print("✓ test connection:", r); assert r["ok"]
bad = A.post("/api/settings/test-ai", json={"ai_provider": "openai", "ai_api_key": "sk-invalid"}).json()
print("✓ invalid key reports error:", bad["message"][:80]); assert not bad["ok"]
A.post("/settings", data={"ai_provider": "custom", "ai_api_key": "test-key", "ai_model": "mock-1",
                          "ai_base_url": "http://localhost:8099/v1", "dry_run": "true", "require_review": "true",
                          "daily_limit": "10", "auto_approve_above": "0", "min_match_score": "0"})
A.post("/profile/resume", files={"file": ("resume.txt", open("sample/placeholder_resume.txt", "rb"), "text/plain")}, data={"parse": "true"})
prof = A.get("/profile").text
assert "[mock-ai] Backend engineer." in prof, "AI parse did not populate profile"
print("✓ resume parsed by BYO-AI into profile")

# --- user B: mock provider, DOCX resume, no review (auto)
import docx, tempfile
d = docx.Document()
for line in open("sample/placeholder_resume.txt").read().replace("Alex", "Bailey").replace("alex.", "bailey.").splitlines():
    d.add_paragraph(line)
tmp = tempfile.mktemp(suffix=".docx"); d.save(tmp)
B.post("/profile/resume", files={"file": ("resume.docx", open(tmp, "rb"))}, data={"parse": "true"})
assert "bailey.placeholder@example.com" in B.get("/profile").text
B.post("/settings", data={"ai_provider": "mock", "dry_run": "true", "auto_approve_above": "1", "require_review": "true", "daily_limit": "5"})
print("✓ user B DOCX resume parsed (mock provider), auto-approve ≥1")

# --- discovery for B
B.post("/jobs/search", data={"keywords": "engineer", "companies": "greenhouse:gitlab\nlever:palantir\nashby:ramp",
                             "sources": ["boards", "remotive"], "save_name": "Eng test", "every_hours": "24"})
jp = B.get("/jobs").text
import re
found = re.findall(r'name="ids" value="(\d+)"', jp)
print(f"✓ discovery found {len(found)} jobs for B; saved search stored:", "Eng test" in jp)
B.post("/jobs/search", data={"keywords": "engineer", "companies": "greenhouse:gitlab", "sources": ["boards"]})
found2 = re.findall(r'name="ids" value="(\d+)"', B.get("/jobs").text)
print(f"✓ dedupe: rerun keeps {len(found2)} (no duplicates: {len(found2) == len(set(found2))})")

# --- queue real jobs
A.post("/applications/add", data={"urls": "\n".join(JOBS)})
ids_a = app_ids(A)
print("A queued:", ids_a)
# isolation
assert B.get(f"/applications/{ids_a[0]}").status_code == 404
assert str(ids_a[0]) not in B.get("/export.csv").text.split("\n")[1:2][0] if B.get("/export.csv").text.count("\n") > 1 else True
print("✓ isolation: B cannot open A's application (404)")

# B: queue the best greenhouse job from discovery -> auto-approved -> dry run
gh = [i for i in found if "greenhouse" in B.get(f"/applications/{i}").text][:1]
B.post("/applications/bulk", data={"action": "queue", "ids": gh})

rows = wait(A, ["REVIEW"], ids_a)
print("A after tailoring:", rows)
for i in ids_a:
    A.post(f"/applications/{i}/action", data={"action": "approve"})
rows = wait(A, ["DRY_RUN_DONE"], ids_a)
print("A after dry-run:", rows)
rows_b = wait(B, ["DRY_RUN_DONE"], [int(x) for x in gh])
print("B after auto-approve dry-run:", rows_b)
csv = A.get("/export.csv").text
print("✓ CSV export lines:", csv.count("\n"))
print("mock AI calls:", httpx.get("http://localhost:8099/calls").json()[:12])
