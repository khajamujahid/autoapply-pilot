"""Scan ~10,000 recently active company career sites (Greenhouse, Lever, Ashby, Workable, Recruitee public job-board APIs) for a user's roles.

The board list (app/data/career_boards.json) comes from an open directory of boards confirmed live against each
vendor's public API, ordered by open roles. Scans run in parallel with per-request timeouts, a global time budget,
an in-memory cache (so the next batch / next user is fast) and resume from an offset, so hosted (60s functions)
can cover the list in batches while self-hosted installs scan everything in one pass.
"""
import concurrent.futures as cf
import datetime as dt
import json
import threading
import time
from pathlib import Path

import httpx

from .fetcher import UA

_FILE = Path(__file__).parent / "data" / "career_boards.json"
_CACHE: dict = {}          # (ats, token) -> (fetched_at, [jobs])
_LOCK = threading.Lock()
CACHE_TTL = 1800
_BOARDS = None
ATS = ("greenhouse", "lever", "ashby", "workable", "recruitee")


def boards():
    """All boards, biggest employers first, interleaving ATS so every batch mixes Greenhouse/Lever/Ashby."""
    global _BOARDS
    if _BOARDS is None:
        d = json.loads(_FILE.read_text())
        rows = [(ats, r[0], r[1], r[2]) for ats in ATS for r in d.get(ats, [])]
        rows.sort(key=lambda r: -r[3])
        _BOARDS = rows
    return _BOARDS


def board_count():
    return len(boards())


def _date(v):
    try:
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).date()
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except Exception:
        return None


def _fetch(c, ats, token, name):
    """Light listing call per board (titles, locations, dates, URLs). Descriptions are fetched later per job."""
    key = (ats, token)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    out = []
    if ats == "greenhouse":
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"); r.raise_for_status()
        for j in r.json().get("jobs", []):
            out.append({"url": j.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{j['id']}",
                        "title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
                        "posted": _date(j.get("updated_at") or j.get("first_published")), "ats": "greenhouse"})
    elif ats == "lever":
        r = c.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}); r.raise_for_status()
        for j in r.json():
            cat = j.get("categories") or {}
            out.append({"url": j.get("hostedUrl", ""), "title": j.get("text", ""), "location": cat.get("location", ""),
                        "posted": _date(j.get("createdAt")), "ats": "lever", "workplace": j.get("workplaceType", "")})
    elif ats == "workable":
        r = c.get(f"https://apply.workable.com/api/v1/widget/accounts/{token}"); r.raise_for_status()
        for j in r.json().get("jobs", []):
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x) + (" Remote" if j.get("telecommuting") else "")
            out.append({"url": j.get("url", ""), "title": j.get("title", ""), "location": loc.strip(),
                        "posted": _date(j.get("published_on") or j.get("created_at")), "ats": "workable"})
    elif ats == "recruitee":
        r = c.get(f"https://{token}.recruitee.com/api/offers/"); r.raise_for_status()
        for j in r.json().get("offers", []):
            loc = (j.get("location") or "") + (" Remote" if j.get("remote") else "")
            out.append({"url": j.get("careers_url", ""), "title": j.get("title", ""), "location": loc.strip(),
                        "posted": _date(str(j.get("published_at") or "").replace(" UTC", "").replace(" ", "T")), "ats": "recruitee"})
    else:
        r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}"); r.raise_for_status()
        for j in r.json().get("jobs", []):
            if j.get("isListed") is False:
                continue
            loc = j.get("location", "") + (" Remote" if j.get("isRemote") else "")
            out.append({"url": j.get("jobUrl", ""), "title": j.get("title", ""), "location": loc.strip(),
                        "posted": _date(j.get("publishedAt")), "ats": "ashby"})
    for j in out:
        j["company"] = name
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def scan(match, offset=0, limit=None, budget_s=40.0, workers=int(__import__("os").getenv("CAREER_SITES_WORKERS", "48")), timeout=8.0):
    """Query boards[offset:offset+limit] in parallel until the time budget runs out.
    match(job) -> bool filters by role/location/date. Returns (jobs, meta)."""
    allb = boards()
    if budget_s <= 0:
        return [], {"total": len(allb), "offset": offset, "scanned": 0, "next_offset": offset, "complete": False,
                    "boards_with_matches": 0, "errors": 0, "seconds": 0, "disabled": True}
    todo = allb[offset: offset + limit if limit else None]
    t0 = time.time(); found, done, errors, with_hits = [], 0, 0, 0
    ex = cf.ThreadPoolExecutor(workers)
    try:
        with httpx.Client(headers=UA, timeout=timeout, follow_redirects=True,
                          limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers)) as c:
            futs, sub = {}, [0]
            it = iter(enumerate(todo))
            def submit_next():
                try:
                    i, (ats, tok, name, _) = next(it)
                except StopIteration:
                    return False
                futs[ex.submit(_fetch, c, ats, tok, name)] = i; sub[0] = i + 1
                return True
            for _ in range(workers * 2):
                if not submit_next():
                    break
            while futs:
                left = budget_s - (time.time() - t0)
                if left <= 0:
                    break
                ready, _ = cf.wait(list(futs), timeout=min(left, 1.0), return_when=cf.FIRST_COMPLETED)
                for f in ready:
                    futs.pop(f); done += 1
                    try:
                        hits = [dict(j, source="career_sites", description="") for j in f.result() if match(j)]
                    except Exception:
                        errors += 1; hits = []
                    if hits:
                        with_hits += 1; found += hits
                    if time.time() - t0 < budget_s:
                        submit_next()
            for f in futs:
                f.cancel()
            pending = sorted(futs.values())
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    # resume point: the first board we did not finish (everything before it is done)
    next_off = offset + (pending[0] if pending else sub[0])
    next_off = min(next_off, len(allb))
    return found, {"total": len(allb), "offset": offset, "scanned": done, "next_offset": next_off,
                   "complete": next_off >= len(allb), "boards_with_matches": with_hits, "errors": errors,
                   "seconds": round(time.time() - t0, 1)}
