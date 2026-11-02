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
