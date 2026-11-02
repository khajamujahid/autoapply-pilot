# Changelog

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
