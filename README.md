# Job Application Agent

A local Python application for discovering jobs, matching them to a candidate,
preparing tailored resumes and cover letters, and tracking applications. A Flask
dashboard provides job history, profile settings, document preparation, and
explicitly approved browser submissions.

AI is optional. Named models and task routes let you use OpenAI, Anthropic,
OpenAI-compatible services, or a mixture of providers across the same workflow.

## What it does

- Discover jobs from configured sources and retain discovery history in SQLite.
- Score jobs with keyword matching and optional AI analysis.
- Parse candidate profiles and generate resumes and cover letters with AI.
- Use existing master documents when AI preparation is unavailable.
- Track prepared applications, application status, and follow-ups.
- Automate supported application forms with local Playwright Chromium.
- Test provider routing offline and evaluate models using synthetic samples.

## Quick start

Run these commands from the repository root. Python 3.13 is the CI target.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.template .env
mkdir -p data/master_resume logs
```

Add your master resume at `data/master_resume/resume.pdf`. Edit `.env` privately
with any provider or integration keys you want to use, then customize
[`config/settings.yaml`](config/settings.yaml) for roles, locations, job boards,
matching thresholds, file paths, notifications, and schedules.

Start the dashboard:

```sh
python api_server.py
```

Open **http://127.0.0.1:5050**. Configure your candidate profile in the dashboard
before preparing applications. You can also use a private
`submission/profile.json` containing the nested `personal` and `professional`
profile format. Generated candidate profiles are cached in
`data/candidate_profile.json`.

The API is a loopback-only local development service. It has no public-service
authentication and should not be exposed to the internet.

## Configure models and providers

Secrets belong in `.env`; settings contain only the names of key variables.
For example:

```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LOCAL_LLM_API_KEY=local
```

Replace empty values with your private credentials for the services you use.
For a local endpoint that does not require authentication, a nonempty placeholder
such as `local` enables its route.

The default `auto` model uses Anthropic when `ANTHROPIC_API_KEY` is set, otherwise
OpenAI when `OPENAI_API_KEY` is set. Without either key, AI is disabled and the
available local fallback paths remain usable. Initial AI profile parsing still
requires a configured provider; use an existing saved profile for local matching.

To choose models independently, replace the `ai` section of settings with a
registry and task routes:

```yaml
ai:
  models:
    writer:
      provider: openai
      model: gpt-5-mini
      api_key_env: OPENAI_API_KEY
    reviewer:
      provider: anthropic
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY
    local:
      provider: openai_compatible
      model: your-model-id
      base_url: http://localhost:11434/v1
      api_key_env: LOCAL_LLM_API_KEY
  routes:
    default: writer
    profile: writer
    matching: reviewer
    resume: writer
    cover_letter: writer
    fields: local
    assistant: writer
  timeout: 60
```

Choose text models available to your provider account. An explicitly selected
provider uses its own key and does not borrow credentials from another provider.

| Task | Used for |
| --- | --- |
| `profile` | Resume/profile parsing |
| `matching` | Semantic job-match analysis |
| `resume` | Resume tailoring and rewriting |
| `cover_letter` | Cover letter generation |
| `fields` | Unknown application-field answers |
| `assistant` | Other assistant tools, such as interview preparation |

Unlisted tasks inherit the `default` route. All AI calls use
[`pipeline/llm.py`](pipeline/llm.py). Adapters support Anthropic Messages, OpenAI
Responses, and OpenAI-compatible Chat Completions. Other API protocols need an
adapter; model names themselves are passed through unchanged.

Optional global environment overrides are `AI_PROVIDER`, `AI_MODEL`, and
`AI_BASE_URL`. They apply to every task, so unset them when testing mixed-provider
routing. Set `PIPELINE_SETTINGS_PATH` to load another settings file. Restart
existing workers after changing their model configuration.

The legacy setting `matching.use_claude_scoring` now controls routed AI scoring;
set it to `false` for keyword-only matching regardless of provider.

## Commands

With the virtual environment activated:

| Command | Purpose |
| --- | --- |
| `python api_server.py` | Start the dashboard on port 5050 |
| `python main.py profile --refresh` | Parse the master resume with the profile model |
| `python main.py run` | Discover, match, and prepare documents; does not submit |
| `python main.py stats` | Show pipeline statistics |
| `python scheduler.py --status` | Show scheduled run times and exit |
| `python scheduler.py` | Run the scheduled preparation pipeline |
| `python scheduler.py --once` | Run the pipeline once and exit |
| `python -m pipeline.job_store` | Collect discovery history once |
| `python -m pipeline.job_store --daily` | Collect discovery history daily at 08:00 Central |
| `streamlit run job_search_assistant/app.py` | Start the optional assistant UI |

Scheduled processes and the machine must remain running. The full pipeline
scheduler also collects discovery history; use one scheduling mode to avoid
duplicate collection. Its schedule and timezone come from `config/settings.yaml`.

Dashboard date filters use the first discovery date in **America/Chicago**, with
Monday-start calendar weeks. They do not infer a posting date. Refresh updates
existing records without erasing discovery history.

## Application submission

The dashboard and Job Board load saved jobs immediately from SQLite on page load.
**Fetch jobs** requests live portal results and merges them into saved discovery
history. Board fetches are saved too, before display filters; duplicate URLs update
existing records. Empty or failed fetches retain previously saved jobs. Public Greenhouse and Lever boards return all matching roles
from configured companies. Other sources retain their configured/API limits.

**Prepare** creates documents only. Choose **Apply in browser**, select **Local**,
and disable dry-run to start a visible Playwright browser. Matching saved portal
credentials for the current email are selected automatically.
Local mode uses the master resume and never calls AI. Complete login, CAPTCHA, or
verification in the browser, then click **Continue after login** in the dashboard;
the application continues in the same browser session. Live application submission
requires the existing confirmation. Saved portal logins must use the current email.
Employer Apply links and embedded Greenhouse forms are followed automatically.
Known profile fields and screening answers are filled without AI. Missing required
answers pause the visible browser and show **Continue after completing form**;
complete these fields before continuing. Recording consent and unknown facts are
left for the applicant.

### Portal accounts without AI

`scripts/setup_portal_account.py` generates a unique password locally and saves it
in the existing encrypted portal store before opening a registration form. Repeated
setup for the same HTTPS origin and email reuses the password. Existing saved
passwords are retained. Passwords are never printed in command output.

```sh
.venv/bin/python scripts/setup_portal_account.py --name "Employer portal" --url "https://portal.example.com/register"
```

Replace the URL with the employer's actual registration page. Add `--open` to fill
a visible form, and `--submit-account` to submit the registration form. Use
`--action login --open --submit-account` with a login URL to reuse saved credentials.
The helper supports single-page email/password forms. Multi-step forms, CAPTCHA,
terms checkboxes, and verification may need completion in the visible browser.
Only a supplied `--confirmation-selector` produces a confirmed success status;
otherwise the result requires verification or review. This command never applies
to a job. Creating accounts needs internet even when AI is disabled.

The local `data/application_identity.json` sets the email for profiles, account
setup, application credentials, and notifications. The application team selects
stored credentials only for the exact portal origin and current email, so old
logins and unrelated employer credentials are not reused. Gmail connected to Codex
is available during assisted setup; it does not install Gmail OAuth credentials
into this standalone Python app. Email links/codes can be completed manually in
the visible browser. SMTP notifications still require valid sender credentials.

### Separate application agents: offline and AI

The new `agents/application_team.py` coordinator runs separate profile, matching,
document, screening, and submission workers. These are Python workers with clear
handoffs, not persistent background processes. This CLI is independent of the
existing dashboard and legacy Claude SDK agents.

Prepare a local job list without network access or AI calls:

```sh
.venv/bin/python scripts/run_application_team.py --mode offline --jobs config/application_jobs.example.json
```

Offline preparation uses keyword matching, the master resume, and exact saved
screening answers. Unknown questions remain unanswered for review. The saved
`submission/profile.json` overrides cached contact details, including email.
An optional cached profile supplies work history and skills; populate those facts
for useful matching and AI tailoring.

Use configured AI routes for matching, a tailored resume PDF, and unknown screening
questions:

```sh
.venv/bin/python scripts/run_application_team.py --mode ai --jobs path/to/jobs.json
```

AI mode requires credentials for `matching`, `resume`, and `fields`; missing keys
stop the run. Output defaults to `output/application-team.json`, with generated
PDFs under `output/application-team/`. Reports contain private candidate data.
Per-job failures are isolated and return a nonzero CLI status.

Both modes prepare by default. To explicitly approve online submissions, add
`--submit` to either command using a reviewed job list with real employer URLs.
Offline mode then uses browser automation without AI; submission itself requires
internet access. Only recommended jobs with all supplied screening questions
answered proceed. Custom questions discovered on employer forms may still need
manual intervention. The example list is synthetic and is for preparation only.

Submission defaults to an offline dry run. It detects the platform from the URL
without preparing resumes, calling an AI provider, or launching a browser:

```sh
python apply_job.py --url "https://jobs.lever.co/example/job-id" --dry-run
```

To explicitly approve a real submission to one URL:

```sh
python apply_job.py --url "https://jobs.lever.co/example/job-id" --submit
```

The API requires both `dry_run: false` and `submission_approved: true` for live
submission. Direct `AutoApplier` callers need `submission_approved=True` in
addition to `dry_run=False`. The dashboard asks for confirmation.

Greenhouse and Lever require visible confirmation before reporting success.
Unsupported confirmation layouts may return failure even after submission;
check the employer's confirmation before retrying. Other platform automation is
best effort and may require manual intervention for login, CAPTCHA, or custom
questions. Never enable live submission in unattended regression tests.

New tracker records are **prepared**, not **applied**. Application timestamps and
follow-ups begin when an application is explicitly marked applied. Earlier
historical records are not automatically relabeled.

## Testing and evaluation

Run the offline regression and provider-contract suite:

```sh
python scripts/ai_harness.py --test
# Equivalent:
python -m unittest discover -s tests -v
```

Inspect task routing without calling providers:

```sh
python scripts/ai_harness.py
```

Run real Chromium samples against synthetic local forms:

```sh
python scripts/test_local_apply_sample.py
```

The browser samples cover confirmed submissions and rejected forms for Greenhouse
and Lever. They never submit to employer sites.

After configuring provider keys, opt into live AI checks with synthetic facts:

```sh
python scripts/ai_harness.py --live --output output/ai-eval.json
python scripts/test_local_apply_sample.py --ai
```

Live checks may incur API costs. Missing keys or failed evaluations return a
nonzero exit. The report includes route metadata and status, without credentials
or candidate profiles.

An optional [Promptfoo integration](evals/README.md) compares named models through
the production router. It includes factual-answer and JSON-output cases. See that
guide for setup and commands. The checked-in GitHub Actions workflow runs the
offline suite without provider credentials; it does not submit applications.

## Project layout

| Path | Contents |
| --- | --- |
| `api_server.py`, `static/` | Flask API and dashboard |
| `main.py`, `scheduler.py` | Pipeline CLI and scheduled execution |
| `pipeline/` | Discovery, matching, profile parsing, document generation, AI routing |
| `submission/` | Browser automation, form helpers, and portal credentials storage |
| `config/settings.yaml` | Search settings and AI model registry |
| `scripts/`, `tests/`, `evals/` | Local harness, regressions, and model comparisons |
| `job_search_assistant/` | Optional Streamlit assistant |
| `data/`, `output/`, `logs/` | Local profiles, databases, documents, and logs |

Secrets, candidate data, databases, and generated output are ignored by Git.
Keep `.env`, portal credentials, resumes, and service-account files private.
Optional Google Drive/Sheets, scraping, and notification integrations are described
in `.env.template` and the
[Solution Design and Developer Guide](docs/solution-design-developer-guide.html).

## Troubleshooting

- **Missing AI key:** run `python scripts/ai_harness.py` and check the selected
  route's `api_key_env`. Configure that variable in `.env`, then restart the app.
- **Invalid model or endpoint:** verify the model ID is available to your account
  and `base_url` includes the provider's API prefix, such as `/v1`.
- **Browser executable missing:** run `python -m playwright install chromium`
  using the same virtual environment that runs the application.
- **No resume prepared:** add the master PDF at the configured path or inspect
  the AI provider error. Preparation fails rather than recording a missing file
  as an application.
- **No scheduled work runs:** keep the scheduler alive and check its timezone
  using `python scheduler.py --status`.

Durable background workers, cloud-sync retries, and fully unified tracking remain
future work. Live provider quality and employer-specific forms need separate
verification beyond the offline suite.
