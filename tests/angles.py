"""Release gate: unit tests + regression + five quality angles. Exit code 0 only if everything passes.
Needs the app running on :8077 (uvicorn app.main:app --port 8077) and internet for live discovery.
Usage: python tests/angles.py [--pass-name first]"""
import json, os, re, subprocess, sys, time, pathlib
sys.path.insert(0, ".")
ROOT = pathlib.Path(".").resolve(); PY = sys.executable; BASE = "http://localhost:8077"
R = {}


def run(cmd, timeout=900):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr)


def check(angle, name, ok, ev):
    R.setdefault(angle, []).append((name, bool(ok), str(ev)[:300]))
    print(f"[{'PASS' if ok else 'FAIL'}] {angle} · {name}: {str(ev)[:200]}", flush=True)


def pytest_k(expr):
    code, out = run([PY, "-m", "pytest", "-k", expr])
    m = re.search(r"(\d+) passed", out); f = re.search(r"(\d+) failed", out)
    return code == 0, f"{m.group(1) if m else 0} passed, {f.group(1) if f else 0} failed"


# ---------- unit
code, out = run([PY, "-m", "pytest", "--cov=app", "--cov-report=term"])
core = [l for l in out.splitlines() if re.match(r"app/(ai|answerer|discovery|profile|auth|crypto|roles|matcher|docs_engine|pdf|hardening|db)\.py", l)]
st = sum(int(l.split()[1]) for l in core); miss = sum(int(l.split()[2]) for l in core)
cov = 100 * (st - miss) / st
m = re.search(r"(\d+) passed", out)
check("UNIT", "pytest green + core coverage >= 80%", code == 0 and cov >= 80, f"{m.group(1) if m else 0} passed, core coverage {cov:.1f}%")

# ---------- A security
ok, ev = pytest_k("password or crypto or throttle or cookie or cross_site or traversal or isolation or encrypted or health")
check("A", "auth/crypto/CSRF/cookie/traversal/isolation tests", ok, ev)
code, out = run(["git", "ls-files"]) if (ROOT / ".git").exists() else (0, "")
files = [p for p in ROOT.rglob("*") if p.is_file() and not any(x in p.parts for x in (".venv", "data", "__pycache__", ".pytest_cache"))
         and p.suffix not in (".png", ".svg", ".pdf", ".zip")]
secret_rx = re.compile(r"(sk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN (RSA |EC )?PRIVATE KEY)")
hits = [str(p.relative_to(ROOT)) for p in files if secret_rx.search(p.read_text(errors="ignore"))]
check("A", "no secrets in source tree", not hits, hits or f"scanned {len(files)} files")
code, out = run([PY, "-m", "pip_audit", "-r", "requirements.txt"], 300)
check("A", "dependency audit (pip-audit)", code == 0 and "No known vulnerabilities" in out, out.strip().splitlines()[-1] if out.strip() else out)

# ---------- B reliability
ok, ev = pytest_k("invalid_resume or empty_jd or missing_ai_key or 429 or dedupe or migration or ai_errors or without_keys")
check("B", "invalid resume / empty JD / no key / 429 / duplicates / migration", ok, ev)

# ---------- C UX (unit happy path + live server crawl)
ok, ev = pytest_k("happy_path or auth_required")
check("C", "signup→resume→questions→discover→match→docs→tracker (TestClient)", ok, ev)
code, out = run([PY, "tests/e2e_documents.py", BASE, "--new"], 300)
check("C", "live e2e on :8077 (new user, docs, chat, undo, 10 PDFs, tracker)", code == 0 and "DONE" in out,
      " | ".join(l for l in out.splitlines() if l.startswith("✓"))[:280] if code == 0 else out[-300:])

# ---------- D performance + E cross-role (live discovery)
from app.db import SessionLocal, User
from sqlalchemy import select
PROFILES = {"software": ("Software Engineer", "Python, Go, TypeScript, React, PostgreSQL, AWS, Docker, Kubernetes", "New York, NY", r"engineer|developer"),
            "network": ("Network Engineer", "Cisco, BGP, OSPF, Palo Alto, Fortinet, SD-WAN, Meraki, VPN, VLAN, Python", "Dallas, TX", r"network"),
            "accountant": ("Staff Accountant", "GAAP, Month-end close, Reconciliations, Journal entries, Accounts payable, NetSuite, Excel", "Chicago, IL", r"account")}
from app.auth import create_user
from app.db import Profile
live = {}
for role, (title, skills, loc, rx) in PROFILES.items():
    email = f"gate-{role}@example.com"
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email))
    u = u or create_user(email, "Password123!", "Gate Tester")
    with SessionLocal() as s:
        p = s.get(Profile, u.id); p.data = {**(p.data or {}), "current_title": title, "skills": skills, "years_experience": "5"}
        res = {"software": "", "network": open("sample/resumes/network_engineer.txt").read(),
               "accountant": open("sample/resumes/accountant.txt").read()}[role]
        p.resume_text = res or f"{title} with 5 years. Skills: {skills}"; s.commit()
    code, out = run([PY, "tests/discovery_any_profession.py", str(u.id), loc, "7"], 300)
    try:
        d = json.JSONDecoder().raw_decode(out[out.index('{\n "user"'):])[0]
    except Exception:
        d = {"total": 0, "secs": 999, "top": [], "raw": {}, "kept": {}, "errors": [out[-200:]]}
    live[role] = d
    rel = [t for t in d["top"] if re.search(rx, t["title"], re.I)]
    check("D", f"discover {role} @ {loc} < 60s", d["secs"] < 60, f"{d['secs']}s, raw={d['raw']}")
    check("E", f"{role}: relevant matches", d["total"] >= 5 and len(rel) >= min(5, len(d["top"])) and d["top"],
          f"{d['total']} matches; top: " + "; ".join(f"{t['title']} @ {t['company']}" for t in d["top"][:3]))
ok, ev = pytest_k("pdf_all_themes or performance_bound")
check("D", "PDF render < 5s per theme + parallel discovery bound", ok, ev)
ok, ev = pytest_k("roles_any_profession or requirements_and_coverage or builtin_docs")
check("E", "role extraction + requirement coverage for network/accountant/nurse/software", ok, ev)

# ---------- regression: dry-run Greenhouse fill (never submits)
import httpx
from app.db import Settings, Application
from app.worker import prepare, apply
gh = httpx.get("https://boards-api.greenhouse.io/v1/boards/gitlab/jobs", timeout=30).json()["jobs"][0]
email = "gate-dryrun@example.com"
with SessionLocal() as s:
    u = s.scalar(select(User).where(User.email == email))
u = u or create_user(email, "Password123!", "Alex Placeholder")
with SessionLocal() as s:
    p = s.get(Profile, u.id); txt = open("sample/placeholder_resume.txt").read()
    p.resume_text = txt; p.data = {**p.data, "phone": "555-0100", "skills": "Python, Go, AWS", "years_experience": "6"}
    rp = pathlib.Path("data/uploads") / f"u{u.id}"; rp.mkdir(parents=True, exist_ok=True); (rp / "resume.txt").write_text(txt)
    p.resume_path = str(rp / "resume.txt")
    for old in s.scalars(select(Application).where(Application.user_id == u.id)):
        s.delete(old)
    s.commit()
    st = s.get(Settings, u.id); st.dry_run = True; st.require_review = False; st.ai_provider = "mock"
    a = Application(user_id=u.id, url=gh["absolute_url"], apply_url=f"https://job-boards.greenhouse.io/gitlab/jobs/{gh['id']}",
                    ats="greenhouse", status="QUEUED"); s.add(a); s.commit()
    assert st.dry_run is True
    prepare(a, s); s.refresh(a)
    if a.status == "APPROVED":
        apply(a, s); s.refresh(a)
    filled = next((l for l in a.log if "Filled:" in l), "")
    check("REG", "dry-run Greenhouse fill (no submit)", a.status == "DRY_RUN_DONE" and "resume" in filled and not a.applied_at,
          f"{a.status}; {filled[:160]}")
    a.status = "SKIPPED"; s.commit()

# ---------- regression: no personal data in repo files
pii = re.compile(r"khaja|mujahid|shoaib|new haven|meriden", re.I)
bad = [str(p.relative_to(ROOT)) for p in files if p.name not in ("LICENSE", "angles.py") and pii.search(p.read_text(errors="ignore").replace("github.com/khajamujahid/autoapply-pilot", ""))
       and ".git" not in p.parts]
check("REG", "no personal data in repo (LICENSE copyright excepted)", not bad, bad or "clean")

summary = {k: all(ok for _, ok, _ in v) for k, v in R.items()}
print("SUMMARY", json.dumps(summary)); print("LIVE", json.dumps({k: {"total": v["total"], "secs": v["secs"], "raw": v["raw"], "kept": v["kept"],
      "top": v["top"][:3]} for k, v in live.items()}, default=str))
json.dump({"results": R, "summary": summary, "live": live}, open(f"/tmp/angles_{sys.argv[-1] if len(sys.argv) > 1 else 'run'}.json", "w"), default=str)
sys.exit(0 if all(summary.values()) else 1)
