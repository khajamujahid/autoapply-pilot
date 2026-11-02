import logging
import re
import csv
import io
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_
from starlette.middleware.sessions import SessionMiddleware

from .ai import PROVIDERS, Provider, AIConfig, AIError
from .ats import detect, SUPPORTED
from .matcher import score
from .db import now
from .auth import current_user, LoginRequired, pwd, oauth, valid_email, create_user
from .config import SECRET_KEY, DATA_DIR, RUN_WORKER_IN_PROCESS, ALLOW_SIGNUP, APP_NAME, HOSTED, BASE_URL, LOG_LEVEL, LOG_FORMAT, MAX_UPLOAD_MB
from .hardening import setup_logging, RequestMiddleware, LoginThrottle
from .crypto import enc, dec
from .db import (SessionLocal, init_db, User, Settings, Profile, Credential, SavedSearch, Application, STATUSES, status_label,
                 ensure_user_rows)
from .discovery import search as discover, save_jobs, run_saved_search
from .profile import FIELDS, SIMPLE_FIELDS, ANSWER_FIELDS, QUESTION_SECTIONS, extract_text

HERE = Path(__file__).parent
setup_logging(LOG_LEVEL, LOG_FORMAT)
log = logging.getLogger("app")
app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
throttle = LoginThrottle()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=60 * 60 * 24 * 14,
                   https_only=BASE_URL.startswith("https"), session_cookie="applypilot_session")
app.add_middleware(RequestMiddleware, base_url=BASE_URL)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
tpl.env.globals.update(status_label=status_label, HOSTED=HOSTED, APP_NAME=APP_NAME, STATUSES=STATUSES, google_enabled=oauth is not None)
init_db()
if RUN_WORKER_IN_PROCESS:
    from .worker import start_background
    start_background()


@app.exception_handler(LoginRequired)
def _login_required(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "login required"}, 401)
    return RedirectResponse("/login", 303)


def render(request, name, user=None, **ctx):
    flash = request.session.pop("flash", None)
    return tpl.TemplateResponse(request, name, {"user": user, "flash": flash, "path": request.url.path, **ctx})


def flash(request, msg, kind="ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


HOSTED_NOTE = ("Hosted demo: browser auto-apply runs only in the self-hosted app. "
               "Download your tailored PDFs, open the apply link, then click Mark applied.")


def hosted_followup(s, a):
    """On serverless hosts there is no background worker: draft documents inline, never auto-submit."""
    if not HOSTED:
        return
    if a.status == "QUEUED":
        from .worker import _run, prepare
        _run(prepare, a, s); a = s.get(Application, a.id)
    if a.status == "APPROVED":
        from .worker import note
        a.status, a.error = "FAILED", HOSTED_NOTE; note(a, HOSTED_NOTE); s.commit()


def own_app(s, user, app_id) -> Application:
    a = s.get(Application, app_id)
    if not a or a.user_id != user.id:  # strict per-user isolation
        raise HTTPException(404)
    return a


# ---------------------------------------------------------------- auth
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", mode="login", allow_signup=ALLOW_SIGNUP)


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return render(request, "login.html", mode="signup", allow_signup=ALLOW_SIGNUP)


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form("")):
    if not ALLOW_SIGNUP:
        raise HTTPException(403, "Sign-up disabled")
    email = email.lower().strip()
    if not valid_email(email) or len(password) < 8:
        flash(request, "Enter a valid email and a password of at least 8 characters.", "err"); return RedirectResponse("/signup", 303)
    with SessionLocal() as s:
        if s.scalar(select(User).where(User.email == email)):
            flash(request, "That email already has an account. Sign in instead.", "err"); return RedirectResponse("/login", 303)
    u = create_user(email, password, name)
    request.session["uid"] = u.id
    flash(request, "Welcome! Start by uploading your resume.")
    return RedirectResponse("/profile", 303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    key = f"{request.client.host if request.client else '-'}|{email.lower().strip()}"
    if throttle.blocked(key):
        flash(request, "Too many failed sign-in attempts. Try again in 10 minutes.", "err"); return RedirectResponse("/login", 303)
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email.lower().strip()))
    if not u or not u.password_hash or not pwd.verify(password, u.password_hash):
        throttle.fail(key); log.info("login failed")
        flash(request, "Wrong email or password.", "err"); return RedirectResponse("/login", 303)
    throttle.reset(key)
    request.session.clear()
    request.session["uid"] = u.id
    return RedirectResponse("/", 303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear(); return RedirectResponse("/login", 303)


@app.get("/auth/google")
async def google_login(request: Request):
    if not oauth:
        raise HTTPException(404, "Google sign-in is not configured")
    return await oauth.google.authorize_redirect(request, f"{BASE_URL}/auth/google/callback")


@app.get("/auth/google/callback")
async def google_cb(request: Request):
    if not oauth:
        raise HTTPException(404)
    token = await oauth.google.authorize_access_token(request)
    info = token.get("userinfo") or {}
    if not info.get("email") or not info.get("email_verified", True):
        flash(request, "Google account email is not verified.", "err"); return RedirectResponse("/login", 303)
    with SessionLocal() as s:
        u = s.scalar(select(User).where(or_(User.google_sub == info["sub"], User.email == info["email"].lower())))
        if u and not u.google_sub:
            u.google_sub = info["sub"]; s.commit()
    if not u:
        if not ALLOW_SIGNUP:
            raise HTTPException(403, "Sign-up disabled")
        u = create_user(info["email"], "", info.get("name", ""), info["sub"])
    request.session["uid"] = u.id
    return RedirectResponse("/", 303)


# ---------------------------------------------------------------- dashboard / tracker
AUTO_PENDING = ("QUEUED", "OPTIMIZING_RESUME", "OPTIMIZING_CL", "REVIEW", "APPROVED", "APPLYING")
VIEWS = {"recommended": ("DISCOVERED",), "reviewing": ("QUEUED", "OPTIMIZING_RESUME", "OPTIMIZING_CL", "REVIEW"),
         "applying": ("APPROVED", "APPLYING"), "applied": ("APPLIED", "INTERVIEW", "OFFER"),
         "failed": ("FAILED", "NEEDS_MANUAL", "DRY_RUN_DONE")}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = "", loc: str = "", remote: bool = False, min_score: int = 0, view: str = "recommended",
         ats: str = "", sort: str = "match"):
    """Main post-login view: filters on top, job cards, pipeline stats and one-click Auto Apply."""
    user = current_user(request)
    view = view if view in VIEWS else "recommended"
    with SessionLocal() as s:
        counts = dict(s.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                                .group_by(Application.status)).all())
        qy = select(Application).where(Application.user_id == user.id, Application.status.in_(VIEWS[view]))
        if q:
            like = f"%{q}%"; qy = qy.where(or_(Application.title.ilike(like), Application.company.ilike(like)))
        if loc:
            qy = qy.where(Application.location.ilike(f"%{loc}%"))
        if remote:
            qy = qy.where(or_(Application.location.ilike("%remote%"), Application.title.ilike("%remote%")))
        if min_score:
            qy = qy.where(Application.match_score >= min_score)
        if ats == "auto":
            qy = qy.where(Application.ats.in_(("greenhouse", "lever", "ashby")))
        order = Application.updated_at.desc() if sort == "new" else Application.match_score.desc()
        jobs = list(s.scalars(qy.order_by(order).limit(60)))
        st = s.get(Settings, user.id); prof = s.get(Profile, user.id)
    c = lambda ks: sum(counts.get(k, 0) for k in ks)
    stats = {k: c(v) for k, v in VIEWS.items()}
    setup = {"resume": bool(prof.resume_text), "ai": st.ai_provider != "mock"}
    return render(request, "home.html", user, jobs=jobs, stats=stats, view=view, f=dict(q=q, loc=loc, remote=remote,
                  min_score=min_score, ats=ats, sort=sort), st=st, setup=setup, label=status_label,
                  ai_label=PROVIDERS.get(st.ai_provider, {}).get("label", st.ai_provider))


def _queue_state(s, user_id):
    rows = s.execute(select(Application.id, Application.status, Application.error).where(
        Application.user_id == user_id, Application.auto_apply.is_(True))).all()
    return {"jobs": {r[0]: {"status": status_label(r[1]), "raw": r[1], "error": (r[2] or "")[:200]} for r in rows},
            "pending": sum(1 for r in rows if r[1] in AUTO_PENDING)}


@app.post("/api/autoapply")
async def api_autoapply(request: Request):
    """Queue every job currently shown. Each one is tailored into the ATS resume, answered and submitted in order."""
    user = current_user(request)
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])][:100]
    n = 0
    with SessionLocal() as s:
        prof = s.get(Profile, user.id)
        if not prof.resume_text:
            return JSONResponse({"error": "Upload your resume on the Profile page first, then click Auto Apply."}, 400)
        for a in s.scalars(select(Application).where(Application.user_id == user.id, Application.id.in_(ids))):
            if a.status in ("DISCOVERED", "SKIPPED", "FAILED", "NEEDS_MANUAL", "DRY_RUN_DONE", "REVIEW"):
                a.status, a.error, a.auto_apply = ("APPROVED" if a.status == "REVIEW" and a.tailored_resume else "QUEUED"), "", True
                n += 1
        s.commit()
        return {"queued": n, **_queue_state(s, user.id)}


def _claim_one(s, user_id, frm, to):
    from sqlalchemy import update
    a = s.scalars(select(Application).where(Application.user_id == user_id, Application.auto_apply.is_(True),
                                            Application.status == frm).order_by(Application.id).limit(1)).first()
    if a and s.execute(update(Application).where(Application.id == a.id, Application.status == frm)
                       .values(status=to)).rowcount:
        s.commit(); s.refresh(a); return a
    return None


@app.post("/api/queue/step")
def api_queue_step(request: Request):
    """Advance this user's Auto Apply queue by one job (the browser calls this in a loop; the worker does the same
    in the background on self-hosted installs). Hosted demo: tailoring runs, browser submission is reported honestly."""
    user = current_user(request)
    from .worker import _run, prepare, apply as submit, note
    with SessionLocal() as s:
        a = _claim_one(s, user.id, "QUEUED", "OPTIMIZING_RESUME")
        if a:
            _run(prepare, a, s)
        else:
            a = _claim_one(s, user.id, "APPROVED", "APPLYING")
            if a:
                if HOSTED:
                    a.status = "FAILED"
                    a.error = ("Not submitted: the hosted demo has no browser to fill forms. Your tailored ATS resume and "
                               "cover letter are ready on this job; self-host ApplyPilot to submit automatically, or apply via the link.")
                    note(a, a.error); s.commit()
                else:
                    _run(submit, a, s)
        return _queue_state(s, user.id)


@app.get("/api/queue")
def api_queue(request: Request):
    user = current_user(request)
    with SessionLocal() as s:
        return _queue_state(s, user.id)


@app.get("/profile/ats.pdf")
def profile_ats_pdf(request: Request):
    """The uploaded resume converted into the standard ATS layout (before any per-job tailoring)."""
    user = current_user(request)
    from .docs_engine import resume_builtin
    from .pdf import text_to_pdf
    with SessionLocal() as s:
        prof = s.get(Profile, user.id)
        if not prof.resume_text:
            raise HTTPException(404, "Upload a resume first")
        txt = resume_builtin(prof, (prof.data or {}).get("current_title", ""), "", [])
    d = DATA_DIR / "artifacts" / f"u{user.id}"; d.mkdir(parents=True, exist_ok=True)
    return FileResponse(text_to_pdf(txt, str(d / "resume_ats.pdf"), "ats"), filename="Resume_ATS.pdf", media_type="application/pdf")


@app.get("/tracker", response_class=HTMLResponse)
def dashboard(request: Request, status: str = "", q: str = "", ats: str = "", view: str = "board"):
    user = current_user(request)
    with SessionLocal() as s:
        counts = dict(s.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                                .group_by(Application.status)).all())
        qy = select(Application).where(Application.user_id == user.id)
        if status:
            qy = qy.where(Application.status == status)
        else:
            qy = qy.where(Application.status != "DISCOVERED")
        if ats:
            qy = qy.where(Application.ats == ats)
        if q:
            like = f"%{q}%"; qy = qy.where(or_(Application.title.ilike(like), Application.company.ilike(like)))
        apps = list(s.scalars(qy.order_by(Application.updated_at.desc()).limit(500)))
        st = s.get(Settings, user.id); prof = s.get(Profile, user.id)
    setup = {"resume": bool(prof.resume_text), "ai": st.ai_provider != "mock"}
    stage = lambda x: ("Applied" if x == "APPLIED" else "Interview" if x == "INTERVIEW" else "Offer" if x == "OFFER" else
                       "Closed" if x in ("REJECTED", "WITHDRAWN", "SKIPPED") else "Draft")
    board = {k: [a for a in apps if stage(a.status) == k] for k in ("Draft", "Applied", "Interview", "Offer", "Closed")}
    c = lambda *ks: sum(counts.get(k, 0) for k in ks)
    after_applied = ("APPLIED", "INTERVIEW", "OFFER")
    funnel = [("Drafted", c(*[x for x in STATUSES if x != "DISCOVERED"])), ("Applied", c(*after_applied) + c("REJECTED")),
              ("Interview", c("INTERVIEW", "OFFER")), ("Offer", c("OFFER"))]
    return render(request, "dashboard.html", user, apps=apps, counts=counts, f_status=status, f_q=q, f_ats=ats, st=st, setup=setup,
                  view=view, board=board, funnel=funnel)


@app.post("/applications/add")
def add_urls(request: Request, urls: str = Form(...)):
    user = current_user(request)
    added, dup = 0, 0
    with SessionLocal() as s:
        for u in [x.strip() for x in urls.splitlines() if x.strip().startswith("http")]:
            a = s.scalar(select(Application).where(Application.user_id == user.id, Application.url == u))
            if a:
                if a.status == "DISCOVERED":
                    a.status = "QUEUED"; added += 1
                else:
                    dup += 1
                continue
            s.add(Application(user_id=user.id, url=u, apply_url=u, ats=detect(u)[0], status="QUEUED")); added += 1
        s.commit()
    flash(request, f"Queued {added} job(s)." + (f" {dup} already tracked." if dup else ""))
    return RedirectResponse("/", 303)


@app.get("/applications/{app_id}", response_class=HTMLResponse)
def app_detail(request: Request, app_id: int):
    user = current_user(request)
    from .docs_engine import versions, LANGS, TONES
    from .pdf import THEME_LABELS
    from .db import ChatMsg
    with SessionLocal() as s:
        a = own_app(s, user, app_id)
        st = s.get(Settings, user.id)
        vers = {k: versions(s, a.id, k) for k in ("resume", "cover", "email")}
        chat = list(s.scalars(select(ChatMsg).where(ChatMsg.app_id == a.id).order_by(ChatMsg.id)))
    x = st.extra or {}
    return render(request, "detail.html", user, a=a, shots=[Path(p).name for p in a.screenshots or []], vers=vers, chat=chat,
                  THEMES=THEME_LABELS, LANGS=LANGS, TONES=TONES, theme=a.theme or x.get("theme", "ats"),
                  lang=x.get("language", "en"), tone=x.get("tone", "professional"), tab=request.query_params.get("tab", "match"))


def _ai_for(s, user_id):
    st = s.get(Settings, user_id)
    return Provider(AIConfig.from_settings(st)), (st.extra or {})


@app.post("/applications/{app_id}/draft")
def app_draft(request: Request, app_id: int, tone: str = Form("professional"), language: str = Form("en"),
              rematch: bool = Form(False)):
    """Build the match view + tailored resume, cover letter and email right now (no auto-apply)."""
    user = current_user(request)
    from .docs_engine import generate
    from .worker import render_docs
    from .fetcher import fetch_job
    with SessionLocal() as s:
        a = own_app(s, user, app_id); prof = s.get(Profile, user.id); ai, x = _ai_for(s, user.id)
        if not a.job_description and a.url.startswith("http"):
            try:
                j = fetch_job(a.url); a.title = a.title or j["title"]; a.company = a.company or j["company"]
                a.job_description = j["description"]
            except Exception as e:
                flash(request, f"Could not fetch the posting: {e}", "err")
        if rematch:
            a.requirements = []
        warns = generate(s, a, prof, ai, tone=tone, lang=language)
        render_docs(a, prof, x.get("theme"))
        if a.status in ("DISCOVERED", "SKIPPED"):
            a.status = "REVIEW"
        s.commit()
    flash(request, "Drafts ready." + (" " + " | ".join(warns) if warns else ""), "ok")
    return RedirectResponse(f"/applications/{app_id}?tab=docs", 303)


@app.post("/applications/{app_id}/chat")
def app_chat(request: Request, app_id: int, kind: str = Form("resume"), message: str = Form(...)):
    user = current_user(request)
    from .docs_engine import refine
    from .worker import render_docs
    kind = kind if kind in ("resume", "cover", "email") else "resume"
    with SessionLocal() as s:
        a = own_app(s, user, app_id); prof = s.get(Profile, user.id); ai, x = _ai_for(s, user.id)
        reply = refine(s, a, prof, ai, kind, message, x.get("language", "en"))
        render_docs(a, prof, x.get("theme")); s.commit()
    flash(request, reply)
    return RedirectResponse(f"/applications/{app_id}?tab=docs&kind={kind}", 303)


@app.post("/applications/{app_id}/version/{kind}/{op}")
def app_version(request: Request, app_id: int, kind: str, op: str, vid: int = Form(0)):
    user = current_user(request)
    from .docs_engine import undo, restore
    from .worker import render_docs
    with SessionLocal() as s:
        a = own_app(s, user, app_id); prof = s.get(Profile, user.id); _, x = _ai_for(s, user.id)
        ok = undo(s, a, kind) if op == "undo" else restore(s, a, kind, vid)
        render_docs(a, prof, x.get("theme")); s.commit()
    flash(request, ("Undone." if op == "undo" else "Version restored.") if ok else "Nothing to undo.", "ok" if ok else "err")
    return RedirectResponse(f"/applications/{app_id}?tab=docs&kind={kind}", 303)


@app.post("/applications/{app_id}/theme")
def app_theme(request: Request, app_id: int, theme: str = Form("ats")):
    user = current_user(request)
    from .worker import render_docs
    with SessionLocal() as s:
        a = own_app(s, user, app_id); prof = s.get(Profile, user.id)
        a.theme = theme; render_docs(a, prof, theme); s.commit()
    return RedirectResponse(f"/applications/{app_id}?tab=docs", 303)


@app.get("/applications/{app_id}/pdf/{kind}")
def app_pdf(request: Request, app_id: int, kind: str, theme: str = ""):
    user = current_user(request)
    from .pdf import text_to_pdf, letter_to_pdf
    from .docs_engine import _contact
    from .worker import _art
    from .profile import full_name
    with SessionLocal() as s:
        a = own_app(s, user, app_id); prof = s.get(Profile, user.id); _, x = _ai_for(s, user.id)
    th = theme or a.theme or x.get("theme", "ats")
    slug = re.sub(r"\W+", "_", f"{full_name(prof)}_{a.company}")[:60]
    if kind == "resume":
        p = text_to_pdf(a.tailored_resume or "", str(_art(a) / f"resume_{th}.pdf"), th); fn = f"{slug}_Resume.pdf"
    else:
        p = letter_to_pdf(a.cover_letter or "", str(_art(a) / f"cover_{th}.pdf"), full_name(prof), _contact(prof), th)
        fn = f"{slug}_Cover_Letter.pdf"
    return FileResponse(p, filename=fn, media_type="application/pdf")


@app.post("/applications/paste")
def app_paste(request: Request, title: str = Form(...), company: str = Form(""), url: str = Form(""), description: str = Form(...)):
    """Paste a posting's text: create a draft application without auto-apply."""
    user = current_user(request)
    with SessionLocal() as s:
        a = Application(user_id=user.id, url=url.strip() or f"manual:{now().timestamp()}", apply_url=url.strip(), source="pasted",
                        ats=detect(url)[0] if url.strip() else "unknown", title=title.strip(), company=company.strip(),
                        job_description=description.strip(), status="REVIEW")
        prof = s.get(Profile, user.id)
        m = score(prof, a.title, a.job_description); a.match_score, a.match_details = m["score"], m
        s.add(a); s.commit(); aid = a.id
        x = s.get(Settings, user.id).extra or {}
    return app_draft(request, aid, tone=x.get("tone", "professional"), language=x.get("language", "en"), rematch=False)


@app.post("/applications/{app_id}/action")
def app_action(request: Request, app_id: int, action: str = Form(...), tailored_resume: str = Form(None),
               cover_letter: str = Form(None)):
    user = current_user(request)
    from .pdf import text_to_pdf
    with SessionLocal() as s:
        a = own_app(s, user, app_id)
        if tailored_resume is not None and tailored_resume != a.tailored_resume:
            from .docs_engine import save_version
            save_version(s, a, "resume", tailored_resume, "manual edit")
            if a.tailored_resume_pdf:
                text_to_pdf(a.tailored_resume, a.tailored_resume_pdf, a.theme or "ats")
        if cover_letter is not None and cover_letter != a.cover_letter:
            from .docs_engine import save_version
            save_version(s, a, "cover", cover_letter, "manual edit")
        if action == "approve" and a.status == "REVIEW":
            a.status = "APPROVED"
        elif action == "queue" and a.status in ("DISCOVERED", "SKIPPED"):
            a.status = "QUEUED"
        elif action == "retry":
            a.status, a.error = ("APPROVED" if a.tailored_resume else "QUEUED"), ""
        elif action == "reject":
            a.status = "SKIPPED"
        elif action in ("INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "APPLIED"):
            from .db import now
            a.status = action
            if action == "APPLIED" and not a.applied_at:
                a.applied_at = now()
        elif action == "mark_applied":
            from .db import now
            a.status, a.applied_at = "APPLIED", now()
        elif action == "delete":
            s.delete(a); s.commit(); return RedirectResponse("/", 303)
        s.commit()
        hosted_followup(s, a)
    return RedirectResponse(request.headers.get("referer") or f"/applications/{app_id}", 303)


@app.post("/applications/bulk")
def bulk(request: Request, action: str = Form(...), ids: list[int] = Form([])):
    user = current_user(request)
    with SessionLocal() as s:
        if HOSTED and action == "queue" and len(ids) > 3:
            flash(request, "Hosted demo drafts up to 3 jobs at a time; the first 3 were processed.", "err")
            ids = ids[:3]
        for i in ids:
            a = s.get(Application, i)
            if not a or a.user_id != user.id:
                continue
            if action == "approve" and a.status == "REVIEW":
                a.status = "APPROVED"
            elif action == "queue" and a.status in ("DISCOVERED", "SKIPPED"):
                a.status = "QUEUED"
            elif action == "delete":
                s.delete(a)
        s.commit()
        for i in ids:
            a = s.get(Application, i)
            if a and a.user_id == user.id:
                hosted_followup(s, a)
    return RedirectResponse(request.headers.get("referer") or "/", 303)


@app.get("/applications/{app_id}/file/{name}")
def app_file(request: Request, app_id: int, name: str):
    user = current_user(request)
    with SessionLocal() as s:
        a = own_app(s, user, app_id)
    base = (DATA_DIR / "artifacts" / f"u{user.id}" / f"app_{a.id}").resolve()
    p = (base / Path(name).name).resolve()
    if not str(p).startswith(str(base)) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/export.csv")
def export_csv(request: Request):
    user = current_user(request)
    cols = ["id", "status", "company", "title", "location", "ats", "source", "match_score", "url", "apply_url",
            "error", "created_at", "applied_at"]
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    with SessionLocal() as s:
        for a in s.scalars(select(Application).where(Application.user_id == user.id).order_by(Application.id)):
            w.writerow([getattr(a, c) for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=applications.csv"})


@app.get("/api/counts")
def api_counts(request: Request):
    user = current_user(request)
    with SessionLocal() as s:
        return dict(s.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                              .group_by(Application.status)).all())


# ---------------------------------------------------------------- discovery
@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, run: int = 0):
    user = current_user(request)
    from .roles import builtin_profile
    with SessionLocal() as s:
        searches = list(s.scalars(select(SavedSearch).where(SavedSearch.user_id == user.id)))
        found = list(s.scalars(select(Application).where(Application.user_id == user.id, Application.status == "DISCOVERED")
                               .order_by(Application.match_score.desc()).limit(300)))
        sp = builtin_profile(s.get(Profile, user.id))
    return render(request, "jobs.html", user, searches=searches, found=found, sp=sp,
                  last=request.session.get("last_discovery"))


@app.post("/jobs/search")
def jobs_search(request: Request, location: str = Form(""), remote_only: bool = Form(False), days: int = Form(7),
                companies: str = Form(""), save_name: str = Form(""), every_hours: int = Form(0),
                auto_queue_above: float = Form(0), boards_offset: int = Form(0), next_batch: bool = Form(False)):
    user = current_user(request)
    if save_name:
        with SessionLocal() as s:
            s.add(SavedSearch(user_id=user.id, name=save_name, keywords="", location=location, remote_only=remote_only,
                              companies=companies, sources=f"auto;days={days}", every_hours=every_hours,
                              auto_queue_above=auto_queue_above))
            s.commit()
    if next_batch:  # continue the company career-site scan where the last pass stopped, same filters
        prev = request.session.get("last_discovery") or {}
        location, remote_only, days = prev.get("where_in", location), prev.get("remote", remote_only), prev.get("days", days)
        jobs, errors, meta = discover(user.id, location, remote_only, days, "", sources=["career_sites"],
                                      boards_offset=max(0, boards_offset))
    else:
        jobs, errors, meta = discover(user.id, location, remote_only, days, companies)
    n = save_jobs(user.id, jobs)
    request.session["last_discovery"] = {"roles": meta["roles"], "method": meta["profile"]["method"],
                                         "raw": meta["raw_counts"], "kept": meta["kept_counts"],
                                         "where": meta["location"], "days": days, "where_in": location,
                                         "remote": remote_only, "cs": meta.get("career_sites") or {}}
    back = request.headers.get("referer") or ""
    flash(request, f"Searched {len(meta['raw_counts'])} sources for {', '.join(meta['roles'])}: {len(jobs)} matching jobs, {n} new."
          + (" Notes: " + "; ".join(errors[:2]) if errors else ""), "err" if errors and not jobs else "ok")
    return RedirectResponse("/" if back and __import__("urllib.parse").parse.urlparse(back).path == "/" else "/jobs", 303)


@app.post("/jobs/saved/{ss_id}/{action}")
def saved_action(request: Request, ss_id: int, action: str):
    user = current_user(request)
    with SessionLocal() as s:
        ss = s.get(SavedSearch, ss_id)
        if not ss or ss.user_id != user.id:
            raise HTTPException(404)
        if action == "delete":
            s.delete(ss); s.commit(); return RedirectResponse("/jobs", 303)
    n, errors = run_saved_search(ss_id)
    flash(request, f"Saved search ran: {n} new jobs." + (" " + "; ".join(errors[:2]) if errors else ""))
    return RedirectResponse("/jobs", 303)


# ---------------------------------------------------------------- profile
@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    user = current_user(request)
    with SessionLocal() as s:
        ensure_user_rows(s, user.id)
        prof = s.get(Profile, user.id)
        creds = list(s.scalars(select(Credential).where(Credential.user_id == user.id)))
    return render(request, "profile.html", user, prof=prof, d=prof.data or {}, ans=prof.answers or {},
                  SIMPLE_FIELDS=SIMPLE_FIELDS, ANSWER_FIELDS=ANSWER_FIELDS, creds=creds)


@app.post("/profile/resume")
def profile_resume(request: Request, file: UploadFile = File(...), parse: bool = Form(True)):
    user = current_user(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".pdf", ".docx", ".txt"):
        flash(request, "Upload a PDF, DOCX or TXT resume.", "err"); return RedirectResponse("/profile", 303)
    d = DATA_DIR / "uploads" / f"u{user.id}"; d.mkdir(parents=True, exist_ok=True)
    dest = d / f"resume{ext}"  # fixed server-side name: the client filename never touches the path
    data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        flash(request, f"Resume is larger than {MAX_UPLOAD_MB} MB.", "err"); return RedirectResponse("/profile", 303)
    if not data.strip():
        flash(request, "That file is empty.", "err"); return RedirectResponse("/profile", 303)
    tmp = d / f"upload_tmp{ext}"
    tmp.write_bytes(data)
    try:
        text = extract_text(str(tmp))
    except Exception as e:
        tmp.unlink(missing_ok=True); log.info("resume parse failed: %s", type(e).__name__)
        flash(request, "We couldn't read that file. Upload a valid PDF, DOCX or TXT resume.", "err")
        return RedirectResponse("/profile", 303)
    if len(text.strip()) < 30:
        tmp.unlink(missing_ok=True)
        flash(request, "No readable text found (scanned image PDFs aren't supported). Upload a text-based resume.", "err")
        return RedirectResponse("/profile", 303)
    tmp.replace(dest)
    with SessionLocal() as s:
        prof, st = s.get(Profile, user.id), s.get(Settings, user.id)
        prof.resume_path, prof.resume_text = str(dest), text
        msg = f"Resume uploaded ({len(text)} characters)."
        if parse and text:
            try:
                parsed = Provider(AIConfig.from_settings(st)).parse_resume(text)
                data = dict(prof.data or {})
                for k in FIELDS:
                    v = parsed.get(k)
                    if v not in (None, "", []) and not data.get(k):
                        data[k] = v
                    elif k in ("experience", "education") and v:
                        data[k] = v
                prof.data = data
                msg += f" Profile auto-filled with {st.ai_provider} - review the fields below."
            except AIError as e:
                msg += f" AI parsing failed: {e}"
        s.commit()
    flash(request, msg)
    with SessionLocal() as s:
        done = (s.get(Profile, user.id).answers or {}).get("questions_done")
    if not done:
        flash(request, "Next: answer the common application questions once, so forms never stall.")
        return RedirectResponse("/questions?onboarding=1", 303)
    return RedirectResponse("/profile", 303)


@app.get("/questions", response_class=HTMLResponse)
@app.get("/settings/questions", response_class=HTMLResponse)
def questions_page(request: Request, onboarding: int = 0):
    user = current_user(request)
    with SessionLocal() as s:
        ensure_user_rows(s, user.id)
        prof = s.get(Profile, user.id)
        d, ans = dict(prof.data or {}), dict(prof.answers or {})
    qa = ans.get("custom_qa") or []
    return render(request, "questions.html", user, d=d, ans=ans, SECTIONS=QUESTION_SECTIONS, qa=qa,
                  pending=sum(1 for x in qa if not str(x.get("a", "")).strip()), onboarding=onboarding)


@app.post("/questions")
async def questions_save(request: Request):
    user = current_user(request)
    form = await request.form()
    with SessionLocal() as s:
        prof = s.get(Profile, user.id)
        data, ans = dict(prof.data or {}), dict(prof.answers or {})
        for _, fields in QUESTION_SECTIONS:
            for k, _, _ in fields:
                if k in form:
                    (data if k.startswith("d:") else ans)[k[2:] if k.startswith("d:") else k] = str(form[k]).strip()
        qs, as_ = form.getlist("qa_q"), form.getlist("qa_a")
        old = {x.get("q"): x for x in ans.get("custom_qa") or []}
        qa = []
        for q, a in zip(qs, as_):
            q, a = str(q).strip(), str(a).strip()
            if q:
                x = dict(old.get(q, {}), q=q, a=a)
                if a and x.get("via") in ("pending", "ai (review)"):
                    x["via"] = "you"
                qa.append(x)
        ans["custom_qa"] = qa
        ans["questions_done"] = True
        prof.data, prof.answers = data, ans; s.commit()
    flash(request, "Application questions saved.")
    return RedirectResponse("/jobs" if form.get("onboarding") == "1" else "/questions", 303)


@app.post("/profile/save")
async def profile_save(request: Request):
    user = current_user(request)
    form = await request.form()
    with SessionLocal() as s:
        prof = s.get(Profile, user.id)
        data = dict(prof.data or {}); ans = dict(prof.answers or {})
        for k in SIMPLE_FIELDS:
            if k in form:
                data[k] = form[k].strip()
        for k, _, _ in ANSWER_FIELDS:
            if k in form:
                ans[k] = form[k].strip()
        prof.data, prof.answers = data, ans; s.commit()
    flash(request, "Profile saved."); return RedirectResponse("/profile", 303)


@app.post("/profile/credentials")
def add_cred(request: Request, site: str = Form(...), username: str = Form(...), password: str = Form(...)):
    user = current_user(request)
    with SessionLocal() as s:
        c = s.scalar(select(Credential).where(Credential.user_id == user.id, Credential.site == site.strip().lower()))
        if c:
            c.username, c.password_enc = username, enc(password)
        else:
            s.add(Credential(user_id=user.id, site=site.strip().lower(), username=username, password_enc=enc(password)))
        s.commit()
    flash(request, "Credential saved (encrypted)."); return RedirectResponse("/profile#credentials", 303)


@app.post("/profile/credentials/{cid}/delete")
def del_cred(request: Request, cid: int):
    user = current_user(request)
    with SessionLocal() as s:
        c = s.get(Credential, cid)
        if c and c.user_id == user.id:
            s.delete(c); s.commit()
    return RedirectResponse("/profile#credentials", 303)


# ---------------------------------------------------------------- settings
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = current_user(request)
    with SessionLocal() as s:
        st = s.get(Settings, user.id)
    from .docs_engine import LANGS, TONES
    from .pdf import THEME_LABELS
    return render(request, "settings.html", user, st=st, PROVIDERS=PROVIDERS, has_key=bool(st.ai_api_key_enc), x=st.extra or {},
                  LANGS=LANGS, TONES=TONES, THEMES=THEME_LABELS)


@app.post("/settings")
def settings_save(request: Request, ai_provider: str = Form("mock"), ai_api_key: str = Form(""), ai_model: str = Form(""),
                  ai_base_url: str = Form(""), dry_run: bool = Form(False), require_review: bool = Form(False),
                  auto_approve_above: float = Form(0), min_match_score: float = Form(0), daily_limit: int = Form(20),
                  clear_key: bool = Form(False), language: str = Form("en"), tone: str = Form("professional"),
                  theme: str = Form("ats"), jsearch_key: str = Form(""), adzuna_app_id: str = Form(""),
                  adzuna_app_key: str = Form(""), arbeitsagentur: bool = Form(False)):
    user = current_user(request)
    with SessionLocal() as s:
        st = s.get(Settings, user.id)
        st.ai_provider = ai_provider if ai_provider in PROVIDERS else "mock"
        if clear_key:
            st.ai_api_key_enc = ""
        elif ai_api_key.strip():
            st.ai_api_key_enc = enc(ai_api_key.strip())
        st.ai_model, st.ai_base_url = ai_model.strip(), ai_base_url.strip()
        st.dry_run, st.require_review = dry_run, require_review
        st.auto_approve_above, st.min_match_score = max(0, auto_approve_above), max(0, min_match_score)
        st.daily_limit = max(0, min(daily_limit, 200))
        x = dict(st.extra or {})
        x.update(language=language, tone=tone, theme=theme, arbeitsagentur=arbeitsagentur, adzuna_app_id=adzuna_app_id.strip())
        if jsearch_key.strip():
            x["jsearch_key"] = jsearch_key.strip()
        if adzuna_app_key.strip():
            x["adzuna_app_key"] = adzuna_app_key.strip()
        st.extra = x
        s.commit()
    flash(request, "Settings saved."); return RedirectResponse("/settings", 303)


@app.post("/api/settings/test-ai")
async def test_ai(request: Request):
    user = current_user(request)
    body = await request.json()
    with SessionLocal() as s:
        st = s.get(Settings, user.id)
        cfg = AIConfig.from_settings(st)
    p = body.get("ai_provider") or cfg.provider
    dflt = PROVIDERS.get(p, PROVIDERS["mock"])
    key = body.get("ai_api_key") or (cfg.api_key if p == cfg.provider else "")
    cfg = AIConfig(p, key, body.get("ai_model") or dflt["model"], (body.get("ai_base_url") or dflt["base_url"]).rstrip("/"))
    try:
        out = Provider(cfg).test()
        return {"ok": True, "message": f"Connected to {PROVIDERS[p]['label']} ({cfg.model}). Reply: {out}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}


@app.get("/docs/architecture", response_class=HTMLResponse)
def arch(request: Request):
    try:
        user = current_user(request)
    except LoginRequired:
        user = None
    return render(request, "architecture.html", user)


@app.get("/healthz")
def health():
    from sqlalchemy import text
    try:
        with SessionLocal() as s:
            s.execute(text("SELECT 1"))
        db = "ok"
    except Exception as e:  # pragma: no cover
        db = f"error: {type(e).__name__}"
    from . import __version__
    return JSONResponse({"ok": db == "ok", "db": db, "version": __version__}, 200 if db == "ok" else 503)
