# Dice Submission Module

Auto-submits Dice **Easy Apply** applications. The browser is driven live by the
Claude agent through the **Playwright MCP** tools, following [`dice_playbook.md`](dice_playbook.md).

## Files
| File | Purpose |
|------|---------|
| `profile.json` | Your data used to fill application forms. **Fill this in.** |
| `settings.yaml` | Run controls: dry_run, pause_before_submit, caps, job source. |
| `dice_playbook.md` | Step-by-step flow the agent follows in the browser. |
| `submitter.py` | Helper: validates config, resolves screening answers, logs results. |
| `submissions.json` | Audit log of every application attempt (created on first run). |

## Quick start
1. Edit `profile.json` with your real info and put a resume at
   `data/master_resume/resume.pdf` (or update `documents.resume_path`).
2. Validate:
   ```bash
   python -m submission.submitter check
   ```
3. Ask the agent to run it (e.g. "apply to Dice jobs"). The agent will:
   open a browser, wait for you to log in, find Easy-Apply jobs, fill each form
   from `profile.json`, **show you the filled form, and wait for your OK before
   submitting** (while `pause_before_submit: true`).
4. Review history:
   ```bash
   python -m submission.submitter history
   ```

## Safety defaults
- `pause_before_submit: true` — nothing is sent without your confirmation.
- `dry_run: false` — set `true` to fill forms without submitting.
- `max_applications_per_run: 5` — hard cap per session.
- The agent never bypasses captcha/MFA and never fabricates answers to
  authorization / clearance / EEO questions.
