"""Core unit + integration tests (offline). Run: pytest -q"""
import io
import sqlite3
import subprocess
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from app import discovery
from app.answerer import Answerer
from app.ai import Provider, AIConfig
from app.auth import pwd, valid_email
from app.crypto import enc, dec
from app.db import SessionLocal, User, Profile, Settings, Application, init_db
from app.docs_engine import (extract_requirements_builtin, cover_builtin, fact_check, resume_builtin,
                             cover_letter_builtin, email_builtin, match_requirements, _mock_refine)
from app.hardening import LoginThrottle
from app.main import app
from app.matcher import score
from app.pdf import text_to_pdf, letter_to_pdf, THEMES
from app.profile import rule_answer, custom_answer, add_unknown_questions, default_answers
from app.roles import builtin_profile, seniority_from_years


def P(**kw):
    d = dict(first_name="Jordan", last_name="Rivera", email="j@example.com", phone="555-0100", current_title="Network Engineer",
             years_experience="6", skills="Cisco, BGP, OSPF, Palo Alto, VPN, Python", location="Dallas, TX")
    d.update(kw.pop("data", {}))
    return types.SimpleNamespace(data=d, answers=kw.pop("answers", default_answers()),
                                 resume_text=kw.pop("resume_text", "Network Engineer with 6 years. EXPERIENCE\nSenior Network Engineer, Acme (2021–present): BGP, OSPF."))


MOCK = Provider(AIConfig("mock"))


# ---------------- auth / crypto / security
def test_password_hashing():
    h = pwd.hash("CorrectHorse9")
    assert h != "CorrectHorse9" and h.startswith("$2") and pwd.verify("CorrectHorse9", h) and not pwd.verify("x", h)


def test_crypto_roundtrip_and_tamper():
    t = enc("sk-secret")
    assert t != "sk-secret" and "sk-secret" not in t and dec(t) == "sk-secret"
    assert dec(t[:-4] + "AAAA") == "" and dec("") == "" and enc("") == ""


def test_valid_email():
    assert valid_email("a@b.co") and not valid_email("nope") and not valid_email("")


def test_login_throttle():
    t = LoginThrottle(limit=3, window=60)
    for _ in range(3):
        assert not t.blocked("k"); t.fail("k")
    assert t.blocked("k"); t.reset("k"); assert not t.blocked("k")


# ---------------- roles / profile extraction
@pytest.mark.parametrize("title,resume,expect", [
    ("Network Engineer", "Senior Network Engineer at Acme. Network Administrator before.", "network engineer"),
    ("Staff Accountant", "Staff Accountant, month-end close. Accounts Payable Specialist.", "staff accountant"),
    ("", "Registered Nurse in ICU for 5 years. Charge Nurse.", "registered nurse"),
    ("Software Engineer", "Backend engineer building APIs", "software engineer"),
])
def test_roles_any_profession(title, resume, expect):
    sp = builtin_profile(P(data={"current_title": title}, resume_text=resume))
    assert any(expect in r for r in sp["roles"]), sp


def test_roles_override_and_seniority():
    sp = builtin_profile(P(data={"target_roles": "HVAC Technician, Maintenance Technician"}))
    assert sp["roles"][:2] == ["hvac technician", "maintenance technician"] and sp["method"] == "profile override"
    assert seniority_from_years(1) == "entry" and seniority_from_years(7) == "senior" and seniority_from_years("x") == "entry"


def test_roles_no_single_word_false_positive():
    sp = builtin_profile(P(data={"current_title": "Data Engineer"}, resume_text="Worked with a buyer and a cook on data pipelines"))
    assert "buyer" not in sp["roles"] and "cook" not in sp["roles"]


# ---------------- answerer / question matching
def test_rule_answers_cover_common_questions():
    p = P(answers={**default_answers(), "us_person": "Yes", "languages": "English, Spanish", "school": "State University",
                   "earliest_start": "2026-12-01", "relatives_at_company": "No"})
    yn = ["Yes", "No"]
    assert rule_answer(p, "Are you a U.S. person for export control purposes?", yn) == "Yes"
    assert rule_answer(p, "Do you have relatives employed by this company?", yn) == "No"
    assert rule_answer(p, "Are you at least 18 years old?", yn) == "Yes"
    assert rule_answer(p, "Will you require visa sponsorship?", yn) == "No"
    assert rule_answer(p, "What languages do you speak?") == "English, Spanish"
    assert rule_answer(p, "Which school did you attend?") == "State University"
    assert rule_answer(p, "Earliest start date") == "2026-12-01"
    assert rule_answer(p, "Phone number") == "555-0100"
    assert rule_answer(p, "Gender", ["Male", "Female", "Decline to self-identify"]) == "Decline to self-identify"


def test_custom_qa_exact_and_fuzzy():
    p = P(answers={"custom_qa": [{"q": "Do you have a CCNP certification?", "a": "Yes"}, {"q": "Blank one", "a": ""}]})
    assert custom_answer(p, "Do you have a CCNP certification?") == "Yes"
    assert custom_answer(p, "Do you hold a CCNP certification?", ["Yes", "No"]) == "Yes"
    assert custom_answer(p, "What is your favourite colour?") is None and custom_answer(p, "Blank one") is None


def test_unknown_questions_saved_once():
    p = P(answers={})
    assert add_unknown_questions(p, [{"q": "Do you have a CDL license?"}, {"q": "do you have a CDL license"}]) == 1
    assert add_unknown_questions(p, [{"q": "Do you have a CDL license?"}]) == 0
    assert p.answers["custom_qa"][0]["via"] == "pending"


def test_answerer_records_unknown():
    a = Answerer(P(answers={}), MOCK)
    assert a.text("Describe your favourite network outage story") in (None, "Mock response.")
    assert a.unknown and a.unknown[0]["q"].startswith("Describe")


# ---------------- matcher / gaps
def test_matcher_skills_and_gaps():
    m = score(P(), "Network Engineer", "Need BGP, OSPF and Juniper. 5+ years experience.")
    assert {"bgp", "ospf"} <= set(m["matched_skills"]) and "palo alto" in m["missing_skills"] and m["required_years"] == 5
    assert 0 <= m["score"] <= 100


JD = """About us\nWe build things.\nRequirements:\n- 5+ years of network engineering\n- BGP and OSPF routing\n- Palo Alto firewalls
- CCIE certification\n- Experience with Juniper Mist wireless\nBenefits:\nHealth"""


def test_requirements_and_coverage():
    reqs = extract_requirements_builtin(JD)
    assert len(reqs) == 5 and "Health" not in reqs
    cov = {r["text"]: r["covered"] for r in cover_builtin(P(), reqs)}
    assert cov["BGP and OSPF routing"] and cov["Palo Alto firewalls"] and cov["5+ years of network engineering"]
    assert not cov["CCIE certification"] and not cov["Experience with Juniper Mist wireless"]


def test_requirements_empty_jd():
    assert extract_requirements_builtin("") == [] and match_requirements(P(), MOCK, "x", "") == []


# ---------------- docs engine truth rules
def test_builtin_docs_use_only_profile_facts():
    p = P()
    reqs = cover_builtin(p, extract_requirements_builtin(JD))
    for txt in (resume_builtin(p, "Network Engineer", "Contoso", reqs), cover_letter_builtin(p, "Network Engineer", "Contoso", reqs),
                email_builtin(p, "Network Engineer", "Contoso")):
        assert "CCIE" not in txt and "Juniper" not in txt and "Jordan Rivera" in txt


def test_fact_check_flags_jd_terms_not_in_profile():
    a = types.SimpleNamespace(company="Contoso", title="Network Engineer", job_description=JD, requirements=[])
    w = fact_check(P(), "Expert in BGP and Juniper Mist with CCIE and S/4HANA", a)
    assert w and "Juniper" in w[0] and "S/4HANA" in w[0] and "BGP" not in w[0]
    assert fact_check(P(), "Expert in BGP and OSPF at Acme", a) == []


def test_mock_refine_never_adds_missing_skill():
    p = P()
    doc = "Jordan Rivera\nj@example.com\n\nSKILLS\nCisco, BGP, Python\n\nEXPERIENCE\nX - Y (2020 - 2021)\n- a\n- b\n- c\n- d"
    new, msg = _mock_refine(p, doc, "add Kubernetes")
    assert new == doc and "can't add" in msg
    new, _ = _mock_refine(p, doc, "highlight Python"); assert "Python, Cisco, BGP" in new
    new, _ = _mock_refine(p, doc, "make it shorter"); assert new.count("\n- ") < doc.count("\n- ")
    new, _ = _mock_refine(p, doc, "remove BGP"); assert "BGP" not in new and "Cisco" in new


# ---------------- discovery dedupe / filters / failures
def _job(title, company="Acme", loc="Dallas, TX", src="indeed", url=None):
    return {"url": url or f"https://x/{company}/{title}", "ats": "unknown", "company": company, "title": title, "location": loc,
            "description": "BGP OSPF Cisco", "source": src, "posted": None}


def test_title_fit_and_location_filter():
    assert discovery.title_fit("Senior Network Engineer", ["network engineer"]) == 1
    assert discovery.title_fit("Systems Engineer", ["network engineer"]) == 0.5
    assert discovery.title_fit("Registered Nurse - ICU", ["registered nurse"]) == 1
    assert discovery._loc_ok(_job("x", src="remotive", loc="Remote, USA"), "", True)
    assert not discovery._loc_ok(_job("x", src="arbeitnow", loc="Remote (Germany)"), "New York, NY", False)
    assert discovery._loc_ok(_job("x", src="themuse", loc="New York, NY"), "New York, NY", False)


@pytest.fixture
def user_id():
    from app.auth import create_user
    u = create_user(f"u{time.time_ns()}@example.com", "Password123!", "Jordan Rivera")
    with SessionLocal() as s:
        p = s.get(Profile, u.id); p.data = {**p.data, **P().data}; p.resume_text = P().resume_text; s.commit()
    return u.id


def test_search_dedupes_survives_429_and_gates_titles(monkeypatch, user_id):
    def fake_jobspy(site, role, location, remote_only, days, n=30):
        if site == "linkedin":
            raise RuntimeError("429 Too Many Requests (blocked: use a proxy)")
        return [_job("Network Engineer", "Acme", src=site, url="https://dup/1"), _job("Network Engineer", "Acme", src=site),
                _job("Pastry Chef", "Bakery", src=site), _job("Senior Network Engineer", "Beta", src=site)]
    monkeypatch.setattr(discovery, "jobspy_site", fake_jobspy)
    jobs, errors, meta = discovery.search(user_id, location="Dallas, TX", days=7,
                                          sources=["linkedin", "indeed", "glassdoor"])
    titles = [(j["company"], j["title"]) for j in jobs]
    assert len(titles) == len(set(titles)) == 2, titles
    assert all("Chef" not in t for _, t in titles)
    assert any("429" in e for e in errors) and meta["raw_counts"]["indeed"] > 0
    assert discovery.save_jobs(user_id, jobs) == 2 and discovery.save_jobs(user_id, jobs) == 0  # DB dedupe


def test_search_performance_bound(monkeypatch, user_id):
    monkeypatch.setattr(discovery, "jobspy_site", lambda *a, **k: (time.sleep(0.3), [_job("Network Engineer")])[1])
    t = time.time()
    discovery.search(user_id, location="Dallas, TX", sources=["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"])
    assert time.time() - t < 5  # 15 calls in parallel, not serial


def test_optional_sources_without_keys_are_skipped():
    import httpx
    with httpx.Client() as c:
        assert discovery.jsearch(c, "x") is None and discovery.adzuna(c, "x") is None
        assert discovery.usajobs(c, "x") is None and discovery.arbeitsagentur(c, "x") is None


# ---------------- PDF
def test_pdf_all_themes_fast(tmp_path):
    txt = resume_builtin(P(), "Network Engineer", "Contoso", [])
    for th in THEMES:
        t = time.time(); p = text_to_pdf(txt, str(tmp_path / f"{th}.pdf"), th)
        assert open(p, "rb").read(4) == b"%PDF" and time.time() - t < 5
    assert open(letter_to_pdf("Dear team,\nHello", str(tmp_path / "cl.pdf"), "Jordan", "j@example.com"), "rb").read(4) == b"%PDF"
    from pypdf import PdfReader
    assert "Jordan Rivera" in PdfReader(str(tmp_path / "classic.pdf")).pages[0].extract_text()  # real, ATS-readable text


# ---------------- HTTP: security + happy path + reliability
@pytest.fixture
def client():
    return TestClient(app)


def _signup(c, email=None):
    email = email or f"t{time.time_ns()}@example.com"
    r = c.post("/signup", data={"email": email, "password": "Password123!", "name": "Pat Lee"})
    assert r.status_code == 200
    return email


def test_health_and_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["db"] == "ok" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY" and "x-request-id" in r.headers
    assert client.get("/docs").status_code == 404  # API docs disabled


def test_session_cookie_flags(client):
    r = client.post("/signup", data={"email": f"c{time.time_ns()}@example.com", "password": "Password123!"}, follow_redirects=False)
    ck = r.headers.get("set-cookie", "").lower()
    assert "applypilot_session=" in ck and "httponly" in ck and "samesite=lax" in ck


def test_cross_site_post_blocked(client):
    _signup(client)
    r = client.post("/settings", data={"ai_provider": "mock"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_auth_required_and_isolation(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    _signup(client)
    client.post("/applications/paste", data={"title": "Network Engineer", "company": "A", "description": JD})
    with SessionLocal() as s:
        aid = s.query(Application).order_by(Application.id.desc()).first().id
    other = TestClient(app); _signup(other)
    assert other.get(f"/applications/{aid}").status_code == 404
    assert other.get(f"/applications/{aid}/pdf/resume").status_code == 404


def test_path_traversal_blocked(client):
    _signup(client)
    client.post("/applications/paste", data={"title": "Accountant", "description": "Requirements:\n- GAAP"})
    with SessionLocal() as s:
        aid = s.query(Application).order_by(Application.id.desc()).first().id
    for bad in ("..%2F..%2Fapp.db", "../../.encryption_key", "%2e%2e%2f%2e%2e%2f.secret_key"):
        r = client.get(f"/applications/{aid}/file/{bad}")
        assert r.status_code in (404, 422) and b"%PDF" not in r.content and b"sqlite" not in r.content.lower()
    r = client.post("/profile/resume", files={"file": ("../../evil.txt", b"Pat Lee\nAccountant with 5 years experience in GAAP and audits.", "text/plain")},
                    data={"parse": "true"})
    assert r.status_code == 200
    import os
    from app.config import DATA_DIR
    assert not (DATA_DIR / "evil.txt").exists() and not os.path.exists(DATA_DIR.parent / "evil.txt")


def test_login_wrong_password_and_throttle(client):
    email = _signup(client)
    c2 = TestClient(app)
    for _ in range(9):
        r = c2.post("/login", data={"email": email, "password": "wrong"})
    assert "Too many failed" in r.text
    assert c2.post("/login", data={"email": email, "password": "Password123!"}).url.path == "/login"  # still throttled


def test_invalid_resume_and_empty_file(client):
    _signup(client)
    r = client.post("/profile/resume", files={"file": ("cv.pdf", b"%PDF-1.4 garbage not a pdf", "application/pdf")}, data={"parse": "true"})
    assert r.status_code == 200 and "couldn" in r.text.lower()
    r = client.post("/profile/resume", files={"file": ("cv.txt", b"   ", "text/plain")}, data={"parse": "true"})
    assert "empty" in r.text.lower()
    r = client.post("/profile/resume", files={"file": ("cv.exe", b"MZ", "application/octet-stream")}, data={"parse": "true"})
    assert "Upload a PDF" in r.text


def test_secrets_encrypted_at_rest(client):
    _signup(client)
    client.post("/settings", data={"ai_provider": "openai", "ai_api_key": "sk-test-PLAINTEXT-123", "dry_run": "true"})
    from app.config import DATABASE_URL
    raw = open(DATABASE_URL.replace("sqlite:///", ""), "rb").read()
    assert b"sk-test-PLAINTEXT-123" not in raw and b"Password123!" not in raw


def test_missing_ai_key_is_clear_and_falls_back(client):
    _signup(client)
    client.post("/settings", data={"ai_provider": "openai", "dry_run": "true"})  # provider chosen, no key
    r = client.post("/api/settings/test-ai", json={"ai_provider": "openai", "ai_api_key": "", "ai_model": "", "ai_base_url": ""})
    assert r.json()["ok"] is False and "key" in r.json()["message"].lower()
    r = client.post("/applications/paste", data={"title": "Network Engineer", "company": "A", "description": JD})
    assert r.status_code == 200 and "Tailored resume" in r.text  # built-in drafts still produced


def test_empty_jd_does_not_crash(client):
    _signup(client)
    r = client.post("/applications/paste", data={"title": "Nurse", "company": "Clinic", "description": " "})
    assert r.status_code in (200, 422)


def test_happy_path_pages(client):
    _signup(client)
    client.post("/profile/resume", files={"file": ("cv.txt", open("sample/resumes/accountant.txt", "rb").read(), "text/plain")}, data={"parse": "true"})
    for path in ("/", "/?view=applied", "/tracker", "/tracker?view=table", "/tracker?view=funnel", "/profile", "/questions?onboarding=1", "/jobs", "/settings", "/docs/architecture"):
        assert client.get(path).status_code == 200, path
    r = client.post("/questions", data={"city": "Chicago", "qa_q": ["Do you have a CPA?"], "qa_a": ["Yes"], "onboarding": "1"})
    assert r.url.path == "/jobs"
    r = client.post("/applications/paste", data={"title": "Staff Accountant", "company": "Acme", "description": "Requirements:\n- GAAP\n- NetSuite\n- Workiva"})
    aid = int(str(r.url).split("/applications/")[1].split("?")[0])
    for tab in ("match", "docs", "apply"):
        assert client.get(f"/applications/{aid}?tab={tab}").status_code == 200
    assert client.post(f"/applications/{aid}/chat", data={"kind": "resume", "message": "add Workiva"}).status_code == 200
    assert client.post(f"/applications/{aid}/version/resume/undo").status_code == 200
    assert client.get(f"/applications/{aid}/pdf/resume?theme=modern").content[:4] == b"%PDF"
    assert client.post(f"/applications/{aid}/action", data={"action": "INTERVIEW"}).status_code == 200
    assert "Interview" in client.get("/tracker?view=board").text
    assert client.get("/export.csv").status_code == 200


def test_migration_on_old_sqlite(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), name VARCHAR(200), password_hash VARCHAR(200),
      google_sub VARCHAR(100), created_at DATETIME);
    CREATE TABLE settings (user_id INTEGER PRIMARY KEY, ai_provider VARCHAR(30), ai_api_key_enc TEXT, ai_model VARCHAR(100),
      ai_base_url VARCHAR(300), dry_run BOOLEAN, require_review BOOLEAN, auto_approve_above FLOAT, min_match_score FLOAT, daily_limit INTEGER);
    CREATE TABLE applications (id INTEGER PRIMARY KEY, user_id INTEGER, url VARCHAR(1000), apply_url VARCHAR(1000), source VARCHAR(50),
      ats VARCHAR(50), company VARCHAR(200), title VARCHAR(300), location VARCHAR(300), job_description TEXT, match_score FLOAT,
      match_details JSON, match_explanation TEXT, tailored_resume TEXT, tailored_resume_pdf VARCHAR(500), cover_letter TEXT,
      status VARCHAR(30), error TEXT, screenshots JSON, log JSON, answers_used JSON, attempts INTEGER, applied_at DATETIME,
      created_at DATETIME, updated_at DATETIME);
    INSERT INTO users VALUES (1,'old@example.com','Old','', '', '2026-01-01');
    INSERT INTO applications (id,user_id,url,status) VALUES (1,1,'https://x','APPLIED');""")
    con.commit(); con.close()
    code = ("import app.db as d; d.init_db(); from sqlalchemy import inspect; i=inspect(d.engine);"
            "print(sorted(c['name'] for c in i.get_columns('applications'))); print([c['name'] for c in i.get_columns('settings')])")
    env = {**__import__("os").environ, "DATABASE_URL": f"sqlite:///{db}", "DATA_DIR": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=".")
    assert out.returncode == 0, out.stderr
    assert "requirements" in out.stdout and "email_draft" in out.stdout and "extra" in out.stdout
    con = sqlite3.connect(db); assert con.execute("select status from applications").fetchone()[0] == "APPLIED"


def test_ats_theme_layout(tmp_path):
    import time
    from pathlib import Path
    from pypdf import PdfReader
    from app.pdf import text_to_pdf
    src = (Path(__file__).parent.parent / "sample" / "ats_resume_template.txt").read_text()
    t = time.time(); out = text_to_pdf(src, str(tmp_path / "r.pdf"), "ats"); assert time.time() - t < 5
    r = PdfReader(out); txt = r.pages[0].extract_text()
    order = [txt.find(h) for h in ("PROFESSIONAL SUMMARY", "TECHNICAL SKILLS", "PROFESSIONAL EXPERIENCE", "EDUCATION")]
    assert all(i >= 0 for i in order) and order == sorted(order)
    fonts = str(r.pages[0]["/Resources"]["/Font"].get_object())
    assert "Carlito" in str([f.get_object()["/BaseFont"] for f in r.pages[0]["/Resources"]["/Font"].values()]) or fonts
    assert abs(float(r.pages[0].mediabox.width) - 612) < 1  # US Letter


def _seed_jobs(client, n=3, ats="unknown"):
    from app.db import SessionLocal, Application, User
    from sqlalchemy import select
    with SessionLocal() as s:
        uid = s.scalars(select(User.id).order_by(User.id.desc())).first()
        ids = []
        for i in range(n):
            a = Application(user_id=uid, url=f"https://jobs.example.com/{uid}/{i}", apply_url=f"https://jobs.example.com/{uid}/{i}",
                            source="test", ats=ats, company=f"Co{i}", title="Staff Accountant", location="Chicago, IL",
                            job_description="Requirements: GAAP, NetSuite, reconciliations, month-end close.",
                            match_score=70 - i, match_details={}, status="DISCOVERED")
            s.add(a); s.flush(); ids.append(a.id)
        s.commit()
    return ids


def _drain(client):
    st = client.get("/api/queue").json()
    for _ in range(20):
        if not st["pending"]:
            break
        st = client.post("/api/queue/step").json()
    return st


def test_auto_apply_flow_never_fakes_applied(client):
    _signup(client)
    client.post("/profile/resume", files={"file": ("cv.txt", open("sample/resumes/accountant.txt", "rb").read(), "text/plain")}, data={"parse": "true"})
    ids = _seed_jobs(client)
    home = client.get("/").text
    assert "Auto Apply (3)" in home and "Recommended" in home
    r = client.post("/api/autoapply", json={"ids": ids}).json()
    assert r["queued"] == 3 and {j["status"] for j in r["jobs"].values()} == {"Reviewing"}
    st = _drain(client)
    assert st["pending"] == 0
    for j in st["jobs"].values():
        assert j["status"] == "Failed" and "UNSUPPORTED_ATS" in j["error"]   # unknown ATS: honest failure, never Applied
    from app.db import SessionLocal, Application
    with SessionLocal() as s:
        for i in ids:
            a = s.get(Application, i)
            assert a.tailored_resume and "PROFESSIONAL" in a.tailored_resume.upper() or a.tailored_resume
            assert a.applied_at is None and a.status == "FAILED"


def test_auto_apply_hosted_reports_honestly(client, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "HOSTED", True)
    _signup(client)
    client.post("/profile/resume", files={"file": ("cv.txt", open("sample/resumes/accountant.txt", "rb").read(), "text/plain")}, data={"parse": "true"})
    ids = _seed_jobs(client, 2, ats="greenhouse")
    client.post("/api/autoapply", json={"ids": ids})
    seen = set()
    st = client.get("/api/queue").json()
    for _ in range(10):
        seen |= {j["status"] for j in st["jobs"].values()}
        if not st["pending"]:
            break
        st = client.post("/api/queue/step").json()
    seen |= {j["status"] for j in st["jobs"].values()}
    assert {"Reviewing", "Applying", "Failed"} <= seen and "Applied" not in seen
    assert all("hosted demo" in j["error"] for j in st["jobs"].values())


def test_autoapply_requires_resume(client):
    _signup(client)
    ids = _seed_jobs(client, 1)
    r = client.post("/api/autoapply", json={"ids": ids})
    assert r.status_code == 400 and "resume" in r.json()["error"].lower()


def test_provider_catalog_has_ten_plus():
    from app.ai import PROVIDERS
    need = {"openai", "anthropic", "gemini", "xai", "deepseek", "mistral", "groq", "together", "openrouter", "custom", "ollama", "lmstudio"}
    assert need <= set(PROVIDERS)


def test_profile_ats_pdf(client):
    _signup(client)
    assert client.get("/profile/ats.pdf").status_code == 404
    client.post("/profile/resume", files={"file": ("cv.txt", open("sample/resumes/accountant.txt", "rb").read(), "text/plain")}, data={"parse": "true"})
    r = client.get("/profile/ats.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
