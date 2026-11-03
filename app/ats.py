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
         "recruitee.com": "recruitee", "breezy.hr": "breezyhr", "teamtailor.com": "teamtailor",
         "taleo.net": "taleo", "successfactors": "successfactors", "avature.net": "avature", "ultipro.com": "ultipro",
         "ukg.com": "ultipro", "contacthr.com": "contacthr", "paylocity.com": "paylocity", "adp.com": "adp",
         "jazzhr.com": "jazzhr", "applytojob.com": "jazzhr", "oraclecloud.com": "oracle", "dayforcehcm.com": "dayforce",
         "linkedin.com": "linkedin", "glassdoor.": "glassdoor", "indeed.com": "indeed", "ziprecruiter.com": "ziprecruiter",
         "dice.com": "dice", "grnh.se": "greenhouse_short"}
# Job aggregators: the real application lives elsewhere (or behind the site's own login / Easy Apply).
AGGREGATORS = {"linkedin", "glassdoor", "indeed", "ziprecruiter", "dice"}
# Need an account on the employer's portal before the form appears.
ACCOUNT_PORTALS = {"workday", "taleo", "successfactors", "icims", "oracle", "avature", "adp", "dayforce"}
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


def host(url: str) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def resolve(url: str, client=None) -> str:
    """Follow short links / redirects (grnh.se, lnkd.in, tracking links) and pull the employer's apply link out of
    aggregator pages (LinkedIn 'applyUrl', Glassdoor/Indeed redirects). Returns the best URL to apply on."""
    import httpx
    import re as _re
    from html import unescape
    own = client is None
    c = client or httpx.Client(follow_redirects=True, timeout=20,
                               headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"})
    try:
        r = c.get(url)
        final = str(r.url)
        ats = detect(final)[0]
        if ats in AGGREGATORS:
            html = unescape(r.text or "")
            for rx in (r'<code id="applyUrl"[^>]*><!--"?(https?[^"<]+?)"?--></code>', r'"applyUrl"\s*:\s*"(https?[^"]+)"',
                       r'"(?:companyApplyUrl|externalApplyUrl|applyLink)"\s*:\s*"(https?[^"]+)"'):
                m = _re.search(rx, html)
                if m:
                    ext = m.group(1).encode().decode("unicode_escape") if "\\u" in m.group(1) else m.group(1)
                    if "linkedin.com/jobs" in ext and "externalApply" in ext:
                        from urllib.parse import parse_qs as _pq
                        ext = (_pq(urlparse(ext).query).get("url") or [ext])[0]
                    try:
                        return str(c.get(ext).url)
                    except Exception:
                        return ext
        return final
    except Exception:
        return url
    finally:
        if own:
            c.close()
