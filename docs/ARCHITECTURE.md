# Architecture

```
Browser ──► FastAPI app (app/main.py)          ./data/app.db       SQLite (or Postgres via DATABASE_URL)
  Jinja2 pages + small JSON API               ./data/uploads/     resumes (per user)
  session cookie auth, Google OAuth           ./data/artifacts/   tailored PDFs, screenshots (per user/app)
            │
            ▼
Worker (app/worker.py): DB-backed queue, in-process thread or a separate container
  every 2s:  claim QUEUED   → prepare()  fetch JD → detect ATS → score → AI explain → tailor resume (PDF) → cover letter
                                         → REVIEW, or APPROVED (review off / score ≥ auto-approve)
             claim APPROVED → apply()    Playwright submitter → DRY_RUN_DONE | APPLIED | NEEDS_MANUAL | FAILED
  every 60s: due saved searches → discovery → DISCOVERED (or QUEUED if score ≥ auto-queue)
```

## Modules
| File | Responsibility |
|---|---|
| `app/config.py` | `.env` loading, `./data` paths, auto-generated secrets |
| `app/db.py` | SQLAlchemy models: User, Settings, Profile, Credential, SavedSearch, Application |
| `app/auth.py` | bcrypt passwords, Google OAuth (Authlib), per-request user |
| `app/crypto.py` | Fernet encryption for API keys and site passwords |
| `app/ai.py` | **Provider abstraction** (xAI, Gemini, Anthropic, OpenAI, custom, mock) and every AI task |
| `app/profile.py` | Profile schema, standard answers, PDF/DOCX text extraction, rule-based answers |
| `app/answerer.py` | Answers a question: profile rules first, then AI; records every answer used |
| `app/ats.py`, `app/fetcher.py` | ATS detection from URL; JD fetch via public board APIs with HTML fallback |
| `app/roles.py` | Derives target roles, keywords and seniority from any resume (user's AI, or built-in cross-industry title taxonomy) |
| `app/discovery.py` | JobSpy (LinkedIn/Indeed/Glassdoor/ZipRecruiter/Google), The Muse, Remotive, Arbeitnow, Adzuna, USAJobs, company boards; run in parallel, role-title gate, location/date filter, dedupe (URL + company/title), fit ranking (35% title fit + 65% profile match), saved searches |
| `app/profile.py` | `QUESTION_SECTIONS` (Application Questions), `rule_answer` (exact + regex rules), `custom_answer` (fuzzy match against the user's Q&A), `add_unknown_questions` |
| `app/matcher.py` | Transparent 0–100 score: skills 45%, text similarity 25%, years 20%, seniority 10% |
| `app/pdf.py` | Tailored resume and cover-letter PDFs |
| `app/submitters/` | `base.py` generic engine (text, native selects, react-select comboboxes, radio/checkbox groups, cover-letter upload, CAPTCHA detection) plus per-ATS modules |

## Isolation and security
- Every query is scoped by `user_id`. Detail and file routes return 404 for other users' records, and files are served only from that user's artifact folder.
- Passwords are hashed with bcrypt. AI keys and site passwords are encrypted with Fernet. Session cookies are signed, `SameSite=Lax`, and HTTPS-only when `BASE_URL` is https.
- Dry-run and review are on by default. There is a daily limit per user. CAPTCHAs are detected and handed to the human, never bypassed.

## Extending
- **New ATS:** subclass `BaseSubmitter`, implement `fill_form()` for the fixed fields, and let `fill_generic()` handle the questions. Register it in `app/submitters/__init__.py` and add a URL pattern in `app/ats.py`.
- **New AI provider:** add an entry to `PROVIDERS` in `app/ai.py`. Use OpenAI-compatible endpoints as-is, or add a branch in `Provider.complete()`.
- **New job source:** add a function in `app/discovery.py` that returns job dicts, and wire it into `search()`.
