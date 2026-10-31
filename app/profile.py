"""Profile schema, standard answers, resume text extraction, and deterministic question answering."""
import re
from pathlib import Path

FIELDS = ["first_name", "last_name", "email", "phone", "location", "linkedin", "github", "website",
          "current_title", "current_company", "years_experience", "skills", "summary", "experience", "education"]
SIMPLE_FIELDS = [f for f in FIELDS if f not in ("experience", "education")]

ANSWER_FIELDS = [
    ("work_authorized", "Authorized to work in the country of the job?", ["Yes", "No"]),
    ("needs_sponsorship", "Will you need visa sponsorship now or in future?", ["No", "Yes"]),
    ("willing_to_relocate", "Willing to relocate?", ["Yes", "No"]),
    ("remote_preference", "Work preference", ["Remote", "Hybrid", "Onsite", "Any"]),
    ("country", "Country of residence", None),
    ("salary_expectation", "Salary expectation (e.g. 120000 USD)", None),
    ("notice_period", "Notice period / earliest start", None),
    ("how_heard", "How did you hear about us?", None),
    ("gender", "Gender (EEO, voluntary)", ["Decline to self-identify", "Male", "Female", "Non-binary"]),
    ("race", "Race / ethnicity (EEO, voluntary)", ["Decline to self-identify"]),
    ("veteran", "Veteran status (EEO, voluntary)", ["Decline to self-identify", "I am not a protected veteran", "I am a protected veteran"]),
    ("disability", "Disability (EEO, voluntary)", ["Decline to self-identify", "No", "Yes"]),
]


def default_answers():
    return {"work_authorized": "Yes", "needs_sponsorship": "No", "willing_to_relocate": "Yes",
            "remote_preference": "Any", "country": "United States", "salary_expectation": "", "notice_period": "2 weeks",
            "how_heard": "Company careers page", "gender": "Decline to self-identify",
            "race": "Decline to self-identify", "veteran": "Decline to self-identify", "disability": "Decline to self-identify"}


def extract_text(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages).strip()
    if p.suffix.lower() == ".docx":
        import docx
        d = docx.Document(str(p))
        return "\n".join(par.text for par in d.paragraphs).strip()
    return p.read_text(errors="ignore").strip()


def profile_text(prof) -> str:
    d = prof.data or {}
    if prof.resume_text:
        return prof.resume_text
    return "\n".join(f"{k}: {d.get(k)}" for k in SIMPLE_FIELDS if d.get(k))


def profile_summary(prof) -> str:
    d, a = prof.data or {}, prof.answers or {}
    return ("\n".join(f"{k}: {d.get(k)}" for k in SIMPLE_FIELDS if d.get(k)) + "\n" +
            "\n".join(f"{k}: {v}" for k, v in a.items() if v) + "\nRESUME:\n" + (prof.resume_text or "")[:3000])


def full_name(prof):
    d = prof.data or {}
    return f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()


def _pick(options, *prefs):
    for p in prefs:
        for o in options:
            if o.strip().lower() == p.lower():
                return o
    for p in prefs:
        for o in options:
            if p.lower() in o.lower():
                return o
    return None


def rule_answer(prof, question: str, options: list[str] | None = None):
    """Answer common questions from the profile without AI. Returns None if unknown."""
    d, a = prof.data or {}, prof.answers or {}
    q = re.sub(r"\s+", " ", question.lower())
    yn = lambda v: (_pick(options, v) if options else v)
    if re.search(r"gender|sex\b", q):
        return _pick(options, a.get("gender", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if re.search(r"race|ethnic|hispanic|latino", q):
        return _pick(options, a.get("race", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if "veteran" in q:
        return _pick(options, a.get("veteran", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not a protected") if options else None
    if "disab" in q:
        return _pick(options, a.get("disability", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if "sponsor" in q:
        return yn(a.get("needs_sponsorship", "No"))
    if re.search(r"authori[sz]ed|legally (able|eligible|permitted)|eligib.* to work|right to work", q):
        return yn(a.get("work_authorized", "Yes"))
    if "relocat" in q:
        return yn(a.get("willing_to_relocate", "Yes"))
    if re.search(r"salary|compensation|pay expectation", q):
        return a.get("salary_expectation") or None if not options else None
    if re.search(r"notice period|start date|earliest.*start|when can you start", q) and not options:
        return a.get("notice_period") or None
    if re.search(r"how did you hear|where did you (hear|find)|referral source|source", q):
        return _pick(options, a.get("how_heard", ""), "careers", "website", "linkedin", "other") if options else a.get("how_heard")
    if "linkedin" in q and not options:
        return d.get("linkedin") or None
    if "github" in q and not options:
        return d.get("github") or None
    if re.search(r"website|portfolio", q) and not options:
        return d.get("website") or d.get("github") or None
    if re.search(r"country", q):
        return _pick(options, a.get("country", "")) if options else a.get("country")
    if re.search(r"preferred (first )?name|name you.d prefer|prefer us to use", q) and not options:
        return d.get("first_name")
    if re.search(r"(current|most recent) (company|employer)", q) and not options:
        return d.get("current_company") or None
    if re.search(r"(current|most recent) (title|role|position)", q) and not options:
        return d.get("current_title") or None
    if re.search(r"years of (professional )?experience|how many years", q) and not options:
        return str(d.get("years_experience") or "")
    if re.search(r"\b(city|location|where are you (based|located))", q) and not options:
        return d.get("location") or None
    if re.search(r"previously (worked|been employed|applied)|former employee|non-?compete|employment agreement|restrictive", q):
        return yn("No")
    if re.search(r"privacy|consent|acknowledge|i agree|certify|accurate", q) and options:
        return _pick(options, "yes", "i agree", "agree", "i acknowledge", "acknowledge", "confirm")
    if re.search(r"remote|hybrid|on-?site|office", q) and options:
        return _pick(options, a.get("remote_preference", ""), "yes")
    return None
