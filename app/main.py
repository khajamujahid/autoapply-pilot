import csv
import io
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_
from starlette.middleware.sessions import SessionMiddleware

from .ai import PROVIDERS, Provider, AIConfig, AIError
from .ats import detect, SUPPORTED
from .auth import current_user, LoginRequired, pwd, oauth, valid_email, create_user
from .config import SECRET_KEY, DATA_DIR, RUN_WORKER_IN_PROCESS, ALLOW_SIGNUP, APP_NAME, BASE_URL
from .crypto import enc, dec
from .db import (SessionLocal, init_db, User, Settings, Profile, Credential, SavedSearch, Application, STATUSES,
                 ensure_user_rows)
from .discovery import search as discover, save_jobs, run_saved_search
from .profile import FIELDS, SIMPLE_FIELDS, ANSWER_FIELDS, extract_text

HERE = Path(__file__).parent
app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=60 * 60 * 24 * 14,
                   https_only=BASE_URL.startswith("https"))
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
tpl.env.globals.update(APP_NAME=APP_NAME, STATUSES=STATUSES, google_enabled=oauth is not None)
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
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email.lower().strip()))
    if not u or not u.password_hash or not pwd.verify(password, u.password_hash):
        flash(request, "Wrong email or password.", "err"); return RedirectResponse("/login", 303)
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
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, status: str = "", q: str = "", ats: str = ""):
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
    return render(request, "dashboard.html", user, apps=apps, counts=counts, f_status=status, f_q=q, f_ats=ats, st=st, setup=setup)


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
    with SessionLocal() as s:
        a = own_app(s, user, app_id)
    return render(request, "detail.html", user, a=a, shots=[Path(p).name for p in a.screenshots or []])


@app.post("/applications/{app_id}/action")
def app_action(request: Request, app_id: int, action: str = Form(...), tailored_resume: str = Form(None),
               cover_letter: str = Form(None)):
    user = current_user(request)
    from .pdf import text_to_pdf
    with SessionLocal() as s:
        a = own_app(s, user, app_id)
        if tailored_resume is not None and tailored_resume != a.tailored_resume:
            a.tailored_resume = tailored_resume
            if a.tailored_resume_pdf:
                text_to_pdf(a.tailored_resume, a.tailored_resume_pdf)
        if cover_letter is not None:
            a.cover_letter = cover_letter
        if action == "approve" and a.status == "REVIEW":
            a.status = "APPROVED"
        elif action == "queue" and a.status in ("DISCOVERED", "SKIPPED"):
            a.status = "QUEUED"
        elif action == "retry":
            a.status, a.error = ("APPROVED" if a.tailored_resume else "QUEUED"), ""
        elif action == "reject":
            a.status = "SKIPPED"
        elif action == "mark_applied":
            from .db import now
            a.status, a.applied_at = "APPLIED", now()
        elif action == "delete":
            s.delete(a); s.commit(); return RedirectResponse("/", 303)
        s.commit()
    return RedirectResponse(request.headers.get("referer") or f"/applications/{app_id}", 303)


@app.post("/applications/bulk")
def bulk(request: Request, action: str = Form(...), ids: list[int] = Form([])):
    user = current_user(request)
    with SessionLocal() as s:
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
    with SessionLocal() as s:
        searches = list(s.scalars(select(SavedSearch).where(SavedSearch.user_id == user.id)))
        found = list(s.scalars(select(Application).where(Application.user_id == user.id, Application.status == "DISCOVERED")
                               .order_by(Application.match_score.desc()).limit(300)))
    return render(request, "jobs.html", user, searches=searches, found=found)


@app.post("/jobs/search")
def jobs_search(request: Request, keywords: str = Form(""), location: str = Form(""), remote_only: bool = Form(False),
                companies: str = Form(""), sources: list[str] = Form([]), save_name: str = Form(""),
                every_hours: int = Form(0), auto_queue_above: float = Form(0)):
    user = current_user(request)
    src = ",".join(sources) or "boards"
    if save_name:
        with SessionLocal() as s:
            s.add(SavedSearch(user_id=user.id, name=save_name, keywords=keywords, location=location, remote_only=remote_only,
                              companies=companies, sources=src, every_hours=every_hours, auto_queue_above=auto_queue_above))
            s.commit()
    jobs, errors = discover(user.id, keywords, location, remote_only, companies, src)
    n = save_jobs(user.id, jobs)
    flash(request, f"Found {len(jobs)} matching jobs, {n} new." + (" Notes: " + "; ".join(errors[:3]) if errors else ""),
          "err" if errors and not jobs else "ok")
    return RedirectResponse("/jobs", 303)


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
    dest = d / f"resume{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    text = extract_text(str(dest))
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
    flash(request, msg); return RedirectResponse("/profile", 303)


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
    return render(request, "settings.html", user, st=st, PROVIDERS=PROVIDERS, has_key=bool(st.ai_api_key_enc))


@app.post("/settings")
def settings_save(request: Request, ai_provider: str = Form("mock"), ai_api_key: str = Form(""), ai_model: str = Form(""),
                  ai_base_url: str = Form(""), dry_run: bool = Form(False), require_review: bool = Form(False),
                  auto_approve_above: float = Form(0), min_match_score: float = Form(0), daily_limit: int = Form(20),
                  clear_key: bool = Form(False)):
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
    return {"ok": True}
