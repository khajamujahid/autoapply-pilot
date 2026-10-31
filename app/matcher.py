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
    m = re.search(r"(\d{1,2})\+?\s*(?:-\s*\d+\s*)?years?", jd_l)
    req = int(m.group(1)) if m else None
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
