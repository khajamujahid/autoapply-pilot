import datetime as dt
from sqlalchemy import create_engine, String, Text, Float, DateTime, Integer, JSON, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)

STATUSES = ["DISCOVERED", "QUEUED", "OPTIMIZING_RESUME", "OPTIMIZING_CL", "REVIEW", "APPROVED", "APPLYING",
            "APPLIED", "DRY_RUN_DONE", "NEEDS_MANUAL", "SKIPPED", "FAILED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"]


def now():
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    google_sub: Mapped[str] = mapped_column(String(100), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Settings(Base):
    """Per-user preferences + Bring-Your-Own-AI config (api key encrypted)."""
    __tablename__ = "settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ai_provider: Mapped[str] = mapped_column(String(30), default="mock")
    ai_api_key_enc: Mapped[str] = mapped_column(Text, default="")
    ai_model: Mapped[str] = mapped_column(String(100), default="")
    ai_base_url: Mapped[str] = mapped_column(String(300), default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    require_review: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_approve_above: Mapped[float] = mapped_column(Float, default=0)  # 0 = off
    min_match_score: Mapped[float] = mapped_column(Float, default=0)
    daily_limit: Mapped[int] = mapped_column(Integer, default=20)
    # v1.2: output language, default resume theme, tone, optional job-source keys (jsearch_key, adzuna_*, usajobs_*, arbeitsagentur)
    extra: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)


class Profile(Base):
    __tablename__ = "profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)        # structured fields (see profile.FIELDS)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)     # standard application answers
    resume_path: Mapped[str] = mapped_column(String(500), default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    site: Mapped[str] = mapped_column(String(200))   # e.g. "workday:acme" or a hostname
    username: Mapped[str] = mapped_column(String(255))
    password_enc: Mapped[str] = mapped_column(Text)


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    keywords: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    remote_only: Mapped[bool] = mapped_column(Boolean, default=False)
    companies: Mapped[str] = mapped_column(Text, default="")  # lines: greenhouse:gitlab / lever:palantir / ashby:ramp
    sources: Mapped[str] = mapped_column(String(200), default="boards,remotive,arbeitnow")
    every_hours: Mapped[int] = mapped_column(Integer, default=0)  # 0 = manual only
    auto_queue_above: Mapped[float] = mapped_column(Float, default=0)  # 0 = off
    last_run: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    apply_url: Mapped[str] = mapped_column(String(1000), default="")
    source: Mapped[str] = mapped_column(String(50), default="manual")
    ats: Mapped[str] = mapped_column(String(50), default="unknown")
    company: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    job_description: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[float] = mapped_column(Float, default=0)
    match_details: Mapped[dict] = mapped_column(JSON, default=dict)
    match_explanation: Mapped[str] = mapped_column(Text, default="")
    tailored_resume: Mapped[str] = mapped_column(Text, default="")
    tailored_resume_pdf: Mapped[str] = mapped_column(String(500), default="")
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    email_draft: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    requirements: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True)  # [{text, covered, evidence}]
    theme: Mapped[str | None] = mapped_column(String(30), default="", nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    screenshots: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[list] = mapped_column(JSON, default=list)
    answers_used: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class DocVersion(Base):
    """Every draft (resume / cover letter / email) of an application, newest = current. Enables undo + history."""
    __tablename__ = "doc_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))           # resume | cover | email
    content: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(String(300), default="")  # e.g. "generated (mock)", "chat: make it shorter", "undo"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatMsg(Base):
    __tablename__ = "chat_msgs"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | ai
    kind: Mapped[str] = mapped_column(String(20), default="resume")
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


def _migrate():
    """Tiny additive migrations for existing installs (create_all never adds columns)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    adds = {"settings": [("extra", "JSON")], "applications": [("requirements", "JSON"), ("email_draft", "TEXT"),
                                                               ("theme", "VARCHAR(30)"), ("notes", "TEXT")]}
    with engine.begin() as c:
        for table, cols in adds.items():
            have = {x["name"] for x in insp.get_columns(table)}
            for name, typ in cols:
                if name not in have:
                    c.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {typ}"))


def init_db():
    Base.metadata.create_all(engine)
    _migrate()


def ensure_user_rows(s, user_id):
    if not s.get(Settings, user_id):
        s.add(Settings(user_id=user_id))
    if not s.get(Profile, user_id):
        from .profile import default_answers
        s.add(Profile(user_id=user_id, data={}, answers=default_answers()))
    s.commit()
