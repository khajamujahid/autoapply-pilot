# Changelog

## 1.6.0
- **Universal form filler**: every ATS without a dedicated submitter (iCIMS, SmartRecruiters, Taleo, SuccessFactors, UltiPro/UKG, Avature, ContactHR, Workable, BambooHR, JazzHR, Paylocity, company career sites ...) is now attempted instead of failing as "unsupported". It clicks Apply, follows new tabs, iframes and redirects to the real ATS, fills contact fields, Application Questions and AI answers, uploads the tailored resume and walks multi-step wizards. If the Apply button leads to Greenhouse/Lever/Ashby/Workday it switches to that submitter.
- Short links (grnh.se, tracking links) are resolved; LinkedIn/Glassdoor/Indeed pages are searched for the employer's apply link.
- Every non-submission has an actionable reason code: ACCOUNT_REQUIRED, CAPTCHA, JOB_CLOSED, AGGREGATOR_EASY_APPLY, REQUIRED_UNANSWERED, NO_FORM_FOUND, SITE_UNREACHABLE, TIMEOUT, VALIDATION. Never a fake Applied, never a CAPTCHA bypass.
- **Open & prefill** button (self-hosted): opens a visible browser on the real form, fully filled, for you to review/solve CAPTCHA/submit.
- **Failure monitor** at /monitor: counts by status, reason and ATS host plus the latest 50 failures.
- Fix: "Years required" parsing (ignores "70 years of history", ages, money; caps at 20; prefers numbers next to "experience").
- Match is recomputed after tailoring (shown in the log as before → after). Tailoring still never invents employers, titles, dates or degrees.
- Speed: parallel apply workers (APPLY_WORKERS, default 3), one reused Chromium per worker, images/fonts blocked on forms, discovery capped by DISCOVERY_BUDGET (default 45s) with 24 parallel fetches.

## 1.5.0
- Find new jobs now also scans **10,448 recently active company career sites** (Greenhouse 4,565, Ashby 2,449, Workable 1,586, Lever 1,247, Recruitee 601). Each board answered its public API and had at least one posting in the last 120 days when the list was built (app/data/career_boards.json).
- Parallel scan (64 connections, 8s per-board timeout, time budget, 30-min cache), filtered by your resume's roles, location and date; matches are ranked against your resume.
- Hosted: each pass has a ~32s budget and covers part of the list; the dashboard says how many boards were scanned and offers "Scan next batch". Self-hosted: 240s budget covers the full list (CAREER_SITES_BUDGET).

## 1.4.0
- New main view: job cards with top filters, pipeline stats (Recommended / Reviewing / Applying / Applied / Failed) and a one-click **Auto Apply** that queues every job shown.
- Honest status flow Reviewing -> Applying -> Applied | Failed. Applied only after a real submission; unsupported sites, missing fields and the hosted demo show Failed with the reason (no "needs manual" limbo, no applied date on failures).
- Auto Apply tailors each job's keywords into the ATS resume format before applying; profile page offers the uploaded resume converted to ATS format.
- 13 AI providers: OpenAI (ChatGPT), Anthropic Claude, Google Gemini, Grok (xAI), DeepSeek, Mistral, Groq, Together, OpenRouter, Perplexity, Cohere, ChatGPT-compatible custom endpoint, Ollama and LM Studio.
- Old dashboard moved to /tracker.

## 1.3.1
- Hosted mode (`HOSTED=1`, auto-on for Vercel): Postgres URL normalisation, `/tmp` data dir, inline document drafting, auto-apply disabled with a clear note. Live demo at https://applypilot-jobs.vercel.app.

## 1.3.0 (2026-11-02) — production hardening
- Security: cross-site POST protection, login throttling, security headers, hardened session cookie, upload size/type checks with atomic save, API docs disabled.
- Reliability: clear errors for unreadable/empty/scanned resumes; additive DB migrations tested on old SQLite; JobSpy 429s and source failures isolated.
- Ops: structured JSON logging with request ids, `/healthz` checks the database, pinned requirements + `requirements-dev.txt`, pip-audit clean.
- Quality: 50+ offline pytest tests (~90% coverage of core modules) and `tests/angles.py` release gate (unit, regression, security, reliability, UX, performance, cross-role).
- Fixes: drafts now see structured profile skills even when a resume is uploaded; location filter uses word boundaries ("NY" no longer matches "Germany"); "Staff Accountant" keeps its title; removed dead code and obsolete scripts.

## 1.2.0 (2026-11-01)
- Per-job tailored resume, cover letter and application email from profile facts only, with a fact check.
- Match view: posting requirements vs. profile (covered with evidence / gaps).
- Chat refinement with version history, undo and restore; manual edits are versions too.
- 10 ATS-friendly PDF themes for resume and cover letter; auto-apply uploads the current themed PDFs.
- Tracker board / table / funnel with Interview, Offer, Rejected, Withdrawn stages; paste-a-posting drafts.
- Optional JSearch and Arbeitsagentur sources; Adzuna keys in Settings. Ollama and LM Studio providers. 12 output languages and 4 tones.
- Clean-room implementation inspired by the feature list of Dossier (AGPL-3.0); no Dossier code is used.

## 1.1.0 (2026-10-31)
- Discovery works for any profession. Roles/keywords/seniority are derived from the resume (your AI key, or a built-in extractor). New sources: LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs (python-jobspy, MIT), The Muse, optional Adzuna and USAJobs. Filters reduced to location/remote + date posted. Per-source counts shown on the Discover page.
- New Application Questions page (onboarding step after resume upload, linked from Settings) covering the common Workday/Greenhouse/Lever/Ashby/iCIMS questions plus a custom Q&A list. Fuzzy question matching in the form-filler; unknown questions are saved to the user's Q&A automatically.
- Profile: new *Target roles* field to override inferred roles.

## 1.0.0
- First release.
