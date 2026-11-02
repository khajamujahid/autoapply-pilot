"""Offline tests for every job-source parser (httpx MockTransport), JobSpy wrapper, saved searches and AI provider paths."""
import json
import types

import httpx
import pytest

from app import discovery, ai as ai_mod
from app.ai import Provider, AIConfig, AIError, MockBrain
from app.answerer import Answerer
from app.profile import default_answers, rule_answer, profile_summary, full_name, extract_text

FEEDS = {
    "remotive.com": {"jobs": [{"url": "https://r/1", "company_name": "R", "title": "Network Engineer", "candidate_required_location": "USA",
                               "description": "<p>BGP</p>", "publication_date": "2026-10-30T00:00:00"}]},
    "arbeitnow.com": {"data": [{"url": "https://a/1", "company_name": "A", "title": "Network Engineer", "remote": True, "location": "Berlin",
                                "description": "x", "created_at": 1761800000}]},
    "themuse.com": {"results": [{"refs": {"landing_page": "https://m/1"}, "company": {"name": "M"}, "name": "Network Engineer",
                                 "locations": [{"name": "Dallas, TX"}], "contents": "x", "publication_date": "2026-10-30"}]},
    "jsearch.p.rapidapi.com": {"data": [{"job_apply_link": "https://j/1", "employer_name": "J", "job_title": "Network Engineer",
                                         "job_city": "Dallas", "job_state": "TX", "job_country": "US", "job_is_remote": False,
                                         "job_description": "x", "job_posted_at_datetime_utc": "2026-10-30T00:00:00Z"}]},
    "api.adzuna.com": {"results": [{"redirect_url": "https://z/1", "company": {"display_name": "Z"}, "title": "Network Engineer",
                                    "location": {"display_name": "Dallas"}, "description": "x", "created": "2026-10-30T00:00:00Z"}]},
    "data.usajobs.gov": {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {"PositionURI": "https://u/1",
                         "OrganizationName": "DoD", "PositionTitle": "IT Specialist (Network)", "PositionLocationDisplay": "Dallas, TX",
                         "QualificationSummary": "x", "PublicationStartDate": "2026-10-30"}}]}},
    "rest.arbeitsagentur.de": {"stellenangebote": [{"refnr": "10000-1", "arbeitgeber": "BA", "titel": "Netzwerkadministrator",
                                                    "arbeitsort": {"ort": "Berlin"}, "beruf": "IT", "aktuelleVeroeffentlichungsdatum": "2026-10-30"}]},
    "boards-api.greenhouse.io": {"jobs": [{"id": 7, "title": "Network Engineer", "location": {"name": "Remote"}, "content": "&lt;p&gt;x&lt;/p&gt;"}]},
    "api.lever.co": [{"hostedUrl": "https://jobs.lever.co/x/1", "text": "Network Engineer", "categories": {"location": "NYC"}, "descriptionPlain": "x"}],
    "api.ashbyhq.com": {"jobs": [{"jobUrl": "https://jobs.ashbyhq.com/x/1", "title": "Network Engineer", "location": "SF", "descriptionPlain": "x"}]},
}


@pytest.fixture
def c():
    def handler(req):
        for host, body in FEEDS.items():
            if host in str(req.url):
                return httpx.Response(200, json=body)
        return httpx.Response(404)
    discovery._ARB.clear(); discovery._MUSE.clear()
    return httpx.Client(transport=httpx.MockTransport(handler))


KEYS = {"jsearch_key": "k", "adzuna_app_id": "i", "adzuna_app_key": "k", "arbeitsagentur": True}


def test_every_source_parses(c, monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "k"); monkeypatch.setenv("USAJOBS_EMAIL", "a@b.co")
    for name, fn in discovery.API_SOURCES.items():
        out = fn(c, "network engineer", location="Dallas, TX", days=7, keys=KEYS)
        assert out and out[0]["url"].startswith("https://") and out[0]["title"] and out[0]["source"] == name, name
    assert discovery.remotive(c, "x")[0]["posted"].isoformat() == "2026-10-30"
    for ats in ("greenhouse", "lever", "ashby"):
        js = discovery.board_jobs(c, ats, "x"); assert js and js[0]["ats"] == ats
    assert discovery.board_jobs(c, "auto", "x")[0]["ats"] == "greenhouse"
    assert discovery.parse_companies("greenhouse:gitlab, ramp") == [("greenhouse", "gitlab"), ("auto", "ramp")]


def test_parse_date_variants():
    import datetime as dt
    assert discovery._parse_date("2026-10-30T10:00") == dt.date(2026, 10, 30)
    assert discovery._parse_date(1761800000000).year == 2025 and discovery._parse_date(None) is None
    assert discovery._parse_date("garbage") is None and discovery._parse_date(dt.datetime(2026, 1, 2)) == dt.date(2026, 1, 2)


def test_jobspy_wrapper(monkeypatch):
    import pandas as pd
    import jobspy
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return pd.DataFrame([{"job_url": "https://li/1", "job_url_direct": "https://co/apply", "company": "Acme", "title": "Network Engineer",
                              "location": "Dallas, TX", "is_remote": False, "description": "BGP", "date_posted": "2026-10-30"}])
    monkeypatch.setattr(jobspy, "scrape_jobs", fake)
    out = discovery.jobspy_site("linkedin", "network engineer", "Dallas, TX", False, 3)
    assert out[0]["apply_url"] == "https://co/apply" and out[0]["source"] == "linkedin" and seen["hours_old"] == 72
    assert "last 3 days" in seen["google_search_term"]


def test_saved_search_flow(monkeypatch):
    from app.auth import create_user
    from app.db import SessionLocal, SavedSearch, Profile
    import time
    u = create_user(f"s{time.time_ns()}@example.com", "Password123!", "A B")
    with SessionLocal() as s:
        p = s.get(Profile, u.id); p.data = {**p.data, "current_title": "Network Engineer", "skills": "BGP"}
        s.add(SavedSearch(user_id=u.id, name="n", location="Dallas, TX", sources="auto;days=3", every_hours=1, auto_queue_above=1)); s.commit()
    monkeypatch.setattr(discovery, "jobspy_site", lambda *a, **k: [{"url": f"https://x/{a[0]}", "ats": "unknown", "company": a[0],
                        "title": "Network Engineer", "location": "Dallas, TX", "description": "BGP", "source": a[0], "posted": None}])
    for name in list(discovery.API_SOURCES):
        monkeypatch.setitem(discovery.API_SOURCES, name, lambda *a, **k: None)
    ids = discovery.due_saved_searches(); assert ids
    n, errors = discovery.run_saved_search(ids[-1]); assert n == 5 and not errors
    assert ids[-1] not in discovery.due_saved_searches()


# ---------------- AI provider (HTTP mocked)
class R:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, json.dumps(body)

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://x"), response=self)


def test_openai_compatible_and_anthropic(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if "messages" in url and "anthropic" in url:
            return R(200, {"content": [{"text": "hi from claude"}]})
        return R(200, {"choices": [{"message": {"content": '{"answer": "No"}'}}]})
    monkeypatch.setattr(ai_mod.httpx, "post", post)
    p = Provider(AIConfig("openai", "sk", "gpt", "https://api.openai.com/v1"))
    assert p.answer("Need sponsorship?", ["Yes", "No"], "prof") == "No"
    assert calls[-1][1]["json"]["response_format"] == {"type": "json_object"}
    assert Provider(AIConfig("anthropic", "k", "claude", "https://api.anthropic.com/v1")).complete("s", "u") == "hi from claude"
    assert Provider(AIConfig("ollama", "", "qwen", "http://localhost:11434/v1")).complete("s", "u")  # no key needed
    assert calls[-1][1]["timeout"] == 300


def test_ai_errors(monkeypatch):
    with pytest.raises(AIError, match="No API key"):
        Provider(AIConfig("openai", "", "m", "u")).complete("s", "u")
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: R(401, {"error": "bad key"}))
    with pytest.raises(AIError, match="401"):
        Provider(AIConfig("xai", "k", "m", "https://x")).complete("s", "u")

    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(ai_mod.httpx, "post", boom)
    with pytest.raises(AIError, match="connection"):
        Provider(AIConfig("gemini", "k", "m", "https://g")).complete("s", "u")
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: R(200, {"choices": [{"message": {"content": "not json"}}]}))
    with pytest.raises(AIError, match="JSON"):
        Provider(AIConfig("openai", "k", "m", "https://o")).complete_json("s", "u")


def test_mock_brain_tasks():
    m = Provider(AIConfig("mock"))
    parsed = m.parse_resume("Pat Lee\npat@example.com | 555-123-4567\nSKILLS\nGAAP, Excel\n5 years")
    assert parsed["email"] == "pat@example.com" and parsed["years_experience"] == 5 and "GAAP" in parsed["skills"]
    assert "Pat" in m.cover_letter("x", "Pat", "Accountant", "Acme", "jd", {"matched_skills": ["gaap"]})
    assert m.test() and "Matched" in m.explain_match("x", "t", "jd", {"matched_skills": []})
    assert MockBrain.answer("Gender", ["Male", "Decline"], False) == "Decline" and MockBrain.answer("Favourite food?", ["A", "B"]) == ""
    assert MockBrain.answer("Free text?", None) == "N/A"


def test_answerer_choose_paths():
    prof = types.SimpleNamespace(data={"first_name": "Pat"}, answers=default_answers(), resume_text="")
    a = Answerer(prof, Provider(AIConfig("mock")), cover_letter="CL")
    assert a.text("Cover letter") == "CL"
    assert a.choose("Will you require sponsorship?", ["Select...", "Yes", "No"]) == "No"
    assert a.choose("Are you legally authorized to work in the US?", ["Yes", "No"], multi=True) == ["Yes"]
    assert a.choose("Pick your favourite", ["Red", "Blue"]) is None and a.unknown[-1]["via"] == "pending"
    assert a.choose("x", ["Select one"]) is None


def test_profile_helpers(tmp_path):
    prof = types.SimpleNamespace(data={"first_name": "Pat", "last_name": "Lee", "linkedin": "li", "current_company": "Acme"},
                                 answers={**default_answers(), "zip": "60601", "address": "1 Main", "state": "IL", "salary_expectation": "90000"},
                                 resume_text="")
    assert full_name(prof) == "Pat Lee" and "RESUME" in profile_summary(prof)
    assert rule_answer(prof, "ZIP code") == "60601" and rule_answer(prof, "Street address") == "1 Main"
    assert rule_answer(prof, "State", ["Illinois", "IL"]) == "IL" and rule_answer(prof, "Desired salary") == "90000"
    assert rule_answer(prof, "Current employer") == "Acme" and rule_answer(prof, "LinkedIn") == "li"
    assert rule_answer(prof, "Have you been convicted of a felony?", ["Yes", "No"]) == "No"
    assert rule_answer(prof, "Do you consent to the privacy policy?", ["I agree", "No"]) == "I agree"
    assert rule_answer(prof, "Something random") is None
    f = tmp_path / "r.txt"; f.write_text("hello resume"); assert extract_text(str(f)) == "hello resume"
    import docx
    d = docx.Document(); d.add_paragraph("docx resume"); d.save(tmp_path / "r.docx"); assert extract_text(str(tmp_path / "r.docx")) == "docx resume"


def test_answerer_ai_error_paths_save_question():
    prof = types.SimpleNamespace(data={}, answers={}, resume_text="")
    a = Answerer(prof, Provider(AIConfig("openai", "", "m", "u")))  # no key -> AIError -> pending question
    assert a.text("Describe a hard project") is None and a.choose("Pick a colour", ["Red", "Blue"]) is None
    assert [u["via"] for u in a.unknown] == ["pending", "pending"] and "ai_error" in a.used[-1]["via"]
