"""Job discovery for ANY profession (v1.1). Sources:
- LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs via python-jobspy (MIT)
- The Muse public API, Adzuna + USAJobs (free keys, optional, via env)
- Greenhouse / Lever / Ashby public job-board APIs: ~10,000 recently active company career sites (career_sites.py)
  plus any companies the user lists
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


ALL_SOURCES = ["career_sites", "linkedin", "indeed", "glassdoor", "zip_recruiter", "google", "remotive", "arbeitnow", "themuse",
               "adzuna", "usajobs", "jsearch", "arbeitsagentur", "boards"]
JOBSPY_SITES = ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]


def _parse_date(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc).date()
        if isinstance(v, dt.datetime):
            return v.date()
        if isinstance(v, dt.date):
            return v
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def jobspy_site(site, role, location, remote_only, days, n=30):
    """LinkedIn / Indeed / Glassdoor / ZipRecruiter / Google Jobs via python-jobspy (MIT)."""
    from jobspy import scrape_jobs
    loc = location or ("" if remote_only else "United States")
    g = f"{role} jobs" + (f" near {loc}" if loc else "") + (" remote" if remote_only else "") + \
        (" since yesterday" if days <= 1 else f" in the last {days} days" if days <= 7 else "")
    df = scrape_jobs(site_name=[site], search_term=role, google_search_term=g, location=loc or None, is_remote=remote_only,
                     results_wanted=n, hours_old=days * 24 if days else None, country_indeed="USA",
                     description_format="markdown", linkedin_fetch_description=False, verbose=0)
    out = []
    for r in df.fillna("").to_dict("records"):
        url = r.get("job_url_direct") or r.get("job_url")
        if not url:
            continue
        locs = r.get("location") or ""
        out.append({"url": r.get("job_url") or url, "apply_url": url, "ats": "unknown", "company": str(r.get("company") or ""),
                    "title": str(r.get("title") or ""), "location": ("Remote, " if r.get("is_remote") is True else "") + str(locs),
                    "description": str(r.get("description") or "")[:20000], "source": site,
                    "posted": _parse_date(r.get("date_posted"))})
    return out


def remotive(c, role, **_):
    out = []
    for j in _get(c, "https://remotive.com/api/remote-jobs", params={"search": role, "limit": 50}).get("jobs", []):
        out.append({"url": j["url"], "ats": "unknown", "company": j.get("company_name", ""), "title": j["title"],
                    "location": "Remote, " + j.get("candidate_required_location", ""), "description": _text(j.get("description", "")),
                    "source": "remotive", "posted": _parse_date(j.get("publication_date"))})
    return out


_ARB = {}


def arbeitnow(c, role, **_):
    if "d" not in _ARB:
        rows = []
        for p in (1, 2, 3):
            rows += _get(c, "https://www.arbeitnow.com/api/job-board-api", params={"page": p}).get("data", [])
        _ARB["d"] = rows
    return [{"url": j["url"], "ats": "unknown", "company": j.get("company_name", ""), "title": j["title"],
             "location": ("Remote, " if j.get("remote") else "") + j.get("location", ""),
             "description": _text(j.get("description", "")), "source": "arbeitnow", "posted": _parse_date(j.get("created_at"))}
            for j in _ARB["d"]]


_MUSE = {}


def themuse(c, role, location="", **_):
    key = location
    if key not in _MUSE:
        rows = []
        for p in range(5):
            params = {"page": p, "descending": "true"}
            if location:
                params["location"] = location
            rows += _get(c, "https://www.themuse.com/api/public/jobs", params=params).get("results", [])
        _MUSE[key] = rows
    return [{"url": j["refs"]["landing_page"], "ats": "unknown", "company": (j.get("company") or {}).get("name", ""),
             "title": j["name"], "location": ", ".join(l["name"] for l in j.get("locations", [])),
             "description": _text(j.get("contents", "")), "source": "themuse", "posted": _parse_date(j.get("publication_date"))}
            for j in _MUSE[key]]


def jsearch(c, role, location="", days=7, keys=None, **_):
    """JSearch on RapidAPI (aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google for Jobs). Needs a RapidAPI key."""
    import os
    k = (keys or {}).get("jsearch_key") or os.environ.get("JSEARCH_API_KEY")
    if not k:
        return None
    q = role + (f" in {location}" if location else "")
    dp = "today" if days <= 1 else "3days" if days <= 3 else "week" if days <= 7 else "month"
    r = _get(c, "https://jsearch.p.rapidapi.com/search", params={"query": q, "num_pages": 1, "date_posted": dp,
             **({"remote_jobs_only": "true"} if location == "" and _.get('remote_only') else {})},
             headers={"X-RapidAPI-Key": k, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"})
    return [{"url": j.get("job_apply_link") or j.get("job_google_link"), "apply_url": j.get("job_apply_link"), "ats": "unknown",
             "company": j.get("employer_name", ""), "title": j.get("job_title", ""),
             "location": ("Remote, " if j.get("job_is_remote") else "") + ", ".join(x for x in [j.get("job_city"), j.get("job_state"), j.get("job_country")] if x),
             "description": j.get("job_description", ""), "source": "jsearch",
             "posted": _parse_date(j.get("job_posted_at_datetime_utc"))} for j in r.get("data", []) if j.get("job_apply_link") or j.get("job_google_link")]


def arbeitsagentur(c, role, location="", days=7, keys=None, **_):
    """Germany's Bundesagentur fuer Arbeit job board (public read endpoint used by its own site; no official API, so OFF
    unless the user enables it in Settings or ARBEITSAGENTUR=on)."""
    import os
    if not ((keys or {}).get("arbeitsagentur") or os.environ.get("ARBEITSAGENTUR", "").lower() in ("on", "1", "true")):
        return None
    p = {"was": role, "size": 50, "page": 1, "veroeffentlichtseit": max(1, min(days or 7, 100))}
    if location and location.lower() != "remote":
        p["wo"] = location
    r = _get(c, "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs", params=p,
             headers={"X-API-Key": "jobboerse-jobsuche"})
    out = []
    for j in r.get("stellenangebote", []):
        ref = j.get("refnr", "")
        out.append({"url": j.get("externeUrl") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}", "ats": "unknown",
                    "company": j.get("arbeitgeber", ""), "title": j.get("titel", ""),
                    "location": ", ".join(x for x in [(j.get("arbeitsort") or {}).get("ort"), "Germany"] if x),
                    "description": j.get("beruf", ""), "source": "arbeitsagentur", "posted": _parse_date(j.get("aktuelleVeroeffentlichungsdatum"))})
    return out


def adzuna(c, role, location="", days=7, keys=None, **_):
    import os
    keys = keys or {}
    aid, akey = keys.get("adzuna_app_id") or os.environ.get("ADZUNA_APP_ID"), keys.get("adzuna_app_key") or os.environ.get("ADZUNA_APP_KEY")
    if not (aid and akey):
        return None
    p = {"app_id": aid, "app_key": akey, "what": role, "results_per_page": 50, "content-type": "application/json"}
    if location and location.lower() != "remote":
        p["where"] = location
    if days:
        p["max_days_old"] = days
    return [{"url": j["redirect_url"], "ats": "unknown", "company": (j.get("company") or {}).get("display_name", ""),
             "title": j["title"], "location": (j.get("location") or {}).get("display_name", ""),
             "description": j.get("description", ""), "source": "adzuna", "posted": _parse_date(j.get("created"))}
            for j in _get(c, "https://api.adzuna.com/v1/api/jobs/us/search/1", params=p).get("results", [])]


def usajobs(c, role, location="", days=7, keys=None, **_):
    import os
    key, mail = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not (key and mail):
        return None
    p = {"Keyword": role, "ResultsPerPage": 50}
    if location and location.lower() != "remote":
        p["LocationName"] = location
    if days:
        p["DatePosted"] = days
    r = _get(c, "https://data.usajobs.gov/api/search", params=p,
             headers={"Host": "data.usajobs.gov", "User-Agent": mail, "Authorization-Key": key})
    out = []
    for it in r.get("SearchResult", {}).get("SearchResultItems", []):
        d = it["MatchedObjectDescriptor"]
        out.append({"url": d["PositionURI"], "ats": "unknown", "company": d.get("OrganizationName", ""), "title": d["PositionTitle"],
                    "location": d.get("PositionLocationDisplay", ""), "description": d.get("QualificationSummary", ""),
                    "source": "usajobs", "posted": _parse_date(d.get("PublicationStartDate"))})
    return out


API_SOURCES = {"jsearch": jsearch, "arbeitsagentur": arbeitsagentur, "remotive": remotive, "arbeitnow": arbeitnow, "themuse": themuse, "adzuna": adzuna, "usajobs": usajobs}

_W = re.compile(r"[a-z0-9+#]+")
_GENERIC = {"senior", "sr", "junior", "jr", "lead", "staff", "principal", "i", "ii", "iii", "iv", "remote", "hybrid", "the", "of", "and"}


def title_fit(title, roles):
    """0..1: best share of a target role's words found in the job title (handles 'RN' and plural forms loosely)."""
    t = set(_W.findall(title.lower().replace("registered nurse", "registered nurse rn")))
    t |= {w.rstrip("s") for w in t}
    best = 0.0
    for r in roles:
        rw = [w for w in _W.findall(r.lower()) if w not in _GENERIC]
        if rw:
            best = max(best, sum(1 for w in rw if w in t or w.rstrip("s") in t) / len(rw))
    return best


def _loc_ok(job, location, remote_only):
    L = (job.get("location") or "") + " " + job["title"]
    if remote_only:
        return "remote" in L.lower() or "anywhere" in L.lower()
    if not location or job["source"] in JOBSPY_SITES + ["adzuna", "usajobs", "jsearch", "arbeitsagentur"]:
        return True  # these sources already filtered by location server-side
    parts = [p.strip().lower() for p in re.split(r"[,/]", location) if p.strip()]
    return (job["source"] == "remotive" and "remote" in L.lower()) or any(
        re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", L.lower()) for p in parts)


def search(user_id, location="", remote_only=False, days=7, companies="", sources=None, roles_override=None, per_site=30,
           boards_offset=0):
    """Profile-driven discovery: derive roles from the resume, query every source, dedupe, gate by role, rank by fit.
    Returns (jobs, errors, meta)."""
    import concurrent.futures as cf
    from .ai import AIConfig, Provider
    from .db import Settings
    from .roles import derive_search_profile
    srcs = [s for s in (sources or ALL_SOURCES) if s in ALL_SOURCES]
    if (location or "").strip().lower() == "remote":
        location, remote_only = "", True
    with SessionLocal() as s:
        prof, st = s.get(Profile, user_id), s.get(Settings, user_id)
        ai = Provider(AIConfig.from_settings(st)) if st else None
        sp = derive_search_profile(prof, ai)
        keys = dict((st.extra or {}) if st else {})
    roles = roles_override or sp["roles"] or ["software engineer"]
    found, errors, counts = [], [], {}
    cs_meta = {}

    def run_jobspy(site, role):
        return site, jobspy_site(site, role, location, remote_only, days, per_site)

    tasks = []
    with cf.ThreadPoolExecutor(8) as ex, httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        for site in [x for x in srcs if x in JOBSPY_SITES]:
            for role in roles[:3]:
                tasks.append(ex.submit(run_jobspy, site, role))
        for name in [x for x in srcs if x in API_SOURCES]:
            for role in (roles[:3] if name in ("remotive", "adzuna", "usajobs", "jsearch", "arbeitsagentur") else roles[:1]):
                tasks.append(ex.submit(lambda n=name, r=role: (n, API_SOURCES[n](c, r, location=location, days=days, keys=keys,
                                                                                    remote_only=remote_only))))
        if "career_sites" in srcs:
            from . import career_sites as cs
            from .config import CAREER_SITES_BUDGET
            cutoff_d = (dt.date.today() - dt.timedelta(days=days)) if days else None

            def cs_match(j):
                return (title_fit(j["title"], roles) >= 0.6 and _loc_ok(dict(j, source="career_sites"), location, remote_only)
                        and not (cutoff_d and j.get("posted") and j["posted"] < cutoff_d))

            def run_cs():
                js, m = cs.scan(cs_match, offset=boards_offset, budget_s=CAREER_SITES_BUDGET)
                cs_meta.update(m); return "career_sites", js
            tasks.append(ex.submit(run_cs))
        if "boards" in srcs and companies:
            for ats, slug in parse_companies(companies):
                tasks.append(ex.submit(lambda a=ats, sl=slug: ("boards", board_jobs(c, a, sl))))
        for f in cf.as_completed(tasks):
            try:
                name, js = f.result()
            except Exception as e:
                errors.append(f"{type(e).__name__}: {str(e)[:120]}")
                continue
            if js is None:
                counts.setdefault(name, "no key")
                continue
            counts[name] = counts.get(name, 0) if isinstance(counts.get(name), int) else 0
            counts[name] += len(js)
            found += js
    cutoff = (dt.date.today() - dt.timedelta(days=days)) if days else None
    raw = {k: v for k, v in counts.items()}
    kept = {}
    with SessionLocal() as s:
        prof = s.get(Profile, user_id)
        seen, out = set(), []
        for j in found:
            key = j["url"].split("?")[0] if j["source"] not in JOBSPY_SITES else j["url"]
            dkey = re.sub(r"\W+", " ", f"{j['company']} {j['title']}".lower()).strip()
            if key in seen or dkey in seen:
                continue
            tf = title_fit(j["title"], roles)
            if tf < 0.6 or not _loc_ok(j, location, remote_only):
                continue
            if cutoff and j.get("posted") and j["posted"] < cutoff:
                continue
            seen.add(key); seen.add(dkey)
            m = score(prof, j["title"], j["description"] or j["title"])
            m["title_fit"] = round(tf, 2)
            j["match_score"] = round(0.35 * 100 * tf + 0.65 * m["score"], 1)
            j["match_details"] = m
            j["posted"] = j["posted"].isoformat() if j.get("posted") else ""
            kept[j["source"]] = kept.get(j["source"], 0) + 1
            out.append(j)
    out.sort(key=lambda j: -j["match_score"])
    meta = {"career_sites": cs_meta, "profile": sp, "roles": roles, "raw_counts": raw, "kept_counts": kept, "location": location or ("Remote" if remote_only else "Anywhere (US)"),
            "days": days}
    return out, errors, meta


def save_jobs(user_id, jobs, status="DISCOVERED"):
    """Insert jobs for this user, skipping ones already tracked (dedupe by URL). Returns count added."""
    added = 0
    with SessionLocal() as s:
        existing = set(s.scalars(select(Application.url).where(Application.user_id == user_id)))
        from .ats import detect
        for j in jobs:
            if j.get("ats") == "unknown" and j.get("apply_url"):
                try:
                    j["ats"] = detect(j["apply_url"])[0] or "unknown"
                except Exception:
                    pass
            if j["url"] in existing:
                continue
            existing.add(j["url"])
            s.add(Application(user_id=user_id, url=j["url"], apply_url=j.get("apply_url") or j["url"], source=j["source"], ats=j["ats"],
                              company=j["company"], title=j["title"], location=j["location"],
                              job_description=j["description"][:20000], match_score=j["match_score"],
                              match_details=j["match_details"], status=status))
            added += 1
        s.commit()
    return added


def run_saved_search(ss_id):
    with SessionLocal() as s:
        ss = s.get(SavedSearch, ss_id)
        m = re.search(r"days=(\d+)", ss.sources or "")
        args = dict(user_id=ss.user_id, location=ss.location, remote_only=ss.remote_only, companies=ss.companies,
                    days=int(m.group(1)) if m else 7)
        auto = ss.auto_queue_above
    jobs, errors, _ = search(**args)
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
