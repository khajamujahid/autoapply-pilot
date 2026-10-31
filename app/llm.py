import re
"""Tailoring via any OpenAI-compatible endpoint; deterministic mock if no key."""
import httpx
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

RESUME_SYS = ("You tailor resumes. Rewrite the candidate's resume for the job: reorder and rephrase existing "
              "experience to emphasise relevant skills. NEVER invent employers, titles, dates, degrees or skills "
              "the candidate does not have. Output plain text resume only.")
CL_SYS = ("Write a concise (<=250 words), specific cover letter using only facts from the resume. "
          "No placeholders like [Company]. Plain text.")
QA_SYS = "Answer a job application question truthfully from the profile, in one short answer. If unknown, answer 'N/A'."


def chat(system: str, user: str, max_tokens=1500) -> str | None:
    if not LLM_API_KEY:
        return None
    r = httpx.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                   json={"model": LLM_MODEL, "temperature": 0.3, "max_tokens": max_tokens,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def tailor_resume(profile, title, company, jd, match) -> str:
    out = chat(RESUME_SYS, f"JOB: {title} at {company}\n\n{jd[:8000]}\n\nRESUME:\n{profile.resume_text[:8000]}")
    if out:
        return out
    hl = ", ".join(match.get("matched_skills", [])) or profile.skills
    return (f"{profile.first_name} {profile.last_name}\n{profile.email} | {profile.phone} | {profile.location}\n\n"
            f"TARGET ROLE: {title} — {company}\nKEY SKILLS FOR THIS ROLE: {hl}\n\n{profile.resume_text}").strip()


def cover_letter(profile, title, company, jd, match) -> str:
    out = chat(CL_SYS, f"JOB: {title} at {company}\n\n{jd[:6000]}\n\nRESUME:\n{profile.resume_text[:6000]}", 700)
    if out:
        return out
    skills = ", ".join(match.get("matched_skills", [])[:6]) or profile.skills
    return (f"Dear {company or 'Hiring'} team,\n\nI'm excited to apply for the {title} role. With "
            f"{profile.years_experience:g} years of experience and hands-on work with {skills}, I can contribute "
            f"quickly to your team.\n\nThank you for your consideration.\n\n{profile.first_name} {profile.last_name}")


def answer_question(profile, question: str) -> str:
    q = question.lower()
    if "sponsor" in q:
        return "Yes" if profile.needs_sponsorship == "yes" else "No"
    if "authori" in q or "legally" in q:
        return "Yes" if profile.work_authorized == "yes" else "No"
    if "country" in q:
        return profile.country or "United States"
    if re.search(r"previously (worked|been employed)|employment agreement|non-compete|restriction|relatives?", q):
        return "No"
    if "preferred name" in q or "prefer us to use" in q:
        return profile.first_name
    if "linkedin" in q:
        return profile.linkedin
    if "github" in q:
        return profile.github
    if "website" in q or "portfolio" in q:
        return profile.website or profile.github
    if "years" in q and "experience" in q:
        return f"{profile.years_experience:g}"
    if "location" in q or "city" in q or "where" in q:
        return profile.location
    out = chat(QA_SYS, f"PROFILE: {profile.first_name} {profile.last_name}, skills {profile.skills}, "
                       f"{profile.years_experience} yrs\nRESUME:\n{profile.resume_text[:4000]}\n\nQUESTION: {question}", 150)
    return out or "N/A"
