# Job Application Agent

Check memory first: call `memory_search` before re-reading source files, and verify a result before relying on it. Save new non-obvious facts with `memory_save`, and delete entries that turn out wrong. Never store secrets.

## Where things are
- Active code: `main.py`, `scheduler.py`, `pipeline/`, `config/settings.yaml`
- Dice applications: `submission/` (driven by `submission/dice_playbook.md`)
- Multi-board Playwright apply: `submission/auto_apply.py` + `submission/auto_apply_playbook.md`
- Application tracking DB: `pipeline/app_tracker.py` → `data/applications.db` (7 tables)
- Local memory MCP server: `tools/local_memory.py` → `data/memory.db`
- `agents/` is not imported by the pipeline
- Legacy, skip unless asked: `scraper.py`, `server.py`, `ui.py`, `job_search_assistant/`
- Never read `.venv/`. Don't print `submission/.secrets.env`, `job_search_assistant/.env`, or personal values from `submission/profile.json`.

## Commands
- Tests: `.venv/bin/python test_pipeline.py` (calls public job board APIs; appends a row to `logs/test_applications.csv`)
- Memory CLI: `.venv/bin/python tools/local_memory.py list`

## Safety
`submission/` sends real job applications. Don't run that flow or change its answers without asking.
`submission/auto_apply.py` submits real applications via Playwright. Set `AUTO_APPLY_DRY_RUN=true` to test safely. Never run without asking.

## Agent skills
- `pipeline/app_tracker.py` — `AppTracker` class tracks applications in SQLite (7 tables: applications, companies, contacts, interview_stages, documents, notes, follow_ups)
- `submission/auto_apply.py` — `AutoApplier` class handles Playwright-driven human-like apply for Greenhouse, Lever, LinkedIn, Indeed, and generic boards
- `tools/local_memory.py` — MCP server with 4 tools: `memory_search`, `memory_save`, `memory_list`, `memory_delete`; uses FTS5 SQLite
- `pipeline/job_matcher.py` — keyword matching with alias map (k8s↔kubernetes, js↔javascript), whole-word regex, context-sensitive YOE extraction
- `pipeline/document_builder.py` — PDF (reportlab + Unicode font) and DOCX (python-docx) resume builders; all user content XML-escaped
