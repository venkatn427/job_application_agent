# Dice Easy Apply — Playbook (Playwright MCP, agent-driven)

This is the runbook the agent (Claude) follows to submit a Dice application via the
Playwright MCP browser tools. It is adaptive: Dice's DOM changes, so rely on
`browser_snapshot` to read the live page rather than hard-coded selectors.

## Inputs
- `submission/profile.json` — the candidate data used to fill fields.
- `submission/settings.yaml` — run controls (dry_run, pause_before_submit, caps).
- A list of target job URLs (from `dice_search`, `matched_jobs`, or `manual_urls`).

## One-time setup (per session)
1. `browser_navigate` → https://www.dice.com/dashboard/login
2. `browser_snapshot`. If already logged in (dashboard visible), continue.
   Otherwise PAUSE and ask the user to log in manually in the opened browser
   (handles SSO / MFA / captcha). Do not type credentials unless the user
   explicitly provides them.
3. Confirm the resume in `profile.documents.resume_path` exists locally — it is
   needed for any "upload resume" step.

## Per-job loop (repeat up to settings.max_applications_per_run)
1. `browser_navigate` to the job URL.
2. `browser_snapshot`. Extract: job title, company, location, and whether an
   **"Easy apply"** button is present.
   - If the apply button redirects to an external ATS (no Dice modal), and
     `easy_apply_only: true`, SKIP and log `status: skipped_external`.
   - If title matches any `exclude_titles`, SKIP and log `status: skipped_filtered`.
3. Click **Easy apply**. `browser_snapshot` the modal.
4. Walk the modal steps. Typical Dice flow:
   - **Resume step** — choose the existing uploaded resume if present, else
     `browser_file_upload` with `profile.documents.resume_path`.
   - **Contact step** — name / email / phone / location, pulled from
     `profile.personal`. Use `browser_fill_form` for batch fields.
   - **Screening questions** — for each question, match its text (case-insensitive
     substring) against `profile.screening_defaults`. Resolve `use ...` references
     to `profile.json` values. If no confident match, PAUSE and ask the user.
   - **Work authorization** — from `profile.work_authorization`.
   - **EEO / voluntary self-id** — use `profile.eeo` (default: decline).
5. After all steps are filled, `browser_snapshot` + `browser_take_screenshot`.
   - If `settings.dry_run` → STOP here, log `status: dry_run`, do NOT submit.
   - If `settings.pause_before_submit` → show the user the screenshot + a summary
     of every field value, and WAIT for explicit "submit" confirmation.
6. Click **Submit**. `browser_snapshot` to confirm the success state
   (e.g. "Application submitted" / "Applied" badge).
7. Log the result to `submission/submissions.json` via `submitter.py log`.
8. Wait `settings.delay_between_apps_seconds`, then next job.

## Field-mapping rules
- Names: split `personal.first_name` / `personal.last_name`; full name = join.
- Phone: digits only if the field rejects formatting.
- Salary: `professional.desired_salary_usd` (omit if 0).
- Yes/No questions about sponsorship → `work_authorization.require_sponsorship`.
- Yes/No about authorization → `work_authorization.authorized_to_work_us`.
- Unknown required field → PAUSE and ask; never guess on legally meaningful fields.

## Hard safety rules
- NEVER submit when `dry_run: true`.
- NEVER bypass a captcha or MFA automatically — hand control to the user.
- NEVER fabricate answers to compliance questions (authorization, clearance,
  veteran/disability status). Use profile values or ask.
- Respect `max_applications_per_run`. Stop when reached.
- Log every attempt, including skips and failures, with a reason.
