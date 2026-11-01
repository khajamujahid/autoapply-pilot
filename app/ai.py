"""One provider abstraction for every AI task (Bring Your Own AI).

Providers: xai (Grok), gemini, anthropic, openai, custom (any OpenAI-compatible base URL), mock (offline, deterministic).
"""
import json
import re
from dataclasses import dataclass
import httpx
from .crypto import dec

PROVIDERS = {
    "xai": {"label": "Grok (xAI)", "base_url": "https://api.x.ai/v1", "model": "grok-4"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash"},
    "anthropic": {"label": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "ollama": {"label": "Ollama (local, private, no key)", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    "lmstudio": {"label": "LM Studio (local, no key)", "base_url": "http://localhost:1234/v1", "model": "local-model"},
    "custom": {"label": "Custom (OpenAI-compatible)", "base_url": "", "model": ""},
    "mock": {"label": "Mock (offline, no key)", "base_url": "", "model": "mock"},
}


class AIError(Exception):
    pass


@dataclass
class AIConfig:
    provider: str = "mock"
    api_key: str = ""
    model: str = ""
    base_url: str = ""

    @classmethod
    def from_settings(cls, st):
        p = st.ai_provider or "mock"
        d = PROVIDERS.get(p, PROVIDERS["mock"])
        return cls(p, dec(st.ai_api_key_enc), st.ai_model or d["model"], (st.ai_base_url or d["base_url"]).rstrip("/"))


class Provider:
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg

    @property
    def is_mock(self):
        return self.cfg.provider == "mock"

    def complete(self, system: str, user: str, max_tokens=1500, json_mode=False) -> str:
        c = self.cfg
        if self.is_mock:
            return MockBrain.complete(system, user)
        if not c.api_key and c.provider not in ("custom", "ollama", "lmstudio"):
            raise AIError("No API key set. Add one in Settings.")
        try:
            if c.provider == "anthropic":
                r = httpx.post(f"{c.base_url}/messages", timeout=120, headers={
                    "x-api-key": c.api_key, "anthropic-version": "2023-06-01"},
                    json={"model": c.model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": user}]})
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"]).strip()
            body = {"model": c.model, "temperature": 0.2, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if json_mode and c.provider in ("openai", "xai"):
                body["response_format"] = {"type": "json_object"}
            r = httpx.post(f"{c.base_url}/chat/completions", timeout=300 if c.provider in ("ollama", "lmstudio") else 120,
                           headers={"Authorization": f"Bearer {c.api_key}"} if c.api_key else {}, json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            raise AIError(f"{c.provider} HTTP {e.response.status_code}: {e.response.text[:300]}")
        except httpx.HTTPError as e:
            raise AIError(f"{c.provider} connection error: {e}")

    def complete_json(self, system, user, max_tokens=2000) -> dict:
        txt = self.complete(system + "\nRespond with ONLY valid JSON, no prose, no code fences.", user, max_tokens, True)
        m = re.search(r"\{.*\}", txt, re.S)
        try:
            return json.loads(m.group(0) if m else txt)
        except Exception:
            raise AIError(f"Model did not return JSON: {txt[:200]}")

    def test(self) -> str:
        out = self.complete("You are a connectivity check.", "Reply with exactly: OK", 10)
        return out[:100]

    # ---------------- tasks -----------------
    def parse_resume(self, text: str) -> dict:
        from .profile import FIELDS
        if self.is_mock:
            return MockBrain.parse_resume(text)
        keys = ", ".join(FIELDS)
        return self.complete_json(
            f"Extract a structured candidate profile from the resume. JSON keys: {keys}. "
            "skills = comma-separated string; years_experience = number; summary = 2-3 sentences; "
            "experience = list of {company,title,start,end,highlights}; education = list of {school,degree,year}. "
            "Use empty string when unknown. Never invent data.", text[:15000])

    def tailor_resume(self, profile_text, job_title, company, jd, match) -> str:
        if self.is_mock:
            return MockBrain.tailor(profile_text, job_title, company, match)
        return self.complete(
            "You tailor resumes for a specific job. Reorder and rephrase the candidate's REAL experience to emphasise "
            "what this job needs. NEVER invent employers, titles, dates, degrees, metrics or skills. Plain text with "
            "sections: NAME/CONTACT, SUMMARY, SKILLS, EXPERIENCE, EDUCATION.",
            f"JOB: {job_title} at {company}\n{jd[:8000]}\n\nCANDIDATE RESUME:\n{profile_text[:8000]}", 2000)

    def cover_letter(self, profile_text, name, job_title, company, jd, match) -> str:
        if self.is_mock:
            return MockBrain.cover(name, job_title, company, match)
        return self.complete(
            "Write a specific, warm cover letter under 230 words using only facts from the resume. "
            "No placeholders, no brackets. Sign with the candidate's name.",
            f"JOB: {job_title} at {company}\n{jd[:6000]}\n\nRESUME:\n{profile_text[:6000]}\nNAME: {name}", 700)

    def explain_match(self, profile_text, job_title, jd, match) -> str:
        if self.is_mock:
            return MockBrain.explain(match)
        return self.complete("In 3 short bullet points, explain how well this candidate fits the job and the main gap. Be honest.",
                             f"JOB: {job_title}\n{jd[:5000]}\n\nRESUME:\n{profile_text[:5000]}\nSCORE: {match}", 300)

    def answer(self, question: str, options: list[str] | None, profile_summary: str, multi=False) -> str | list[str]:
        """Answer an application question. With options returns one (or several if multi) of them verbatim."""
        if self.is_mock:
            return MockBrain.answer(question, options, multi)
        if options:
            sys = ("Pick the answer(s) a truthful candidate would choose for this job application question. "
                   f"Return JSON {{\"answer\": {'[list of exact option strings]' if multi else '\"exact option string\"'}}}. "
                   "For voluntary demographic questions choose the 'decline to answer' style option.")
            out = self.complete_json(sys, f"PROFILE:\n{profile_summary}\n\nQUESTION: {question}\nOPTIONS: {json.dumps(options)}", 200)
            return out.get("answer", [] if multi else "")
        return self.complete("Answer this job application question truthfully and briefly (1-3 sentences, or a single "
                             "value if it asks for one) using only the profile. If not knowable, reply N/A.",
                             f"PROFILE:\n{profile_summary}\n\nQUESTION: {question}", 250)


class MockBrain:
    """Offline deterministic stand-in so the whole product works with no API key (and for tests)."""

    @staticmethod
    def complete(system, user):
        if "connectivity" in system:
            return "OK"
        return "Mock response."

    @staticmethod
    def parse_resume(text):
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        phone = re.search(r"\+?\d[\d\s().-]{8,}\d", text)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        name = lines[0] if lines else ""
        first, _, last = name.partition(" ")
        li = re.search(r"(https?://)?(www\.)?linkedin\.com/in/[\w-]+", text)
        gh = re.search(r"(https?://)?(www\.)?github\.com/[\w-]+", text)
        sk = re.search(r"SKILLS\s*\n(.+)", text, re.I)
        yrs = re.search(r"(\d+)\+?\s+years", text, re.I)
        loc = re.search(r"\|\s*([A-Z][a-zA-Z .]+,\s*[A-Z]{2})", text)
        return {"first_name": first, "last_name": last, "email": email.group(0) if email else "",
                "phone": phone.group(0).strip() if phone else "", "location": loc.group(1).strip() if loc else "",
                "linkedin": li.group(0) if li else "", "github": gh.group(0) if gh else "", "website": "",
                "current_title": "", "current_company": "", "years_experience": int(yrs.group(1)) if yrs else 0,
                "skills": sk.group(1).strip() if sk else "", "summary": " ".join(lines[1:4])[:400],
                "experience": [], "education": []}

    @staticmethod
    def tailor(profile_text, title, company, match):
        hl = ", ".join(match.get("matched_skills", []))
        return f"TARGET: {title} at {company}\nMOST RELEVANT SKILLS: {hl or 'see below'}\n\n{profile_text}"

    @staticmethod
    def cover(name, title, company, match):
        sk = ", ".join(match.get("matched_skills", [])[:5]) or "my core skills"
        return (f"Dear {company or 'Hiring'} team,\n\nI'm applying for the {title} role. My background with {sk} "
                f"maps closely to what you're looking for, and I'd love to bring that experience to your team.\n\n"
                f"Thank you for your time and consideration.\n\n{name}")

    @staticmethod
    def explain(m):
        return (f"- Matched skills: {', '.join(m.get('matched_skills', [])) or 'none found'}\n"
                f"- Missing: {', '.join(m.get('missing_skills', [])[:5]) or 'nothing major'}\n"
                f"- Experience fit {m.get('experience_fit')}, seniority fit {m.get('seniority_fit')}")

    @staticmethod
    def answer(q, options, multi=False):
        if options:
            ql = q.lower()
            pref = []
            if re.search(r"gender|race|ethnic|hispanic|veteran|disab|orientation|pronoun", ql):
                pref = ["decline", "prefer not", "don't wish", "do not wish", "not to"]
            elif re.search(r"privacy|consent|acknowledge|agree|certify|terms", ql):
                pref = ["yes", "agree", "i acknowledge", "acknowledge", "i agree", "confirm"]
            elif re.search(r"sponsor", ql):
                pref = ["no"]
            elif re.search(r"authori|legally|eligible", ql):
                pref = ["yes"]
            elif re.search(r"language", ql):
                pref = ["english"]
            for p in pref:
                for o in options:
                    if o.lower().startswith(p) or p in o.lower():
                        return [o] if multi else o
            return [] if multi else ""  # unknown: leave blank for the human (conservative)
        return "N/A"
