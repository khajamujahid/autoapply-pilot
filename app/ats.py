"""ATS detection from a job URL. Returns (ats_id, ids dict)."""
import re
from urllib.parse import urlparse, parse_qs

PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", re.I)),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkday(?:jobs|site)\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/", re.I)),
]
OTHER = {"icims.com": "icims", "jobvite.com": "jobvite", "smartrecruiters.com": "smartrecruiters",
         "workable.com": "workable", "bamboohr.com": "bamboohr", "rippling.com": "rippling",
         "recruitee.com": "recruitee", "breezy.hr": "breezyhr", "teamtailor.com": "teamtailor"}
SUPPORTED = {"greenhouse", "lever", "ashby", "workday"}


def detect(url: str):
    for ats, rx in PATTERNS:
        m = rx.search(url)
        if m:
            g = m.groups()
            if ats == "workday":
                return ats, {"tenant": g[0], "dc": g[1], "site": g[2]}
            return ats, {"company": g[0], "job_id": g[1]}
    q = parse_qs(urlparse(url).query)
    if "gh_jid" in q:  # company career page embedding Greenhouse
        return "greenhouse", {"company": None, "job_id": q["gh_jid"][0]}
    host = urlparse(url).netloc.lower()
    for dom, ats in OTHER.items():
        if dom in host:
            return ats, {}
    return "unknown", {}
