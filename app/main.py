import os
import shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from pypdf import PdfReader
from sqlalchemy import select, func
from .config import DATA_DIR, DRY_RUN, REQUIRE_REVIEW, LLM_API_KEY
from .db import SessionLocal, Application, Profile, Status, init_db
from .ats import detect, SUPPORTED
from .fetcher import fetch_job

app = FastAPI(title="autoapply-pilot")
init_db()
if os.getenv("RUN_WORKER_IN_PROCESS", "true").lower() in ("1", "true", "yes"):
    from .worker import start_background
    start_background()

PROFILE_FIELDS = [c.name for c in Profile.__table__.columns if c.name not in ("id", "resume_path", "resume_text")]


class ProfileIn(BaseModel):
    model_config = {"extra": "ignore"}
    first_name: str = ""; last_name: str = ""; email: str = ""; phone: str = ""; location: str = ""; country: str = "United States"
    linkedin: str = ""; github: str = ""; website: str = ""; current_company: str = ""; skills: str = ""
    years_experience: float = 0; work_authorized: str = "yes"; needs_sponsorship: str = "no"
    workday_email: str = ""; workday_password: str = ""


class UrlsIn(BaseModel):
    urls: list[str]


def ser(a: Application):
    return {c.name: getattr(a, c.name) for c in Application.__table__.columns if c.name not in ("job_description",)} | {
        "screenshots": [f"/api/v1/artifact?path={p}" for p in (a.screenshots or [])]}


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/api/v1/config")
def config():
    return {"dry_run": DRY_RUN, "require_review": REQUIRE_REVIEW, "llm": "configured" if LLM_API_KEY else "mock"}


@app.get("/api/v1/profile")
def get_profile():
    with SessionLocal() as s:
        p = s.get(Profile, 1)
        d = {f: getattr(p, f) for f in PROFILE_FIELDS}
        d["workday_password"] = "********" if p.workday_password else ""
        d["resume_uploaded"] = bool(p.resume_text); d["resume_chars"] = len(p.resume_text or "")
        return d


@app.put("/api/v1/profile")
def put_profile(body: ProfileIn):
    with SessionLocal() as s:
        p = s.get(Profile, 1)
        for k, v in body.model_dump().items():
            if k == "workday_password" and v in ("", "********"):
                continue
            setattr(p, k, v)
        s.commit()
    return get_profile()


@app.post("/api/v1/profile/resume")
def upload_resume(file: UploadFile = File(...)):
    name = Path(file.filename or "resume").name
    dest = DATA_DIR / "uploads" / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if name.lower().endswith(".pdf"):
        text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(dest)).pages)
    else:
        text = dest.read_text(errors="ignore")
    with SessionLocal() as s:
        p = s.get(Profile, 1); p.resume_path, p.resume_text = str(dest), text.strip(); s.commit()
    return {"ok": True, "chars": len(text)}


@app.post("/api/v1/job-description/fetch")
def jd_fetch(body: dict):
    return fetch_job(body["url"])


@app.post("/api/v1/applications")
def create(body: UrlsIn):
    out = []
    with SessionLocal() as s:
        for u in [u.strip() for u in body.urls if u.strip()]:
            if s.scalar(select(Application).where(Application.url == u)):
                out.append({"url": u, "ok": False, "code": "DUPLICATE_APPLICATION"}); continue
            ats, _ = detect(u)
            a = Application(url=u, ats=ats); s.add(a); s.commit()
            out.append({"url": u, "ok": True, "id": a.id, "ats": ats, "supported": ats in SUPPORTED})
    return out


@app.get("/api/v1/applications/list")
def list_apps(status: str | None = None):
    with SessionLocal() as s:
        q = select(Application).order_by(Application.id.desc())
        if status:
            q = q.where(Application.status == status)
        return [ser(a) for a in s.scalars(q)]


@app.get("/api/v1/applications/list/counts")
def counts():
    with SessionLocal() as s:
        return dict(s.execute(select(Application.status, func.count()).group_by(Application.status)).all())


def _get(s, i):
    a = s.get(Application, i)
    if not a:
        raise HTTPException(404)
    return a


@app.get("/api/v1/applications/{i}")
def detail(i: int):
    with SessionLocal() as s:
        a = _get(s, i); return ser(a) | {"job_description": a.job_description}


class ReviewIn(BaseModel):
    tailored_resume: str | None = None
    cover_letter: str | None = None


@app.post("/api/v1/applications/{i}/approve")
def approve(i: int, body: ReviewIn | None = None):
    from .pdf import text_to_pdf
    with SessionLocal() as s:
        a = _get(s, i)
        if a.status != Status.REVIEW.value:
            raise HTTPException(409, f"status is {a.status}, not REVIEW")
        if body and body.tailored_resume:
            a.tailored_resume = body.tailored_resume; text_to_pdf(a.tailored_resume, a.tailored_resume_pdf)
        if body and body.cover_letter:
            a.cover_letter = body.cover_letter
        a.status = Status.APPROVED.value; s.commit()
    return {"ok": True}


@app.post("/api/v1/applications/{i}/retry")
def retry(i: int):
    with SessionLocal() as s:
        a = _get(s, i)
        a.status = Status.APPROVED.value if a.tailored_resume else Status.QUEUED.value
        a.error = ""; s.commit()
    return {"ok": True, "status": a.status}


@app.delete("/api/v1/applications/{i}")
def delete(i: int):
    with SessionLocal() as s:
        s.delete(_get(s, i)); s.commit()
    return {"ok": True}


@app.get("/api/v1/artifact")
def artifact(path: str):
    p = Path(path).resolve()
    if not str(p).startswith(str(DATA_DIR.resolve())) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)
