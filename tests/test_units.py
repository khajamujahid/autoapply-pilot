"""Unit + local-fixture tests. Run: pytest -q tests/test_units.py (no network, nothing real submitted)."""
import os
import pathlib
import types
import pytest


from app.ats import detect
from app.crypto import enc, dec
from app.matcher import score
from app.profile import rule_answer, default_answers
from app.ai import Provider, AIConfig
from app.answerer import Answerer

PROF = types.SimpleNamespace(data={"first_name": "Alex", "skills": "Python, Go, AWS", "years_experience": 5,
                                   "linkedin": "https://linkedin.com/in/x"}, answers=default_answers(), resume_text="Python Go AWS engineer")


def test_detect():
    assert detect("https://job-boards.greenhouse.io/gitlab/jobs/123")[0] == "greenhouse"
    assert detect("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd")[0] == "lever"
    assert detect("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245")[0] == "ashby"
    assert detect("https://acme.wd5.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1")[0] == "workday"
    assert detect("https://careers.airbnb.com/positions/1?gh_jid=1")[0] == "greenhouse"


def test_crypto():
    assert dec(enc("secret")) == "secret" and enc("secret") != "secret"


def test_score():
    m = score(PROF, "Senior Backend Engineer", "We use Python and AWS. 3+ years experience.")
    assert m["matched_skills"] == ["python", "aws"] and 0 < m["score"] <= 100


def test_rules():
    assert rule_answer(PROF, "Will you now or in the future require sponsorship?", ["Yes", "No"]) == "No"
    assert rule_answer(PROF, "Gender", ["Male", "Female", "Decline To Self Identify"]) == "Decline To Self Identify"
    assert rule_answer(PROF, "LinkedIn Profile") == "https://linkedin.com/in/x"


def test_mock_ai_conservative():
    p = Provider(AIConfig("mock"))
    assert p.test() == "OK"
    a = Answerer(PROF, p)
    assert a.choose("Do you hold an active security clearance?", ["Yes", "No"]) == "No"
    assert a.choose("Which team interests you most?", ["Payments", "Infra"]) is None  # unknown -> left for human


def test_captcha_goes_needs_manual(tmp_path):
    from app.submitters.base import BaseSubmitter, NeedsManual
    fx = pathlib.Path(__file__).parent / "fixtures" / "captcha_form.html"

    class Local(BaseSubmitter):
        submit_selectors = ["button[type=submit]"]
        def fill_form(self, page):
            self.fill(page, "#fn", "Alex", "first_name")

    app = types.SimpleNamespace(id=1, user_id=0, url=fx.as_uri(), apply_url=fx.as_uri())
    sub = Local(app, PROF, Answerer(PROF, Provider(AIConfig("mock"))), None, dry_run=False)
    with pytest.raises(NeedsManual):
        sub.run()
    assert "first_name" in sub.filled and any("sponsorship" in f for f in sub.filled)
    assert any("Gender" in f and "Decline" in f for f in sub.filled)


def test_years_required_v16():
    from app.matcher import years_required as y
    assert y("Travelers: 70 years of history. 6+ years of experience required") == 6
    assert y("3-5 years of professional experience") == 3
    assert y("must be 18 years old") is None and y("salary $70 years") is None
    assert y("no number here") is None


def test_detect_v16():
    from app.ats import detect, AGGREGATORS
    for u, a in [("https://travelersindex.contacthr.com/1", "contacthr"), ("https://deloitteus.avature.net/x", "avature"),
                 ("https://recruiting2.ultipro.com/A/JobBoard/x", "ultipro"), ("https://www.linkedin.com/jobs/view/1", "linkedin"),
                 ("https://www.glassdoor.com/job-listing/j?jl=1", "glassdoor"), ("https://grnh.se/abc", "greenhouse_short")]:
        assert detect(u)[0] == a
    assert "linkedin" in AGGREGATORS


def test_submitter_for_never_unsupported():
    from app.submitters import submitter_for, UniversalSubmitter, SUBMITTERS
    assert submitter_for("contacthr") is UniversalSubmitter and submitter_for("unknown") is UniversalSubmitter
    assert submitter_for("greenhouse") is SUBMITTERS["greenhouse"]


def test_universal_fills_local_form(tmp_path):
    """Universal filler on a local multi-step form behind an Apply button (no network, dry run)."""
    from app.submitters.universal import UniversalSubmitter
    html = tmp_path / "job.html"
    html.write_text("""<html><body><h1>Job</h1><a href='form.html'>Apply now</a></body></html>""")
    (tmp_path / "form.html").write_text("""<form><label for=f>First Name *</label><input id=f required>
      <label for=l>Last Name</label><input id=l><label for=e>Email</label><input id=e type=email>
      <label for=p>Phone</label><input id=p><label>Resume<input type=file id=r></label>
      <button type=submit>Submit Application</button></form>""")
    res = tmp_path / "r.pdf"; res.write_bytes(b"%PDF-1.4\n%%EOF")
    app = types.SimpleNamespace(apply_url=html.as_uri(), url=html.as_uri(), user_id=0, id=9999)
    prof = types.SimpleNamespace(data={"first_name": "Alex", "last_name": "Kim", "email": "a@x.io", "phone": "555"},
                                 answers=default_answers(), resume_text="x")
    ans = Answerer(prof, Provider(AIConfig()), "")
    r = UniversalSubmitter(app, prof, ans, str(res), dry_run=True).run()
    assert not r["submitted"] and {"first_name", "last_name", "email", "phone", "resume"} <= set(r["filled"])
