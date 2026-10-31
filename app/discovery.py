"""Job discovery from public sources that publish open JSON feeds:
- Greenhouse / Lever / Ashby public job-board APIs for companies the user lists
- Remotive public API (remote jobs)   https://remotive.com/api/remote-jobs
- Arbeitnow public job board API      https://www.arbeitnow.com/api/job-board-api
"""
import datetime as dt
import re
import httpx
from sqlalchemy import select
from .db import Application, Profile, SavedSearch, SessionLocal, now
from .fetcher import _text, UA
from .matcher import score


def _get(c, url, **kw):
    r = c.get(url, **kw); r.raise_for_status(); return r.json()


def parse_companies(text: str):
    out = []
    for line in (text or "").replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            ats, slug = line.split(":", 1)
            out.append((ats.strip().lower(), slug.strip()))
        else:
            out.append(("auto", line))
    return out


def board_jobs(c, ats, slug):
    jobs = []
    tries = ["greenhouse", "lever", "ashby"] if ats == "auto" else [ats]
    for a in tries:
        try:
            if a == "greenhouse":
                for j in _get(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})["jobs"]:
                    jobs.append({"url": f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}", "ats": "greenhouse", "company": slug,
                                 "title": j["title"], "location": (j.get("location") or {}).get("name", ""),
                                 "description": _text(j.get("content", "")), "source": "greenhouse"})
            elif a == "lever":
                for j in _get(c, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}):
                    jobs.append({"url": j["hostedUrl"], "ats": "lever", "company": slug, "title": j["text"],
                                 "location": (j.get("categories") or {}).get("location", ""),
                                 "description": j.get("descriptionPlain", ""), "source": "lever"})
            elif a == "ashby":
                for j in _get(c, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"]:
                    jobs.append({"url": j["jobUrl"], "ats": "ashby", "company": slug, "title": j["title"],
                                 "location": j.get("location", ""), "description": j.get("descriptionPlain", ""), "source": "ashby"})
            if jobs:
                return jobs
        except Exception:
            continue
    return jobs


def remotive(c, kw):
    out = []
    for j in _get(c, "https://remotive.com/api/remote-jobs", params={"search": kw, "limit": 50}).get("jobs", []):
        out.append({"url": j["url"], "ats": "unknown", "company": j.get("company_name", ""), "title": j["title"],
                    "location": j.get("candidate_required_location", "Remote"), "description": _text(j.get("description", "")),
                    "source": "remotive"})
    return out


def arbeitnow(c, kw):
    out = []
    for j in _get(c, "https://www.arbeitnow.com/api/job-board-api").get("data", []):
        out.append({"url": j["url"], "ats": "unknown", "company": j.get("company_name", ""), "title": j["title"],
                    "location": ("Remote, " if j.get("remote") else "") + j.get("location", ""),
                    "description": _text(j.get("description", "")), "source": "arbeitnow"})
    return out


def _matches(job, kw, loc, remote_only):
    hay = f"{job['title']} {job['description'][:3000]}".lower()
    if kw and not all(any(w in hay for w in alt.split("|")) for alt in kw.lower().split()):
        return False
    if loc and loc.lower() not in job["location"].lower() and "remote" not in job["location"].lower():
        return False
    if remote_only and "remote" not in (job["location"] + job["title"]).lower():
        return False
    return True


def search(user_id, keywords="", location="", remote_only=False, companies="", sources="boards,remotive,arbeitnow"):
    """Return ranked, filtered jobs (not yet saved)."""
    srcs = set(s.strip() for s in sources.split(","))
    found, errors = [], []
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        if "boards" in srcs:
            for ats, slug in parse_companies(companies):
                js = board_jobs(c, ats, slug)
                if not js:
                    errors.append(f"No public board found for {ats}:{slug}")
                found += js
        for name, fn in (("remotive", remotive), ("arbeitnow", arbeitnow)):
            if name in srcs and keywords:
                try:
                    found += fn(c, keywords)
                except Exception as e:
                    errors.append(f"{name}: {e}")
    with SessionLocal() as s:
        prof = s.get(Profile, user_id)
        seen, out = set(), []
        for j in found:
            if j["url"] in seen or not _matches(j, keywords, location, remote_only):
                continue
            seen.add(j["url"])
            m = score(prof, j["title"], j["description"])
            j["match_score"], j["match_details"] = m["score"], m
            out.append(j)
    out.sort(key=lambda j: -j["match_score"])
    return out, errors


def save_jobs(user_id, jobs, status="DISCOVERED"):
    """Insert jobs for this user, skipping ones already tracked (dedupe by URL). Returns count added."""
    added = 0
    with SessionLocal() as s:
        existing = set(s.scalars(select(Application.url).where(Application.user_id == user_id)))
        for j in jobs:
            if j["url"] in existing:
                continue
            existing.add(j["url"])
            s.add(Application(user_id=user_id, url=j["url"], apply_url=j["url"], source=j["source"], ats=j["ats"],
                              company=j["company"], title=j["title"], location=j["location"],
                              job_description=j["description"][:20000], match_score=j["match_score"],
                              match_details=j["match_details"], status=status))
            added += 1
        s.commit()
    return added


def run_saved_search(ss_id):
    with SessionLocal() as s:
        ss = s.get(SavedSearch, ss_id)
        args = dict(user_id=ss.user_id, keywords=ss.keywords, location=ss.location, remote_only=ss.remote_only,
                    companies=ss.companies, sources=ss.sources)
        auto = ss.auto_queue_above
    jobs, errors = search(**args)
    hi = [j for j in jobs if auto and j["match_score"] >= auto]
    lo = [j for j in jobs if j not in hi]
    n = save_jobs(args["user_id"], hi, "QUEUED") + save_jobs(args["user_id"], lo, "DISCOVERED")
    with SessionLocal() as s:
        s.get(SavedSearch, ss_id).last_run = now(); s.commit()
    return n, errors


def due_saved_searches():
    with SessionLocal() as s:
        out = []
        for ss in s.scalars(select(SavedSearch).where(SavedSearch.every_hours > 0)):
            lr = ss.last_run.replace(tzinfo=dt.timezone.utc) if ss.last_run and ss.last_run.tzinfo is None else ss.last_run
            if not lr or now() - lr >= dt.timedelta(hours=ss.every_hours):
                out.append(ss.id)
        return out
