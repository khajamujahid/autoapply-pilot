"""Transparent 0-100 match score: skill overlap 45%, text similarity 25%, experience 20%, seniority 10%."""
import math
import re
from collections import Counter

STOP = set("a an and or the to of in for on with at by from as is are be we you our your this that will have has it its not who".split())
SENIOR = {"intern": 0, "junior": 1, "associate": 1, "mid": 3, "senior": 5, "sr": 5, "staff": 8, "lead": 7, "principal": 10, "director": 10}


def toks(t):
    return [w for w in re.findall(r"[a-z][a-z0-9+#.]{1,}", (t or "").lower()) if w not in STOP]


def cosine(a, b):
    ca, cb = Counter(a), Counter(b)
    num = sum(ca[k] * cb[k] for k in ca.keys() & cb.keys())
    den = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    return num / den if den else 0.0


def score(prof, title: str, jd: str) -> dict:
    d = prof.data or {}
    jd_l = (title + "\n" + jd).lower()
    skills = [s.strip().lower() for s in str(d.get("skills", "")).split(",") if s.strip()]
    hit = [s for s in skills if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", jd_l)]
    skill_pct = len(hit) / len(skills) if skills else 0.0
    sim = cosine(toks((prof.resume_text or "") + " " + str(d.get("skills", ""))), toks(title + " " + jd))
    req = years_required(jd_l)
    try:
        yrs = float(d.get("years_experience") or 0)
    except ValueError:
        yrs = 0
    exp_fit = 1.0 if req is None else max(0.0, 1 - max(0, req - yrs) / max(req, 1))
    lvl = next((v for k, v in SENIOR.items() if re.search(rf"\b{k}\b", title.lower())), None)
    sen = 1.0 if lvl is None else max(0.0, 1 - abs(lvl - yrs) / 10)
    total = 100 * (0.45 * skill_pct + 0.25 * min(1.0, sim * 2.5) + 0.2 * exp_fit + 0.1 * sen)
    return {"score": round(total, 1), "matched_skills": hit, "missing_skills": [s for s in skills if s not in hit],
            "required_years": req, "similarity": round(sim, 3), "experience_fit": round(exp_fit, 2), "seniority_fit": round(sen, 2)}


_YRS = re.compile(r"(?<![\d$.,])(\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:or more\s+)?(?:years?|yrs?)\b"
                  r"(?![\s-]*(?:old|of age))", re.I)


def years_required(text: str):
    """Years of experience the posting asks for. Ignores ages, dollar amounts, '70 years of history' etc.
    Prefers numbers near 'experience'; caps at 20. Returns the smallest plausible requirement or None."""
    t = (text or "").lower()
    near, other = [], []
    for mm in _YRS.finditer(t):
        n = int(mm.group(1))
        if not 0 < n <= 20:
            continue
        ctx = t[max(0, mm.start() - 60): mm.end() + 60]
        if re.search(r"\b(?:founded|history|serving|in business|anniversary|old|age|legacy|over the (?:past|last))\b", ctx) \
                and "experience" not in ctx:
            continue
        (near if re.search(r"experien|minimum|at least|required|professional", ctx) else other).append(n)
    vals = near or other
    return min(vals) if vals else None
