<p align="center"><img src="app/static/logo.svg" width="72" alt="ApplyPilot logo"></p>
<h1 align="center">ApplyPilot</h1>
<p align="center"><b>Self-hosted job auto-apply. Bring your own AI. Your data never leaves your machine.</b></p>

ApplyPilot finds jobs, scores how well each one matches you, and tailors your resume (as a PDF) and a cover letter with the AI **you** choose (Grok, Gemini, Claude, OpenAI or any OpenAI-compatible model). It puts each application in a review queue, then fills the application on **Greenhouse, Lever and Ashby**, with **Workday** as best-effort, using a real browser. It answers dropdowns, radio buttons, checkboxes and custom questions from your saved answers plus your AI.

> 🧪 **Safe by default:** every account starts in **dry-run mode**. Forms are filled and screenshotted but never submitted, and each job waits for your review. You switch these off in Settings when you're ready.

![Tracker](docs/screenshots/tracker.svg)

## Features
- **Accounts:** email/password sign-up and **Sign in with Google**. Every user's data is fully isolated.
- **Bring your own AI:** pick a provider, paste a key, choose a model, and click **Test connection**. Keys are encrypted at rest (Fernet). One provider layer handles resume parsing, tailoring, cover letters, question answers and match explanations. A built-in **mock** mode works with no key at all.
- **Profile builder:** upload a PDF or DOCX resume and the AI fills in your profile. Add standard answers (work authorization, sponsorship, relocation, salary, notice, how you heard, and EEO questions, which default to *decline*). An **encrypted credential store** holds Workday logins.
- **Tailored documents per job (v1.2):** for every job ApplyPilot drafts a tailored resume, a cover letter and an application email, using **only facts from your profile**. A built-in fact check flags any term the draft uses that your profile doesn't support.
- **Match view (v1.2):** the posting's requirements side by side with your profile. Each one is marked *covered* (with the evidence from your profile) or *gap*. Gaps are never written into your documents.
- **Refine in chat, with undo and history (v1.2):** ask for changes ("make it shorter", "highlight NetSuite", "more formal"). Every change, manual edit or regeneration is a new version; undo or restore any earlier one. Without an AI key the built-in editor handles shorten / remove / highlight and refuses to add anything not in your profile.
- **10 ATS-friendly PDF themes (v1.2):** Classic, Modern, Minimal, Compact, Executive, Serif, Slate, Emerald, Centered, Monospace. Real selectable text, one column, no tables or images. Download resume and cover letter PDFs; auto-apply uploads the current version in your chosen theme.
- **Tracker board, table and funnel (v1.2):** Draft → Applied → Interview → Offer → Closed, plus "paste a job posting" to draft documents for any job, even without a link.
- **More sources and models (v1.2):** optional JSearch (RapidAPI key) and Bundesagentur für Arbeit (Germany, opt-in) job sources; Adzuna keys can be set in Settings. **Ollama** and **LM Studio** local models (nothing leaves your machine) alongside Grok, Gemini, Claude, OpenAI and any OpenAI-compatible API. Documents in 12 output languages (headings translated in built-in mode; full text with an AI model), with a tone of your choice.
- **Discover (any profession, v1.1):** ApplyPilot reads your resume and works out the roles to search for (software engineer, network engineer, nurse, accountant, electrician... anything), using your own AI key when set and a built-in title/keyword extractor otherwise. It then searches **LinkedIn, Indeed, Glassdoor, ZipRecruiter and Google Jobs** (via the MIT-licensed [python-jobspy](https://github.com/speedyapply/JobSpy)), **The Muse**, **Remotive**, **Arbeitnow**, optional **Adzuna** and **USAJobs** (free keys), and any Greenhouse/Lever/Ashby company boards you add. Results are de-duplicated and ranked by fit. **The only filters are location (or Remote) and date posted.** Override the roles any time with *Profile → Target roles*. Saved searches can run on a schedule and auto-queue jobs above a score.
- **Application Questions (v1.1):** answer the questions every Workday/Greenhouse/Lever/Ashby/iCIMS form asks once (legal/preferred name, address, links, work authorization, sponsorship, citizenship and export control, EEO with *Decline*, 18+, relocation, remote preference, start date, notice, salary, education, years, previous employment, relatives, non-compete, background check, clearance, languages, how you heard) plus your own custom Q&A. Shown during onboarding after resume upload and editable from Settings. The form-filler matches questions exactly and fuzzily, uses your AI when a key is set, and saves any new question it meets to your Q&A so you answer it only once.
- **Pipeline:** `QUEUED → OPTIMIZING_RESUME → OPTIMIZING_CL → REVIEW → APPROVED → APPLYING → APPLIED | DRY_RUN_DONE | NEEDS_MANUAL | FAILED`
  - Options: auto-approve above score X, skip below score Y, and a daily limit per user.
  - **CAPTCHAs are never bypassed.** The job becomes **NEEDS_MANUAL** with a link so you can finish it yourself.
- **Tracker:** status counts, a filterable table, bulk actions, per-application detail with the tailored resume and cover letter (editable before approval), the answers used, screenshots and logs, and **CSV export**.

## Quick start
Full walkthrough with screenshots: **[docs/QUICKSTART.md](docs/QUICKSTART.md)**. How it works: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Option A: Docker (one command)
```bash
cp .env.example .env          # optional: edit it
docker compose up --build     # → http://localhost:8077
```
This starts the web app and a background worker. All data is in `./data`.

### Option B: No Docker (Python 3.10+)
macOS / Linux:
```bash
bash install.sh   # creates .venv, installs packages + Chromium, creates .env
bash start.sh     # → http://localhost:8077
```
Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
.\start.ps1      # or double-click start.bat
```

Then open http://localhost:8077 and **create an account**:
1. Go to **Profile** and upload your resume.
2. In **Settings**, connect your AI.
3. Open **Discover**, or paste job links on the Tracker.
4. Review the tailored resume and cover letter, then **Approve**.

## Bring your own AI

Local models: install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b`, then pick **Ollama** in Settings (base URL `http://localhost:11434/v1`, no key) and press *Test connection*. From Docker use `http://host.docker.internal:11434/v1`.

| Provider | Where to get a key | Default model |
|---|---|---|
| Grok (xAI) | https://console.x.ai | `grok-4` |
| Google Gemini | https://aistudio.google.com/apikey | `gemini-2.5-flash` |
| Anthropic Claude | https://console.anthropic.com | `claude-sonnet-4-5` |
| OpenAI | https://platform.openai.com/api-keys | `gpt-4o-mini` |
| Custom | any OpenAI-compatible `/v1` URL (Ollama, LM Studio, OpenRouter, vLLM…) | your choice |
| Mock | no key; offline rules. Leaves unknown questions blank for you | – |

Each user sets their own provider in **Settings**. Calls go straight from your machine to the provider you pick.

## Google sign-in
1. Go to https://console.cloud.google.com/, then **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and your email, and add yourself as a test user (or publish the app).
2. Open **Credentials → Create credentials → OAuth client ID → Web application**.
   - **Authorized JavaScript origin:** `http://localhost:8077` (or your domain)
   - **Authorized redirect URI:** `http://localhost:8077/auth/google/callback`
3. Put the values in `.env`:
   ```
   BASE_URL=http://localhost:8077
   GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxxxxxx
   ```
4. Restart. The **Continue with Google** button appears on the sign-in page.

If you host ApplyPilot for other people, put it behind HTTPS (Caddy, nginx or a Cloudflare tunnel), set `BASE_URL=https://your.domain`, and add that redirect URI in Google.

## Data, privacy and storage
- Everything is in `./data`:
  - `app.db`: SQLite database
  - `uploads/`: resumes
  - `artifacts/`: tailored PDFs and screenshots
  - `.secret_key` and `.encryption_key`: auto-generated on first run if they're not set in `.env`
- **Back up `./data` and keep it private.** If you lose `.encryption_key`, saved API keys and passwords can't be decrypted.
- **Postgres (optional):** run `docker compose --profile postgres up`.
  - Set `DATABASE_URL=postgresql+psycopg://applypilot:applypilot@postgres:5432/applypilot` in `.env`.
  - Without Docker, use `@localhost:5432`.
- Set `ALLOW_SIGNUP=false` after creating your accounts if the app is reachable by others.

## Configuration (`.env`)

Optional job-source keys (also settable per user in Settings): `JSEARCH_API_KEY` (RapidAPI JSearch), `ARBEITSAGENTUR=on` (opt-in; no official API, its terms don't allow automated access), and free: `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (https://developer.adzuna.com) and `USAJOBS_API_KEY` / `USAJOBS_EMAIL` (https://developer.usajobs.gov). Without them those two sources are skipped; all others need no key.

| Variable | Default | Purpose |
|---|---|---|
| `BASE_URL` | `http://localhost:8077` | Public URL (needed for Google sign-in) |
| `PORT` | `8077` | Port for start scripts / compose |
| `DATABASE_URL` | SQLite in `./data` | Postgres URL if wanted |
| `SECRET_KEY`, `ENCRYPTION_KEY` | auto-generated | Session and encryption keys |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | – | Enable Google sign-in |
| `ALLOW_SIGNUP` | `true` | Allow new accounts |
| `HEADLESS` | `true` | Set `false` to watch the browser fill forms |
| `RUN_WORKER_IN_PROCESS` | `true` | Run the worker inside the web app (`false` when using a separate worker) |

Per-user options (dry-run, review, auto-approve threshold, minimum score, daily limit) are in **Settings** in the app.

## Tests
```bash
.venv/bin/pip install pytest
RUN_WORKER_IN_PROCESS=false .venv/bin/python -m pytest -q tests/test_units.py
```
The unit tests check ATS detection, encryption, scoring and answer rules. They also run a local form with a fake CAPTCHA to confirm it goes to NEEDS_MANUAL.

The end-to-end test runs two users, BYO-AI through a mock OpenAI-compatible server, discovery, review and dry-run:
```bash
.venv/bin/python tests/mock_ai_server.py &   # :8099
./start.sh &                                  # :8077
.venv/bin/python tests/e2e.py <greenhouse-url> <lever-url> <ashby-url>
```

## Responsible use
Apply only to jobs you actually want, and keep your answers truthful. The AI is told never to invent experience, but you should still review its output. Respect each site's terms. ApplyPilot never solves or bypasses CAPTCHAs, and dry-run is on by default.

## License
MIT. See [LICENSE](LICENSE).
