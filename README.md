# autoapply-pilot

A self-hosted job auto-apply pilot. Paste job URLs. For each one it fetches the job description, detects the ATS, scores the match against your profile, and tailors your resume and cover letter with any OpenAI-compatible LLM. It then **pauses for your review**. After you approve, Playwright fills the application form on **Greenhouse, Lever and Ashby** (Workday is best-effort).

**Safe by default:** `DRY_RUN=true` fills the form and saves a full-page screenshot, but it **never clicks submit**.

## Pipeline
```
QUEUED → OPTIMIZING_RESUME → OPTIMIZING_CL → REVIEW ──(you approve)──> APPROVED → APPLYING → DRY_RUN_DONE | APPLIED
                                                                        any step → FAILED (with error code + screenshots) → Retry
(below MIN_MATCH_SCORE → SKIPPED)
```

## Quick start (local, SQLite and an in-process queue; no Docker needed)
```bash
git clone https://github.com/khajamujahid/autoapply-pilot && cd autoapply-pilot
cp .env.example .env        # optionally set LLM_API_KEY; without it a deterministic mock tailor is used
./run.sh                    # creates .venv, installs deps + Chromium, serves on :8077
```
Open http://localhost:8077, then:
1. Fill in your profile and click **Save profile**.
2. Upload your resume (PDF or TXT).
3. Paste job URLs and click **Queue**.
4. Open an application and read the match, tailored resume and cover letter. Edit them if needed, then click **Approve**.
5. Check the screenshots of the filled form. To submit for real, set `DRY_RUN=false` and restart.

## Docker
```bash
cp .env.example .env && docker compose up --build
```
This runs the API and a separate worker sharing a volume.

## Using Grok / other LLMs
Set `LLM_BASE_URL=https://api.x.ai/v1`, `LLM_MODEL=grok-...` and `LLM_API_KEY=...`. Any `/chat/completions`-compatible endpoint works (OpenAI, OpenRouter, Ollama). The prompts tell the model never to invent experience.

## API
| Method | Path | |
|---|---|---|
| GET/PUT | `/api/v1/profile` | profile |
| POST | `/api/v1/profile/resume` | upload resume (multipart `file`) |
| POST | `/api/v1/job-description/fetch` | `{url}` → ats, title, company, description |
| POST | `/api/v1/applications` | `{urls:[...]}`; duplicates rejected (`DUPLICATE_APPLICATION`) |
| GET | `/api/v1/applications/list`, `/list/counts`, `/{id}` | tracking |
| POST | `/api/v1/applications/{id}/approve` | REVIEW → APPROVED (optional edited resume/cover letter) |
| POST | `/api/v1/applications/{id}/retry` | retry a failed one |

## Layout
```
app/ats.py          ATS detection (Greenhouse, Lever, Ashby, Workday + others recognised)
app/fetcher.py      JD fetch via public board APIs (Greenhouse boards-api, Lever postings, Ashby posting-api) + HTML fallback
app/matcher.py      0-100 match score: skill overlap 45%, text similarity 25%, years 20%, seniority 10%
app/llm.py          tailoring, cover letter, screening-question answers (LLM or mock)
app/worker.py       DB-backed queue + state machine (run in-process or `python -m app.worker`)
app/submitters/     Playwright form fillers per ATS
app/static/         dashboard
```

## Known limitations
- CAPTCHAs (reCAPTCHA or hCaptcha on submit) are not solved. A run that hits one is marked FAILED, and you finish it manually.
- Checkbox and radio questions, and voluntary EEO questions, are left blank on purpose. Unknown required questions may block a real submit, so check the screenshot.
- Workday needs an existing account per company (email and password in the profile). It logs in and fills the first pages only. Dry-run stops at the login screen.
- Single user. The Workday password is stored in plain text in the local SQLite database, so keep the `data/` directory private.
- Respect each site's terms and apply only to jobs you actually want.
