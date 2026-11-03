"""DB-backed job queue + state machine + scheduler. Runs inside the web process or standalone: python -m app.worker"""
import datetime as dt
import logging
import threading
import time
from sqlalchemy import select, update, func
from .ai import Provider, AIConfig, AIError
from .answerer import Answerer
from .crypto import dec
from .db import SessionLocal, Application, Profile, Settings, Credential, init_db, now
from .discovery import due_saved_searches, run_saved_search
from .fetcher import fetch_job
from .matcher import score
from .pdf import text_to_pdf
from .profile import profile_text, full_name
from .config import DATA_DIR
from .submitters import submitter_for, SubmitError, NeedsManual
import os
APPLY_WORKERS = max(1, int(os.getenv('APPLY_WORKERS', '3')))

log = logging.getLogger("worker")


def note(a, msg):
    a.log = (a.log or []) + [f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"]


def claim(s, frm, to, exclude_users=()):
    q = select(Application).where(Application.status == frm).order_by(Application.id)
    if exclude_users:
        q = q.where(Application.user_id.not_in(exclude_users))
    a = s.scalars(q).first()
    if not a:
        return None
    if not s.execute(update(Application).where(Application.id == a.id, Application.status == frm)
                     .values(status=to, updated_at=now())).rowcount:
        s.commit(); return None
    s.commit(); s.refresh(a); return a


def applied_today(s, user_id):
    since = now() - dt.timedelta(days=1)
    return s.scalar(select(func.count()).select_from(Application).where(
        Application.user_id == user_id, Application.status.in_(["APPLIED", "NEEDS_MANUAL"]), Application.applied_at >= since)) or 0


def prepare(a, s):
    st, prof = s.get(Settings, a.user_id), s.get(Profile, a.user_id)
    if not (prof.resume_text or (prof.data or {}).get("email")):
        raise SubmitError("PROFILE_INCOMPLETE", "Upload a resume and complete your profile first")
    ai = Provider(AIConfig.from_settings(st))
    if not a.job_description or not a.title:
        job = fetch_job(a.url)
        a.ats = job["ats"] if job["ats"] != "unknown" else a.ats
        a.company = a.company or job["company"]; a.title = a.title or job["title"]; a.location = a.location or job["location"]
        a.job_description = job["description"] or a.job_description; a.apply_url = job["apply_url"]
    if a.ats == "unknown":
        from .ats import detect
        a.ats = detect(a.apply_url or a.url)[0]
    m = score(prof, a.title, a.job_description)
    a.match_score, a.match_details = m["score"], m
    try:
        a.match_explanation = ai.explain_match(profile_text(prof), a.title, a.job_description, m)
    except AIError as e:
        a.match_explanation = f"(AI unavailable: {e})"
    note(a, f"Job fetched ({len(a.job_description)} chars). ATS={a.ats}. Match={m['score']}")
    if st.min_match_score and m["score"] < st.min_match_score:
        a.status = "SKIPPED"; note(a, f"Below your minimum match score {st.min_match_score}"); s.commit(); return
    a.status = "OPTIMIZING_RESUME"; s.commit()
    from .docs_engine import generate
    x = st.extra or {}
    warns = generate(s, a, prof, ai, tone=x.get("tone", "professional"), lang=x.get("language", "en"))
    render_docs(a, prof, x.get("theme", "ats"))
    try:  # match after tailoring (the tailored resume only reframes real experience)
        import types
        m2 = score(types.SimpleNamespace(data=prof.data, resume_text=(prof.resume_text or "") + "\n" + (a.tailored_resume or "")),
                   a.title, a.job_description)
        a.match_details = dict(a.match_details or {}, score_after_tailoring=m2["score"])
        note(a, f"Match after tailoring: {m['score']} → {m2['score']}")
    except Exception:
        log.exception("rescore failed")
    covered = sum(1 for r in a.requirements or [] if r["covered"])
    note(a, f"Resume, cover letter and email drafted with {st.ai_provider}; requirements covered {covered}/{len(a.requirements or [])}"
         + (". Check: " + " | ".join(warns) if warns else ""))
    a.status = "OPTIMIZING_CL"; s.commit()
    auto = st.auto_approve_above and m["score"] >= st.auto_approve_above
    if a.auto_apply:
        a.status = "APPROVED"; note(a, "Auto Apply: documents tailored, moving to submission")
    elif not st.require_review or auto:
        a.status = "APPROVED"; note(a, "Auto-approved" + (f" (score >= {st.auto_approve_above})" if auto else ""))
    else:
        a.status = "REVIEW"; note(a, "Waiting for your review")
    s.commit()


def render_docs(a, prof, theme=None):
    """(Re)render the current tailored resume + cover letter PDFs; these are what auto-apply uploads."""
    from .pdf import text_to_pdf, letter_to_pdf
    from .docs_engine import _contact
    th = a.theme or theme or "ats"
    a.tailored_resume_pdf = text_to_pdf(a.tailored_resume, str(_art(a) / "tailored_resume.pdf"), th)
    letter_to_pdf(a.cover_letter, str(_art(a) / "cover_letter.pdf"), full_name(prof), _contact(prof), th)


def _art(a):
    p = DATA_DIR / "artifacts" / f"u{a.user_id}" / f"app_{a.id}"; p.mkdir(parents=True, exist_ok=True); return p


def apply(a, s):
    st, prof = s.get(Settings, a.user_id), s.get(Profile, a.user_id)
    from .ats import detect, resolve, AGGREGATORS, host
    target = a.apply_url or a.url
    if a.ats in ("unknown", "greenhouse_short") or a.ats in AGGREGATORS or not a.apply_url:
        real = resolve(target)
        if real != target:
            note(a, f"Resolved apply link → {real}")
        target = real
    ats, _ = detect(target)
    if ats in AGGREGATORS:
        a.apply_url = target
        raise NeedsManual("AGGREGATOR_EASY_APPLY", f"{host(target)} only allows applying inside its own signed-in site "
                          "(Easy Apply) and gives no employer link. Open the link and apply there; your tailored resume is ready.")
    a.apply_url, a.ats = target, ats
    cls = submitter_for(ats)
    creds = {c.site: (c.username, dec(c.password_enc)) for c in s.scalars(select(Credential).where(Credential.user_id == a.user_id))}
    ai = Provider(AIConfig.from_settings(st))
    ans = Answerer(prof, ai, a.cover_letter)
    a.attempts += 1
    # With the offline mock provider the "tailored" text is just a header + raw text, so send the original resume file.
    if a.tailored_resume:
        render_docs(a, prof, (st.extra or {}).get("theme"))  # upload the latest version of the drafts
    resume = a.tailored_resume_pdf or prof.resume_path
    try:
        res = cls(a, prof, ans, resume, creds, dry_run=st.dry_run).run()
    finally:
        a.answers_used = ans.used
        try:
            from .profile import add_unknown_questions
            src = f"{a.company} – {a.title}"
            n = add_unknown_questions(prof, [dict(u, source=src) for u in ans.unknown])
            if n:
                note(a, f"Saved {n} new question(s) to your Application Questions → answer them once at /questions")
        except Exception:
            log.exception("saving unknown questions failed")
    a.screenshots = (a.screenshots or []) + res["screenshots"]
    note(a, "Filled: " + ", ".join(res["filled"]))
    if res.get("missing"):
        note(a, "Required fields still blank: " + " | ".join(res["missing"]))
    if res["submitted"]:
        a.status, a.applied_at = "APPLIED", now()
        note(a, "SUBMITTED" + ("" if res["confirmed"] else " (no confirmation text detected, check screenshot)"))
    else:
        a.status = "DRY_RUN_DONE"; note(a, "Dry run: form filled and screenshotted, NOT submitted")
    s.commit()


def _run(fn, a, s):
    _run_inner(fn, a, s)
    a = s.get(Application, a.id)
    # NEEDS_MANUAL stays as-is: an honest "needs you" with the exact reason and an Open & prefill button.


def _run_inner(fn, a, s):
    try:
        fn(a, s)
    except NeedsManual as e:
        a.screenshots = (a.screenshots or []) + e.shots
        a.status, a.error = "NEEDS_MANUAL", f"{e.code}: {e} → {a.apply_url or a.url}"
        note(a, a.error); s.commit()
    except SubmitError as e:
        a.screenshots = (a.screenshots or []) + e.shots
        a.status, a.error = "FAILED", f"{e.code}: {e}"; note(a, a.error); s.commit()
    except Exception as e:
        log.exception("application %s failed", a.id)
        s.rollback(); a = s.get(Application, a.id)
        a.status, a.error = "FAILED", f"ERROR: {str(e)[:400]}"; note(a, a.error); s.commit()


def tick():
    did = False
    with SessionLocal() as s:
        a = claim(s, "QUEUED", "OPTIMIZING_RESUME")
        if a:
            did = True; _run(prepare, a, s)
    with SessionLocal() as s:
        over = [u for (u, lim) in s.execute(select(Settings.user_id, Settings.daily_limit)).all()
                if not s.get(Settings, u).dry_run and applied_today(s, u) >= lim]
        a = claim(s, "APPROVED", "APPLYING", over)
        if a:
            did = True; _run(apply, a, s)
    return did


_last_sched = 0.0


def schedule_tick():
    global _last_sched
    if time.time() - _last_sched < 60:
        return
    _last_sched = time.time()
    for ss_id in due_saved_searches():
        try:
            n, errs = run_saved_search(ss_id); log.info("saved search %s: %s new, errors=%s", ss_id, n, errs)
        except Exception:
            log.exception("saved search %s failed", ss_id)


def loop(stop=None):
    while not (stop and stop.is_set()):
        try:
            schedule_tick()
            if not tick():
                time.sleep(2)
        except Exception:
            log.exception("worker loop error"); time.sleep(5)


def apply_loop(stop=None):
    """Extra submit-only workers: claim APPROVED jobs in parallel (claim() is atomic, so no double-apply)."""
    while not (stop and stop.is_set()):
        try:
            with SessionLocal() as s:
                over = [u for (u, lim) in s.execute(select(Settings.user_id, Settings.daily_limit)).all()
                        if not s.get(Settings, u).dry_run and applied_today(s, u) >= lim]
                a = claim(s, "APPROVED", "APPLYING", over)
                if a:
                    _run(apply, a, s); continue
            time.sleep(2)
        except Exception:
            log.exception("apply loop error"); time.sleep(5)


def start_background():
    t = threading.Thread(target=loop, daemon=True, name="worker"); t.start()
    for i in range(APPLY_WORKERS - 1):
        threading.Thread(target=apply_loop, daemon=True, name=f"apply-{i + 1}").start()
    return t


if __name__ == "__main__":
    from .hardening import setup_logging
    from .config import LOG_LEVEL, LOG_FORMAT
    setup_logging(LOG_LEVEL, LOG_FORMAT)
    init_db(); log.info("worker started"); loop()


def failure_stats(s, user_id):
    """Counts of non-submitted outcomes by reason code and by ATS host, for the /monitor page."""
    from collections import Counter
    from .ats import host
    rows = s.execute(select(Application.status, Application.error, Application.apply_url, Application.url, Application.ats)
                     .where(Application.user_id == user_id)).all()
    by_status, by_reason, by_host = Counter(), Counter(), Counter()
    for st, err, au, u, ats in rows:
        by_status[st] += 1
        if st in ("FAILED", "NEEDS_MANUAL"):
            code = (err or "UNKNOWN").split(":")[0].strip()[:40] or "UNKNOWN"
            by_reason[code] += 1; by_host[f"{host(au or u)} ({ats})"] += 1
    return {"by_status": by_status.most_common(), "by_reason": by_reason.most_common(), "by_host": by_host.most_common(30)}


def prefill_visible(app_id):
    """Self-hosted only: open a VISIBLE browser on the real form, fill everything, and leave it for the user to
    review/solve CAPTCHA/submit. Never clicks submit."""
    def _go():
        from playwright.sync_api import sync_playwright
        from .submitters.universal import UniversalSubmitter
        with SessionLocal() as s:
            a = s.get(Application, app_id); st, prof = s.get(Settings, a.user_id), s.get(Profile, a.user_id)
            ans = Answerer(prof, Provider(AIConfig.from_settings(st)), a.cover_letter)
            sub = submitter_for(a.ats)(a, prof, ans, a.tailored_resume_pdf or prof.resume_path,
                                       {c.site: (c.username, dec(c.password_enc)) for c in
                                        s.scalars(select(Credential).where(Credential.user_id == a.user_id))}, dry_run=True)
            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=False); page = b.new_page()
                try:
                    sub.open(page)
                    try:
                        sub.fill_form(page)
                    except SubmitError:
                        pass
                    UniversalSubmitter.fill_standard(sub, page) if hasattr(sub, "fill_standard") else None
                    sub.fill_generic(page)
                except Exception:
                    log.exception("prefill")
                page.wait_for_event("close", timeout=0)
                b.close()
    threading.Thread(target=_go, daemon=True).start()
