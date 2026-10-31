"""DB-backed queue worker (no Redis needed). Run standalone: python -m app.worker"""
import datetime as dt
import logging
import threading
import time
from sqlalchemy import update, select, func
from .config import DATA_DIR, DRY_RUN, REQUIRE_REVIEW, MIN_MATCH_SCORE, DAILY_QUOTA
from .db import SessionLocal, Application, Profile, Status, init_db, now
from .fetcher import fetch_job
from .matcher import score
from . import llm
from .pdf import text_to_pdf
from .submitters import SUBMITTERS, SubmitError

log = logging.getLogger("worker")


def _claim(s, frm: Status, to: Status):
    """Atomically move one job frm->to; returns the Application or None."""
    a = s.scalars(select(Application).where(Application.status == frm.value).order_by(Application.id)).first()
    if not a:
        return None
    n = s.execute(update(Application).where(Application.id == a.id, Application.status == frm.value)
                  .values(status=to.value, updated_at=now())).rowcount
    s.commit()
    if not n:
        return None
    s.refresh(a)
    return a


def _note(a, msg):
    a.log = (a.log or []) + [f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"]


def prepare(a, s):
    p = s.get(Profile, 1)
    if not p.resume_text:
        raise SubmitError("PROFILE_INCOMPLETE", "Upload a resume first")
    job = fetch_job(a.url)
    a.ats, a.company, a.title, a.location = job["ats"], job["company"], job["title"], job["location"]
    a.job_description, a.url = job["description"], job["apply_url"]
    m = score(p, a.title, a.job_description)
    a.match_score, a.match_details = m["score"], m
    _note(a, f"fetched JD ({len(a.job_description)} chars), ats={a.ats}, match={m['score']}")
    if m["score"] < MIN_MATCH_SCORE:
        a.status = Status.SKIPPED.value; _note(a, f"below MIN_MATCH_SCORE {MIN_MATCH_SCORE}"); s.commit(); return
    s.commit()
    a.status = Status.OPTIMIZING_RESUME.value; s.commit()
    a.tailored_resume = llm.tailor_resume(p, a.title, a.company, a.job_description, m)
    a.tailored_resume_pdf = text_to_pdf(a.tailored_resume, str(DATA_DIR / "artifacts" / f"resume_app_{a.id}.pdf"))
    _note(a, "resume tailored" + ("" if llm.LLM_API_KEY else " (mock LLM)"))
    a.status = Status.OPTIMIZING_CL.value; s.commit()
    a.cover_letter = llm.cover_letter(p, a.title, a.company, a.job_description, m)
    _note(a, "cover letter written")
    a.status = (Status.REVIEW if REQUIRE_REVIEW else Status.APPROVED).value
    s.commit()


def apply(a, s):
    p = s.get(Profile, 1)
    cls = SUBMITTERS.get(a.ats)
    if not cls:
        raise SubmitError("UNSUPPORTED_ATS", f"No submitter for ATS '{a.ats}'")
    today = s.scalar(select(func.count()).select_from(Application).where(
        Application.status == Status.APPLIED.value,
        Application.updated_at >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)))
    if not DRY_RUN and today >= DAILY_QUOTA:
        raise SubmitError("OUT_OF_APPLICATIONS", "Daily quota reached")
    a.attempts += 1
    res = cls(a, p, a.tailored_resume_pdf or p.resume_path, a.cover_letter, dry_run=DRY_RUN).run()
    a.screenshots = (a.screenshots or []) + res["screenshots"]
    _note(a, f"filled: {', '.join(res['filled'])}")
    a.status = (Status.APPLIED if res["submitted"] else Status.DRY_RUN_DONE).value
    _note(a, "SUBMITTED" if res["submitted"] else "dry-run: form filled, NOT submitted")
    s.commit()


def tick():
    did = False
    for frm, to, fn in ((Status.QUEUED, Status.QUEUED, prepare), (Status.APPROVED, Status.APPLYING, apply)):
        with SessionLocal() as s:
            if frm == Status.QUEUED:
                a = s.scalars(select(Application).where(Application.status == "QUEUED").order_by(Application.id)).first()
                if a:  # move off QUEUED immediately so it isn't picked twice
                    a = _claim(s, Status.QUEUED, Status.OPTIMIZING_RESUME)
            else:
                a = _claim(s, frm, to)
            if not a:
                continue
            did = True
            try:
                fn(a, s)
            except SubmitError as e:
                a.screenshots = (a.screenshots or []) + getattr(e, "shots", [])
                a.status, a.error = Status.FAILED.value, f"{e.code}: {e}"; _note(a, a.error); s.commit()
            except Exception as e:
                log.exception("job %s failed", a.id)
                a.status, a.error = Status.FAILED.value, f"ERROR: {str(e)[:400]}"; _note(a, a.error); s.commit()
    return did


def loop(stop: threading.Event | None = None):
    while not (stop and stop.is_set()):
        if not tick():
            time.sleep(2)


def start_background():
    t = threading.Thread(target=loop, daemon=True); t.start(); return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db(); log.info("worker started (DRY_RUN=%s)", DRY_RUN); loop()
