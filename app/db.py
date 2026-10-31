import datetime as dt
import enum
from sqlalchemy import create_engine, String, Text, Float, DateTime, Integer, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    QUEUED = "QUEUED"
    OPTIMIZING_RESUME = "OPTIMIZING_RESUME"
    OPTIMIZING_CL = "OPTIMIZING_CL"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"   # user approved; waiting for the submitter
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"     # real submit clicked (DRY_RUN=false)
    DRY_RUN_DONE = "DRY_RUN_DONE"  # form filled + screenshot, NOT submitted
    SKIPPED = "SKIPPED"     # below match threshold
    FAILED = "FAILED"


def now():
    return dt.datetime.now(dt.timezone.utc)


class Profile(Base):
    __tablename__ = "profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    country: Mapped[str] = mapped_column(String(100), default="United States")
    linkedin: Mapped[str] = mapped_column(String(300), default="")
    github: Mapped[str] = mapped_column(String(300), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    current_company: Mapped[str] = mapped_column(String(200), default="")
    skills: Mapped[str] = mapped_column(Text, default="")  # comma separated
    years_experience: Mapped[float] = mapped_column(Float, default=0)
    work_authorized: Mapped[str] = mapped_column(String(10), default="yes")
    needs_sponsorship: Mapped[str] = mapped_column(String(10), default="no")
    resume_path: Mapped[str] = mapped_column(String(500), default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    workday_email: Mapped[str] = mapped_column(String(200), default="")
    workday_password: Mapped[str] = mapped_column(String(200), default="")  # local only


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    ats: Mapped[str] = mapped_column(String(50), default="unknown")
    company: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    job_description: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[float] = mapped_column(Float, default=0)
    match_details: Mapped[dict] = mapped_column(JSON, default=dict)
    tailored_resume: Mapped[str] = mapped_column(Text, default="")
    tailored_resume_pdf: Mapped[str] = mapped_column(String(500), default="")
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default=Status.QUEUED.value)
    error: Mapped[str] = mapped_column(Text, default="")
    screenshots: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.get(Profile, 1):
            s.add(Profile(id=1)); s.commit()
