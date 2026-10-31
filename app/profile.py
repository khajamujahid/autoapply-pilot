"""Profile schema, standard answers, resume text extraction, and deterministic question answering."""
import re
from pathlib import Path

FIELDS = ["first_name", "last_name", "email", "phone", "location", "linkedin", "github", "website",
          "current_title", "target_roles", "current_company", "years_experience", "skills", "summary", "experience", "education"]
SIMPLE_FIELDS = [f for f in FIELDS if f not in ("experience", "education")]

DECLINE = "Decline to self-identify"
# Application Questions: asked once (onboarding + Settings), reused on every Workday/Greenhouse/Lever/Ashby/iCIMS form.
# Keys prefixed "d:" are stored on profile.data, the rest on profile.answers.
QUESTION_SECTIONS = [
    ("Name & contact", [
        ("d:first_name", "Legal first name", None), ("d:last_name", "Legal last name", None),
        ("preferred_name", "Preferred name", None), ("d:email", "Email", None), ("d:phone", "Phone", None)]),
    ("Address", [
        ("address", "Street address", None), ("address2", "Apt / suite", None), ("city", "City", None),
        ("state", "State / province", None), ("zip", "ZIP / postal code", None), ("country", "Country of residence", None)]),
    ("Links", [("d:linkedin", "LinkedIn URL", None), ("d:github", "GitHub URL", None), ("d:website", "Portfolio / website", None)]),
    ("Work authorization", [
        ("work_authorized", "Authorized to work in the country of the job?", ["Yes", "No"]),
        ("authorized_countries", "Countries you are authorized to work in (comma separated)", None),
        ("needs_sponsorship", "Will you need visa sponsorship now or in the future?", ["No", "Yes"]),
        ("work_auth_explanation", "Current visa / work status (free text, e.g. 'US Citizen', 'H-1B', 'F-1 OPT STEM')", None),
        ("citizenship", "Citizenship (country)", None),
        ("restricted_citizenship", "Are you a U.S. citizen?", ["No", "Yes"]),
        ("us_person", "Export control: are you a U.S. person (citizen, permanent resident, refugee or asylee)?", ["No", "Yes"]),
        ("security_clearance", "Active security clearance", ["None", "Secret", "Top Secret", "TS/SCI", "Public Trust"])]),
    ("Preferences", [
        ("willing_to_relocate", "Willing to relocate?", ["Yes", "No"]),
        ("remote_preference", "Work preference", ["Any", "Remote", "Hybrid", "Onsite"]),
        ("earliest_start", "Earliest start date", None), ("notice_period", "Notice period", None),
        ("salary_expectation", "Salary expectation (e.g. 120000 USD / year)", None)]),
    ("Education & experience", [
        ("degree", "Highest degree (e.g. Master's)", ["", "High school", "Associate's", "Bachelor's", "Master's", "MBA", "PhD", "Other"]),
        ("field_of_study", "Field of study", None), ("school", "School / university", None),
        ("grad_date", "Graduation date (MM/YYYY)", None), ("d:years_experience", "Total years of experience", None),
        ("languages", "Languages you speak (comma separated)", None)]),
    ("Standard yes/no questions", [
        ("over_18", "Are you 18 or older?", ["Yes", "No"]),
        ("previously_employed", "Have you previously worked for the company you're applying to? (default answer)", ["No", "Yes"]),
        ("relatives_at_company", "Relatives / close friends working at the company? (default answer)", ["No", "Yes"]),
        ("non_compete", "Bound by a non-compete or non-solicit agreement?", ["No", "Yes"]),
        ("background_check", "Consent to a background check?", ["Yes", "No"]),
        ("criminal_record", "Felony / misdemeanor conviction?", ["No", "Yes"]),
        ("how_heard", "How did you hear about the job?", None)]),
    ("Voluntary self-identification (EEO)", [
        ("gender", "Gender", [DECLINE, "Male", "Female", "Non-binary"]),
        ("hispanic_latino", "Hispanic or Latino?", [DECLINE, "No", "Yes"]),
        ("race", "Race / ethnicity", [DECLINE, "Asian", "Black or African American", "White", "American Indian or Alaska Native",
                                      "Native Hawaiian or Other Pacific Islander", "Two or more races", "Hispanic or Latino"]),
        ("veteran", "Veteran status", [DECLINE, "I am not a protected veteran", "I am a protected veteran"]),
        ("disability", "Disability", [DECLINE, "No, I do not have a disability", "Yes, I have a disability"])]),
]
ANSWER_FIELDS = [f for _, fs in QUESTION_SECTIONS for f in fs if not f[0].startswith("d:")]


def default_answers():
    return {"work_authorized": "Yes", "needs_sponsorship": "No", "willing_to_relocate": "Yes",
            "remote_preference": "Any", "country": "United States", "salary_expectation": "", "notice_period": "2 weeks",
            "over_18": "Yes", "previously_employed": "No", "relatives_at_company": "No", "non_compete": "No",
            "background_check": "Yes", "criminal_record": "No", "hispanic_latino": "Decline to self-identify",
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
    c = custom_answer(prof, question, options)
    if c:
        return c
    if re.search(r"hispanic|latino", q) and options and a.get("hispanic_latino"):
        return _pick(options, a["hispanic_latino"], "decline", "prefer not", "don't wish", "do not wish")
    if re.search(r"export|itar|ear\b|u\.?s\.? person|deemed export", q):
        return yn(a.get("us_person") or a.get("restricted_citizenship") or "No")
    if re.search(r"relative|family member|related to (any|an) (employee|person)|close (friend|personal relationship)", q):
        return yn(a.get("relatives_at_company", "No"))
    if re.search(r"non-?compete|non-?solicit", q):
        return yn(a.get("non_compete", "No"))
    if re.search(r"background (check|screen)|drug (test|screen)", q):
        return yn(a.get("background_check", "Yes"))
    if re.search(r"(18|eighteen) (years|or older)|at least 18|over the age of 18|legal age", q):
        return yn(a.get("over_18", "Yes"))
    if re.search(r"(previously|ever|before|formerly|in the past).{0,40}(employed|worked|contract)|former employee|current or former", q):
        return yn(a.get("previously_employed", "No"))
    if re.search(r"language", q) and not options and a.get("languages"):
        return a["languages"]
    if re.search(r"school|university|college|institution", q) and not options and a.get("school"):
        return a["school"]
    if re.search(r"field of study|major|discipline", q) and not options and a.get("field_of_study"):
        return a["field_of_study"]
    if re.search(r"graduat", q) and not options and a.get("grad_date"):
        return a["grad_date"]
    if re.search(r"earliest.*start|start date|available to start|availability date", q) and not options and a.get("earliest_start"):
        return a["earliest_start"]
    if re.search(r"preferred (first )?name|nickname|name you.d prefer|prefer us to use", q) and not options:
        return a.get("preferred_name") or d.get("first_name")
    if re.search(r"address line 2|apt|suite|unit", q) and not options and a.get("address2"):
        return a["address2"]
    if re.search(r"phone|mobile|telephone", q) and not options and d.get("phone"):
        return d["phone"]
    if re.search(r"citizenship|country of citizen|nationality", q) and not options and a.get("citizenship"):
        return a["citizenship"]
    if re.search(r"which countries.*authori|countries.*work", q) and not options and a.get("authorized_countries"):
        return a["authorized_countries"]
    if re.search(r"gender|sex\b", q):
        return _pick(options, a.get("gender", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if re.search(r"race|ethnic|hispanic|latino", q):
        return _pick(options, a.get("race", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if "veteran" in q:
        return _pick(options, a.get("veteran", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not a protected") if options else None
    if "disab" in q:
        return _pick(options, a.get("disability", ""), "decline", "prefer not", "don't wish", "do not wish", "not want to answer", "not to answer") if options else None
    if re.search(r"legal (full )?name|full legal name", q) and not options:
        return f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()
    if re.search(r"18\+? years|at least 18|over the age of 18|18 years of age", q):
        return yn("Yes")
    if re.search(r"(been|were you) employed by .* (in the past|previously)|ever worked for or provided services to|worked (for|at) .* before", q):
        return yn("No")
    if re.search(r"felony|misdemeanor|convicted|criminal", q):
        return yn(a.get("criminal_record", "No"))
    if re.search(r"clearance", q):
        cl = a.get("security_clearance") or "None"
        return (_pick(options, cl) or yn("No" if cl in ("None", "N/A") else "Yes")) if options else cl
    if re.search(r"u\.?s\.? citizen|citizenship", q) and options:
        return yn(a.get("restricted_citizenship", "No"))
    if re.search(r"(explain|describe|details?).*(visa|authori|immigration|status)|visa status|immigration status|current (work )?authori[sz]ation", q) and not options:
        return a.get("work_auth_explanation") or None
    if re.search(r"degree|education level|highest level", q):
        return _pick(options, a.get("degree", "") or "bachelor") if options else a.get("degree")
    if re.search(r"\bzip|postal", q) and not options:
        return a.get("zip") or None
    if re.search(r"street|address line|mailing address|^address", q) and not options:
        return a.get("address") or None
    if re.search(r"^state\b|state/province|\bstate\b", q):
        return _pick(options, a.get("state", "")) if options else a.get("state")
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
    if re.search(r"^city\b|\bcity\b", q) and not options:
        return a.get("city") or d.get("location") or None
    if re.search(r"\b(location|where are you (based|located))", q) and not options:
        return d.get("location") or None
    if re.search(r"previously (worked|been employed|applied)|former employee|non-?compete|employment agreement|restrictive", q):
        return yn("No")
    if re.search(r"privacy|consent|acknowledge|i agree|certify|accurate", q) and options:
        return _pick(options, "yes", "i agree", "agree", "i acknowledge", "acknowledge", "confirm")
    if re.search(r"remote|hybrid|on-?site|office", q) and options:
        return _pick(options, a.get("remote_preference", ""), "yes")
    return None


def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (t or "").lower())).strip()


def custom_answer(prof, question, options=None):
    """The user's own Q&A list: exact match first, then fuzzy (token overlap / difflib >= 0.82)."""
    import difflib
    qa = [x for x in ((prof.answers or {}).get("custom_qa") or []) if str(x.get("a", "")).strip()]
    if not qa:
        return None
    nq = _norm(question)
    best, br = None, 0.0
    for x in qa:
        nx = _norm(x.get("q", ""))
        if not nx:
            continue
        r = 1.0 if nx == nq else difflib.SequenceMatcher(None, nx, nq).ratio()
        tq, tx = set(nq.split()), set(nx.split())
        if tx and len(tx) >= 3:
            r = max(r, len(tq & tx) / len(tq | tx) + 0.15)
        if r > br:
            best, br = x, r
    if not best or br < 0.82:
        return None
    ans = str(best["a"]).strip()
    return _pick(options, ans) if options else ans


def add_unknown_questions(prof, items):
    """Save questions the filler could not answer (or that AI answered) to the user's Q&A list, once each."""
    qa = list((prof.answers or {}).get("custom_qa") or [])
    have = {_norm(x.get("q", "")) for x in qa}
    added = 0
    for it in items:
        n = _norm(it["q"])
        if n and n not in have and len(n) > 3:
            qa.append({"q": it["q"][:300], "a": it.get("a", ""), "options": it.get("options", [])[:20],
                       "source": it.get("source", ""), "via": it.get("via", "pending")})
            have.add(n); added += 1
    if added:
        ans = dict(prof.answers or {}); ans["custom_qa"] = qa; prof.answers = ans
    return added
