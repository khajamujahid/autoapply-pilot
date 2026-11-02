"""Derive a search profile (role titles, keywords, seniority) from ANY resume, for ANY profession.

Uses the user's own AI key when set; otherwise a built-in title/keyword extractor with a cross-industry
title taxonomy (tech, networking, healthcare, finance, sales, operations, trades, education, legal, ...).
Users can override the result with the `target_roles` field on their profile.
"""
import re
from collections import Counter

# Common job titles across industries (lowercase). Longer titles are matched first.
TAXONOMY = """
software engineer|software developer|backend engineer|frontend engineer|full stack developer|full stack engineer|java developer|python developer
.net developer|mobile developer|ios developer|android developer|web developer|devops engineer|site reliability engineer|platform engineer
cloud engineer|cloud architect|solutions architect|data engineer|data scientist|data analyst|machine learning engineer|ai engineer
business analyst|business intelligence analyst|qa engineer|test engineer|automation engineer|security engineer|cybersecurity analyst
security analyst|soc analyst|penetration tester|systems administrator|system administrator|systems engineer|it support specialist
help desk technician|desktop support technician|it technician|it manager|database administrator|product manager|project manager
program manager|scrum master|ux designer|ui designer|graphic designer|technical writer|salesforce developer|sap consultant
network engineer|network administrator|network architect|network technician|network analyst|network security engineer|wireless engineer
voip engineer|telecommunications engineer|noc technician|noc engineer|infrastructure engineer
registered nurse|nurse practitioner|licensed practical nurse|staff nurse|charge nurse|icu nurse|er nurse|travel nurse|nurse manager
nursing assistant|certified nursing assistant|medical assistant|physician assistant|pharmacist|pharmacy technician|physical therapist
occupational therapist|respiratory therapist|radiologic technologist|medical technologist|dental hygienist|dental assistant|caregiver
home health aide|phlebotomist|medical coder|medical biller|clinical research coordinator|social worker|case manager|counselor
accountant|staff accountant|senior accountant|cpa|tax accountant|auditor|internal auditor|bookkeeper|accounts payable specialist
accounts receivable specialist|payroll specialist|financial analyst|fp&a analyst|controller|accounting manager|tax manager|finance manager
credit analyst|loan officer|underwriter|investment analyst|bank teller|actuary|claims adjuster|insurance agent
sales representative|account executive|account manager|sales manager|business development representative|sales development representative
customer success manager|customer service representative|call center representative|marketing manager|digital marketing specialist
marketing coordinator|social media manager|content writer|copywriter|seo specialist|public relations specialist
operations manager|supply chain analyst|logistics coordinator|procurement specialist|buyer|warehouse associate|warehouse manager
forklift operator|truck driver|delivery driver|dispatcher|inventory specialist
human resources generalist|hr generalist|recruiter|talent acquisition specialist|hr manager|office manager|administrative assistant
executive assistant|receptionist|data entry clerk
electrician|plumber|hvac technician|mechanic|maintenance technician|welder|carpenter|machinist|construction manager|civil engineer
mechanical engineer|electrical engineer|manufacturing engineer|quality engineer|process engineer|chemical engineer|industrial engineer
teacher|substitute teacher|teaching assistant|professor|tutor|school counselor|paralegal|legal assistant|attorney|lawyer|compliance analyst
chef|cook|line cook|server|bartender|restaurant manager|retail associate|store manager|cashier|security guard|pharmacy assistant
"""
TITLES = sorted({t.strip() for line in TAXONOMY.split("\n") for t in line.split("|") if t.strip()}, key=len, reverse=True)

STOP = set("""a an and or the to of in for on with at by from as is are be we you our your this that will have has it its not who
i me my was were been using used use including within across over under per via etc also into all new more most work worked working team
teams company role responsible responsibilities experience years year skills summary education university college degree bachelor
master inc llc ltd corp project projects client clients ensure ensured various strong ability""".split())

LEVELS = [("intern", 0), ("entry", 0), ("junior", 1), ("mid", 3), ("senior", 6), ("lead", 8), ("staff", 9), ("principal", 11),
          ("manager", 8), ("director", 12)]


def seniority_from_years(y):
    try:
        y = float(y or 0)
    except ValueError:
        y = 0
    return "entry" if y < 2 else "mid" if y < 5 else "senior" if y < 10 else "lead"


def _find_titles(text):
    text = " " + re.sub(r"[^a-z0-9&+.# ]", " ", (text or "").lower()) + " "
    hits = Counter()
    for t in TITLES:
        n = len(re.findall(r"(?<![a-z])" + re.escape(t) + r"s?(?![a-z])", text))
        if n:
            hits[t] += n
            text = text.replace(" " + t + " ", " ")  # don't double count "network engineer" inside "senior network engineer"
    return [t for t, _ in hits.most_common()]


def builtin_profile(prof):
    d = prof.data or {}
    override = [r.strip() for r in str(d.get("target_roles", "")).replace("\n", ",").split(",") if r.strip()]
    cur = str(d.get("current_title", "")).strip()
    found = _find_titles(cur) + [t for t in _find_titles(str(d.get("summary", "")) + " " + str(d.get("experience", "")) + " " + (prof.resume_text or "")) if " " in t]
    roles = []
    for r in override + ([cur] if cur and len(cur) < 60 else []) + found:
        r = re.sub(r"\b(sr\.?|senior|jr\.?|junior|lead|principal)\b", "", r, flags=re.I).strip(" ,-/").lower()
        if r and r not in roles:
            roles.append(r)
    skills = [s.strip() for s in str(d.get("skills", "")).split(",") if s.strip()]
    if not skills:
        words = [w for w in re.findall(r"[a-z][a-z0-9+#.]{2,}", (prof.resume_text or "").lower()) if w not in STOP]
        skills = [w for w, _ in Counter(words).most_common(15)]
    return {"roles": roles[:3], "keywords": skills[:15], "seniority": seniority_from_years(d.get("years_experience")),
            "method": "profile override" if override else "built-in extractor"}


def derive_search_profile(prof, ai=None):
    base = builtin_profile(prof)
    if (prof.data or {}).get("target_roles") or ai is None or getattr(ai, "is_mock", True):
        return base
    try:
        from .profile import profile_summary
        out = ai.complete_json(
            "You are a career coach. From this candidate profile, infer the job titles they should search for on job boards "
            "(any profession, use common market titles, most relevant first), their top hard skills/keywords, and seniority.",
            'Return {"roles": [up to 4 job titles], "keywords": [up to 15], "seniority": "entry|mid|senior|lead"}\n\n'
            + profile_summary(prof)[:5000], 600)
        roles = [str(r).strip().lower() for r in out.get("roles", []) if str(r).strip()][:4]
        if roles:
            return {"roles": roles, "keywords": [str(k) for k in out.get("keywords", [])][:15] or base["keywords"],
                    "seniority": out.get("seniority") or base["seniority"], "method": "your AI (" + ai.cfg.provider + ")"}
    except Exception:
        pass
    return base
