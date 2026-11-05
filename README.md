# Job Application Agent

Local job discovery, candidate matching, resume preparation and optional supervised
browser submission. See [Solution Design and Developer Guide](docs/solution-design-developer-guide.html)
for architecture, flows, API reference and known limitations.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Configure `.env` privately, add a master resume at `data/master_resume/resume.pdf`,
and edit `config/settings.yaml`. Do not commit credentials or candidate data.

## Run

```sh
python main.py profile --refresh
python main.py run                  # Prepares documents; does not submit
python api_server.py               # Loopback dashboard on port 5050
python -m unittest discover -s tests -v  # Offline regression suite
```

Submission defaults to dry-run. `apply_job.py --url URL --submit` explicitly
approves live submission to that URL. The API requires both `dry_run: false`
and `submission_approved: true`; the dashboard asks for confirmation. Direct
`AutoApplier` callers must also supply `submission_approved=True` for live use.
Never enable live submission in unattended tests.

New tracker records are `prepared`, not `applied`. Application timestamps and
follow-ups start when an application is explicitly marked applied. Historical
records are not automatically relabeled; audit earlier applied records manually.

The API is a loopback-only local development tool, not an authenticated public
service. Durable workers, cloud-sync retries and unified tracking remain future work.
