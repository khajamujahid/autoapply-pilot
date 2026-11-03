"""Per-job documents: requirement match (covered vs gaps), tailored resume, cover letter and application email,
chat refinement with version history. Truth rule: drafts use ONLY facts from the user's profile. Anything the job
asks for that the profile lacks is reported as a gap, never written into the documents.

Works with the user's own AI (any provider incl. local Ollama/LM Studio) and has a deterministic built-in fallback.
"""
import re
from sqlalchemy import select
from .ai import AIError
from .db import DocVersion, ChatMsg, now
from .profile import full_name

LANGS = {"en": "English", "es": "Spanish", "de": "German", "fr": "French", "pt": "Portuguese", "it": "Italian",
         "nl": "Dutch", "hi": "Hindi", "ar": "Arabic", "ur": "Urdu", "zh": "Chinese", "ja": "Japanese"}
HEAD_I18N = {
    "es": {"SUMMARY": "RESUMEN", "SKILLS": "HABILIDADES", "EXPERIENCE": "EXPERIENCIA", "EDUCATION": "EDUCACIÓN", "LANGUAGES": "IDIOMAS"},
    "de": {"SUMMARY": "PROFIL", "SKILLS": "KENNTNISSE", "EXPERIENCE": "BERUFSERFAHRUNG", "EDUCATION": "AUSBILDUNG", "LANGUAGES": "SPRACHEN"},
    "fr": {"SUMMARY": "PROFIL", "SKILLS": "COMPÉTENCES", "EXPERIENCE": "EXPÉRIENCE", "EDUCATION": "FORMATION", "LANGUAGES": "LANGUES"},
}
TONES = ["professional", "warm", "confident", "concise"]
STOP = set("""a an and or the to of in for on with at by from as is are be we you our your this that will have has it its not who
able ability strong excellent good experience years year plus preferred required requirement requirements including such
work working knowledge understanding familiarity proficiency proficient skills skill using use team teams other related etc
must should can may would within across least minimum one two three four five more new also any all their they them""".split())
REQ_HDR = re.compile(r"(requirements|qualifications|what you.?ll (need|bring)|what we.?re looking for|you have|you.?ll have|"
                     r"must.have|minimum|preferred|basic qualifications|who you are|skills|about you|ideal candidate)", re.I)


def _facts(prof):
    """Everything the user has told us: structured profile fields + resume text + parsed experience + languages."""
    from .profile import SIMPLE_FIELDS
    d = prof.data or {}
    parts = [f"{k}: {d.get(k)}" for k in SIMPLE_FIELDS if d.get(k)]
    for e in d.get("experience") or []:
        if isinstance(e, dict):
            parts.append(" ".join(str(v) for v in e.values()))
    for e in d.get("education") or []:
        if isinstance(e, dict):
            parts.append(" ".join(str(v) for v in e.values()))
    parts.append(prof.resume_text or "")
    parts.append(str((prof.answers or {}).get("languages", "")))
    return "\n".join(parts)


def _words(t):
    return [w for w in re.findall(r"[a-z][a-z0-9+#./-]{1,}", (t or "").lower()) if w not in STOP]


def _skills(prof):
    return [s.strip() for s in str((prof.data or {}).get("skills", "")).split(",") if s.strip()]


def _has(text, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text.lower()) is not None


# ------------------------------------------------------------------ requirements / match view
def extract_requirements_builtin(jd):
    raw = [l.strip() for l in (jd or "").splitlines()]
    out, on = [], False
    for r0 in raw:
        l = r0.strip(" \t-*•·●▪–")
        if not l:
            continue
        bullet = r0[:1] in "-*•·●▪–"
        if not bullet and REQ_HDR.search(l) and len(l) < 80 and (l.endswith(":") or len(l.split()) <= 6):
            on = True; continue
        if on and not bullet and len(l) < 80 and l.endswith(":"):
            on = bool(REQ_HDR.search(l)); continue
        if on and 3 <= len(l) <= 260:
            out.append(l)
    if len(out) < 3:  # no headed section: take requirement-like sentences
        sents = re.split(r"(?<=[.;])\s+|\n", jd or "")
        out = [x.strip(" -*•") for x in sents if re.search(r"\b(experience|knowledge|proficien|degree|certif|familiar|"
                                                             r"ability to|license|years)\b", x, re.I) and 15 < len(x) < 260]
    seen, res = set(), []
    for r in out:
        k = r.lower()[:60]
        if k not in seen:
            seen.add(k); res.append(r)
    return res[:14]


def cover_builtin(prof, reqs):
    ptxt = _facts(prof).lower()
    plines = [l.strip() for l in (prof.resume_text or "").splitlines() if len(l.strip()) > 20]
    skills = _skills(prof)
    res = []
    for r in reqs:
        hit = [s for s in skills if _has(r, s)]
        rw = set(_words(r)); pw = set(_words(ptxt))
        overlap = len(rw & pw) / len(rw) if rw else 0
        ym = re.search(r"(?<![\d$])([1-9]|1\d|20)\+?\s*years", r.lower())
        yrs_ok = True
        if ym:
            try:
                yrs_ok = float((prof.data or {}).get("years_experience") or 0) >= int(ym.group(1))
            except ValueError:
                yrs_ok = False
        covered = (yrs_ok and len(rw - {ym.group(1)}) <= 2) if ym else (bool(hit) or overlap >= 0.5)
        covered = covered or (yrs_ok and bool(hit))
        ev = ""
        if covered:
            if ym and not hit:
                ev = f"Profile: {(prof.data or {}).get('years_experience')} years of experience"
            elif hit:
                ev = "Skills: " + ", ".join(hit[:4])
            else:
                best = max(plines, key=lambda l: len(rw & set(_words(l))), default="")
                ev = best[:160]
        res.append({"text": r, "covered": covered, "evidence": ev if covered else
                    ("Needs " + ym.group(0) + "; profile shows " + str((prof.data or {}).get("years_experience") or 0) + " years"
                     if ym and not yrs_ok else "Not found in your profile")})
    return res


def match_requirements(prof, ai, title, jd):
    if ai and not ai.is_mock:
        try:
            out = ai.complete_json(
                "You compare a job posting with a candidate profile. List the posting's distinct requirements (max 14, short). "
                "For each, decide if the PROFILE covers it. Evidence must quote or closely paraphrase the profile; if the profile "
                "does not show it, covered=false and evidence explains what is missing. Be strict and honest.",
                'Return {"requirements":[{"text":"...","covered":true,"evidence":"..."}]}\n\nJOB: ' + title + "\n" + jd[:7000]
                + "\n\nPROFILE:\n" + _facts(prof)[:7000], 1800)
            reqs = [{"text": str(r.get("text", ""))[:260], "covered": bool(r.get("covered")), "evidence": str(r.get("evidence", ""))[:300]}
                    for r in out.get("requirements", []) if r.get("text")]
            if reqs:
                return reqs
        except AIError:
            pass
    return cover_builtin(prof, extract_requirements_builtin(jd))


# ------------------------------------------------------------------ drafting
def _contact(prof):
    d = prof.data or {}
    return " | ".join(x for x in [d.get("location"), d.get("email"), d.get("phone"), d.get("linkedin"), d.get("github"), d.get("website")] if x)


def _h(name, lang):
    return HEAD_I18N.get(lang, {}).get(name, name)


def resume_builtin(prof, title, company, reqs, lang="en"):
    d = prof.data or {}
    skills = _skills(prof)
    covered_txt = " ".join(r["text"] for r in reqs if r["covered"]).lower()
    ordered = sorted(skills, key=lambda s: (not _has(covered_txt, s), skills.index(s)))
    L = [full_name(prof) or "Your Name", title or d.get("current_title") or "", _contact(prof), "",
         _h("SUMMARY", lang) if lang != "en" else "PROFESSIONAL SUMMARY"]
    summ = d.get("summary") or ""
    if not summ and prof.resume_text:
        m = re.search(r"SUMMARY:?\s*(.+)", prof.resume_text, re.I)
        summ = m.group(1).strip() if m else ""
    summ = summ or f"{d.get('current_title', '')} with {d.get('years_experience', '')} years of experience.".strip()
    for s_ in ordered[:6]:  # bold the job's keywords the candidate really has
        if _has(covered_txt, s_):
            summ = re.sub(rf"(?<![*\w])({re.escape(s_)})(?![*\w])", r"**\1**", summ, count=1, flags=re.I)
    L.append(summ)
    if ordered:
        hit = [s_ for s_ in ordered if _has(covered_txt, s_)]
        rest = [s_ for s_ in ordered if s_ not in hit]
        L += ["", _h("SKILLS", lang) if lang != "en" else "TECHNICAL SKILLS"]
        if hit:
            L.append("Job-Relevant Skills: " + ", ".join(hit))
        if rest:
            L.append(("Additional Skills: " if hit else "Core Skills: ") + ", ".join(rest))
    exp = d.get("experience") or []
    L += ["", _h("EXPERIENCE", lang) if lang != "en" else "PROFESSIONAL EXPERIENCE"]
    if exp:
        for e in exp:
            dates = f"{e.get('start', '')} - {e.get('end', '')}".strip(" -")
            head = " | ".join(x for x in [e.get("company", ""), e.get("location", ""), dates] if x)
            if dates:
                L += [head, e.get("title", "")]
            else:
                L.append(f"{e.get('title', '')} - {e.get('company', '')}")
            hl = e.get("highlights") or []
            hl = hl if isinstance(hl, list) else [x for x in str(hl).split("\n") if x.strip()]
            hl = sorted(hl, key=lambda h: -sum(1 for s in ordered[:8] if _has(h, s)))
            L += ["- " + str(h).strip(" -") for h in hl]
    else:  # use the experience section of the uploaded resume verbatim
        txt = prof.resume_text or ""
        m = re.search(r"EXPERIENCE\s*\n(.+?)(\n(?:SKILLS|EDUCATION|CERTIFICATIONS|PROJECTS)\b|$)", txt, re.S | re.I)
        for l in (m.group(1) if m else txt).splitlines():
            l = l.strip()
            if l:
                l = re.sub(r"\s*\((\d{4}).?(\d{4}|present)\):\s*", r" (\1 - \2)\n", l, flags=re.I)
                for part in l.split("\n"):
                    if part.strip():
                        L.append(part if " (" in part and len(part) < 120 and not part.endswith(".") else "- " + part.strip())
    edu = d.get("education") or []
    if edu:
        L += ["", _h("EDUCATION", lang)]
        for e in edu:
            L += ([f"{e.get('school', '')} | {e.get('year')}", e.get("degree", "")] if e.get("year")
                  else [f"{e.get('degree', '')} - {e.get('school', '')}"])
    else:
        m = re.search(r"EDUCATION\s*\n(.+?)(\n[A-Z]{4,}\b|$)", prof.resume_text or "", re.S)
        if m:
            L += ["", _h("EDUCATION", lang)] + [l.strip() for l in m.group(1).splitlines() if l.strip()]
    return "\n".join(L).strip()


def cover_letter_builtin(prof, title, company, reqs, tone="professional", lang="en"):
    name = full_name(prof) or "Your Name"
    d = prof.data or {}
    cov = [r for r in reqs if r["covered"]][:3]
    pts = "\n".join(f"- {r['evidence']}" for r in cov) or "- " + ", ".join(_skills(prof)[:5])  # profile facts only
    opener = {"warm": "I was excited to see", "confident": "I'm a strong fit for", "concise": "I'm applying for"}.get(tone, "I am writing to apply for")
    return (f"Dear {company or 'Hiring'} Hiring Team,\n\n{opener} the {title} role at {company}. I bring "
            f"{d.get('years_experience') or 'several'} years of experience as {d.get('current_title') or 'a professional in this field'}.\n\n"
            f"Where my background lines up with what you need:\n{pts}\n\n"
            f"I'd welcome the chance to discuss how I can help your team. Thank you for your time and consideration.\n\n"
            f"Sincerely,\n{name}\n{d.get('email', '')}  {d.get('phone', '')}").strip()


def email_builtin(prof, title, company):
    d = prof.data or {}
    name = full_name(prof) or "Your Name"
    return (f"Subject: Application for {title} - {name}\n\nHello {company} team,\n\nPlease find attached my resume and cover "
            f"letter for the {title} position. My background as {d.get('current_title') or 'a professional'} with "
            f"{', '.join(_skills(prof)[:3]) or 'relevant experience'} matches the role well, and I'd be glad to talk.\n\n"
            f"Best regards,\n{name}\n{d.get('phone', '')}\n{d.get('linkedin', '')}").strip()


TRUTH = ("STRICT RULES: use ONLY facts present in the PROFILE (employers, titles, dates, degrees, skills, metrics). Never invent "
         "or inflate anything. Requirements listed as GAPS must NOT be claimed. You may reorder, select and rephrase. "
         "No placeholders or brackets.")


def _ai_doc(ai, kind, prof, title, company, jd, reqs, tone, lang):
    gaps = "\n".join("- " + r["text"] for r in reqs if not r["covered"]) or "(none)"
    cov = "\n".join(f"- {r['text']} <= {r['evidence']}" for r in reqs if r["covered"]) or "(none)"
    ctx = (f"JOB: {title} at {company}\n{jd[:6000]}\n\nCOVERED REQUIREMENTS:\n{cov}\n\nGAPS (do not claim):\n{gaps}\n\n"
           f"PROFILE:\n{_facts(prof)[:8000]}")
    lang_s = LANGS.get(lang, "English")
    if kind == "resume":
        return ai.complete(f"Write a tailored, ATS-friendly resume in {lang_s} for this job. Plain text. First line = full name, "
                           "second line = a headline matching the target role, third line = contact details separated by ' | '. Then exactly "
                           "these UPPERCASE sections in this order: PROFESSIONAL SUMMARY (3-4 justified lines, wrap the job's key "
                           "phrases the candidate really has in **double asterisks**), TECHNICAL SKILLS (one 'Category: item, item' "
                           "line per group, most relevant groups first), PROFESSIONAL EXPERIENCE (newest first; for each role a line "
                           "'Company | City, ST | Start - End', then the job title on its own line, then '- ' bullets), EDUCATION "
                           "('School | City, ST | Start - End' then the degree line). No certifications, projects or interests "
                           "sections. Keep every employer, title and date exactly as in the profile; only rewrite the summary, "
                           "skills emphasis and bullets toward the job's keywords. "
                           "Lead with what this job needs. " + TRUTH, ctx, 2200)
    if kind == "cover":
        return ai.complete(f"Write a {tone} cover letter in {lang_s}, 180-260 words, specific to this job, grounded in the covered "
                           "requirements. Greeting, 3 short paragraphs, sign-off with the candidate's name. " + TRUTH, ctx, 900)
    return ai.complete(f"Write a short application email in {lang_s} to send with the resume and cover letter attached. First line "
                       "'Subject: ...'. 80-130 words. " + TRUTH, ctx, 500)


def generate(s, a, prof, ai, kinds=("resume", "cover", "email"), tone="professional", lang="en"):
    """(Re)build match + drafts for an application; stores each as a new version. Returns list of warnings."""
    if not a.requirements:
        a.requirements = match_requirements(prof, ai, a.title, a.job_description or "")
    reqs = a.requirements or []
    warns, via = [], "built-in"
    for k in kinds:
        txt = None
        if ai and not ai.is_mock:
            try:
                txt = _ai_doc(ai, k, prof, a.title, a.company, a.job_description or "", reqs, tone, lang); via = ai.cfg.provider
            except AIError as e:
                warns.append(f"AI failed for {k}: {e}; used built-in")
        txt_builtin = not txt
        if not txt:
            txt = {"resume": lambda: resume_builtin(prof, a.title, a.company, reqs, lang),
                   "cover": lambda: cover_letter_builtin(prof, a.title, a.company, reqs, tone, lang),
                   "email": lambda: email_builtin(prof, a.title, a.company)}[k]()
        else:
            warns += [f"{k}: " + w for w in fact_check(prof, txt, a)]  # built-in drafts are assembled from profile text only
        save_version(s, a, k, txt, f"generated ({'built-in' if txt_builtin else via}, {tone}, {lang})")
    return warns


def fact_check(prof, text, a):
    """Flag terms a draft uses that the profile doesn't support: words taken from the posting but absent from the
    profile (the classic way drafts 'invent' experience), and tech-shaped tokens (digits / inner capitals) not in the profile."""
    base = (_facts(prof) + " " + (a.company or "") + " " + (a.title or "")).lower()
    jd = (a.job_description or "").lower()
    sus = set()
    for t in set(re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{1,}", text or "")):
        tl = t.lower().strip("./-")
        if len(tl) < 3 or tl in STOP or tl in base:
            continue
        techy = bool(re.search(r"\d|[a-z][A-Z]|[+#]", t)) or (t[0].isupper() and not t.isupper() and tl in jd)
        if techy and (tl in jd or re.search(r"\d|[a-z][A-Z]|[+#]", t)):
            sus.add(t)
    return ["terms not found in your profile, please check: " + ", ".join(sorted(sus)[:12])] if sus else []


# ------------------------------------------------------------------ versions + chat
def versions(s, app_id, kind):
    return list(s.scalars(select(DocVersion).where(DocVersion.app_id == app_id, DocVersion.kind == kind).order_by(DocVersion.id.desc())))


def save_version(s, a, kind, content, note):
    s.add(DocVersion(app_id=a.id, kind=kind, content=content, note=note[:300], created_at=now()))
    if kind == "resume":
        a.tailored_resume = content
    elif kind == "cover":
        a.cover_letter = content
    else:
        a.email_draft = content
    s.flush()


def undo(s, a, kind):
    vs = versions(s, a.id, kind)
    if len(vs) < 2:
        return False
    save_version(s, a, kind, vs[1].content, f"undo (back to v{len(vs) - 1})")
    return True


def restore(s, a, kind, vid):
    v = s.get(DocVersion, vid)
    if v and v.app_id == a.id and v.kind == kind:
        save_version(s, a, kind, v.content, f"restored version #{v.id}")
        return True
    return False


def _mock_refine(prof, text, msg):
    m = msg.lower().strip()
    base = (_facts(prof)).lower()
    if re.search(r"\b(shorter|shorten|concise|trim|one page)\b", m):
        lines, out, seen_b = text.splitlines(), [], 0
        for l in lines:
            if l.strip().startswith("- "):
                seen_b += 1
                if seen_b % 3 == 0:
                    continue
            out.append(l)
        return "\n".join(out), "Trimmed every third bullet to make it shorter."
    r = re.match(r"(remove|delete|drop)\s+(.+)", msg.strip(), re.I)
    if r:
        term = r.group(2).strip(" .'\"").lower()
        out = []
        for l in text.splitlines():
            if term in l.lower() and l.count(",") >= 2:
                out.append(", ".join(x.strip() for x in l.split(",") if x.strip().lower() != term)); continue
            if term in l.lower() and l.strip().upper() not in ("SUMMARY", "SKILLS", "EXPERIENCE", "EDUCATION"):
                continue
            out.append(l)
        return "\n".join(out), f"Removed '{term}'."
    r = re.match(r"(add|mention|include|highlight|emphasi[sz]e)\s+(.+)", msg.strip(), re.I)
    if r:
        term = r.group(2).strip(" .'\"")
        if term.lower() not in base:
            return text, (f"I can't add '{term}': it isn't in your profile, and I never invent experience. "
                          "Add it to your Profile first if it's true.")
        lines = text.splitlines()
        for i, l in enumerate(lines):
            if l.strip().upper() in ("SKILLS", "HABILIDADES", "KENNTNISSE", "COMPÉTENCES") and i + 1 < len(lines):
                items = [x.strip() for x in lines[i + 1].split(",")]
                orig = next((x for x in items if x.lower() == term.lower()), term)
                items = [orig] + [x for x in items if x.lower() != term.lower()]
                lines[i + 1] = ", ".join(items)
                return "\n".join(lines), f"Moved '{term}' to the front of your skills."
        return text + f"\n\n{term}", f"Added '{term}'."
    return text, ("Without an AI key I can: make it shorter, remove <word>, add/highlight <skill from your profile>. "
                  "Connect an AI (or local Ollama) in Settings for free-form edits.")


def refine(s, a, prof, ai, kind, message, lang="en"):
    cur = {"resume": a.tailored_resume, "cover": a.cover_letter, "email": a.email_draft}[kind] or ""
    s.add(ChatMsg(app_id=a.id, role="user", kind=kind, text=message[:2000], created_at=now()))
    if ai and not ai.is_mock:
        try:
            gaps = "\n".join("- " + r["text"] for r in (a.requirements or []) if not r["covered"]) or "(none)"
            new = ai.complete(f"You edit a job-application document per the user's request and return the FULL revised document "
                              f"only (no commentary), in {LANGS.get(lang, 'English')}. If the request asks to add something not in "
                              "the profile, do not add it. " + TRUTH,
                              f"REQUEST: {message}\n\nGAPS (never claim):\n{gaps}\n\nPROFILE:\n"
                              f"{_facts(prof)[:6000]}\n\nDOCUMENT:\n{cur}", 2200)
            reply = "Done. " + ("; ".join(fact_check(prof, new, a)) or "")
        except AIError as e:
            new, reply = cur, f"AI error: {e}"
    else:
        new, reply = _mock_refine(prof, cur, message)
    if new.strip() and new != cur:
        save_version(s, a, kind, new, "chat: " + message[:120])
    s.add(ChatMsg(app_id=a.id, role="ai", kind=kind, text=reply, created_at=now()))
    s.flush()
    return reply
