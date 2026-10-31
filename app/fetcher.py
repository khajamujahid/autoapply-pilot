"""Fetch job descriptions via public job-board APIs, with an HTML fallback."""
import html as htmllib
import re
import httpx
from bs4 import BeautifulSoup
from .ats import detect

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def _text(html: str) -> str:
    t = BeautifulSoup(htmllib.unescape(html or ""), "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()[:20000]


def fetch_job(url: str) -> dict:
    ats, ids = detect(url)
    out = {"ats": ats, "company": "", "title": "", "location": "", "description": "", "apply_url": url}
    with httpx.Client(headers=UA, timeout=25, follow_redirects=True) as c:
        try:
            if ats == "greenhouse" and ids.get("company"):
                j = c.get(f"https://boards-api.greenhouse.io/v1/boards/{ids['company']}/jobs/{ids['job_id']}").json()
                out.update(company=ids["company"], title=j.get("title", ""),
                           location=(j.get("location") or {}).get("name", ""), description=_text(j.get("content", "")),
                           apply_url=f"https://job-boards.greenhouse.io/{ids['company']}/jobs/{ids['job_id']}")
                return out
            if ats == "lever":
                j = c.get(f"https://api.lever.co/v0/postings/{ids['company']}/{ids['job_id']}").json()
                desc = j.get("descriptionPlain", "") + "\n\n" + "\n".join(
                    l.get("text", "") + "\n" + _text(l.get("content", "")) for l in j.get("lists", []))
                out.update(company=ids["company"], title=j.get("text", ""),
                           location=(j.get("categories") or {}).get("location", ""), description=desc.strip(),
                           apply_url=f"https://jobs.lever.co/{ids['company']}/{ids['job_id']}/apply")
                return out
            if ats == "ashby":
                j = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{ids['company']}").json()
                for p in j.get("jobs", []):
                    if ids["job_id"] in (p.get("id", ""), p.get("jobUrl", "")) or ids["job_id"] in p.get("jobUrl", ""):
                        out.update(company=ids["company"], title=p.get("title", ""), location=p.get("location", ""),
                                   description=p.get("descriptionPlain") or _text(p.get("descriptionHtml", "")),
                                   apply_url=f"https://jobs.ashbyhq.com/{ids['company']}/{ids['job_id']}/application")
                        return out
        except Exception as e:  # fall through to HTML
            out["fetch_warning"] = f"api fetch failed: {e}"
        r = c.get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:title")
        out["title"] = out["title"] or (og["content"] if og and og.get("content") else (soup.title.string if soup.title else ""))
        for s in soup(["script", "style", "nav", "footer"]):
            s.decompose()
        out["description"] = _text(str(soup))
    return out
