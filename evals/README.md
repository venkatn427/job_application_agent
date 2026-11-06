# Multi-model engineering harness

The production router is `pipeline/llm.py`. Models are named entries in
`config/settings.yaml` under `ai.models`. Each `ai.routes` task selects one entry.
Tasks: `profile`, `matching`, `resume`, `cover_letter`, `fields`, `assistant`.
Unlisted tasks inherit the `default` route. Add another task name and call
`ModelClient("your_task")` without changing provider adapters.

Adapters support Anthropic Messages, OpenAI Responses, and OpenAI-compatible Chat
Completions. Model IDs are passed through unchanged; choose a text model available
on the configured service. A provider with a different protocol needs an adapter.
No runtime orchestration framework is required for the existing sequential flows.

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
    matching: reviewer
    fields: local
  timeout: 60
```

Keep keys in `.env`, never YAML. For a local service without authentication set its
key variable to a nonempty placeholder such as `local`. Each route resolves its own
key; an explicitly selected provider never borrows another provider's key. `auto`
preserves Anthropic preference when both keys exist, and uses OpenAI if only its
key exists. With neither key, existing local resume and keyword fallbacks remain.
`AI_PROVIDER`, `AI_MODEL`, and `AI_BASE_URL` override all task routes for debugging;
unset them when comparing different models. `PIPELINE_SETTINGS_PATH` selects a
settings file. Config is resolved when clients are constructed; restart existing
workers after changing their configuration.

## Local harness

```sh
.venv/bin/python scripts/ai_harness.py                 # inspect routes, no API calls
.venv/bin/python scripts/ai_harness.py --test          # offline regressions
.venv/bin/python scripts/ai_harness.py --live --output output/ai-eval.json
.venv/bin/python scripts/test_local_apply_sample.py   # local Chromium samples
.venv/bin/python scripts/test_local_apply_sample.py --ai
```

Live evaluations may incur provider costs. They use synthetic facts and never
submit applications. Missing keys or failed evaluations produce a nonzero exit.
Reports contain model metadata and pass/fail status, not credentials or profiles.
Offline CI runs regression and provider-contract tests without provider keys.

## Promptfoo integration

The Python bridge invokes the production router. It does not maintain a second
provider configuration. Duplicate a provider entry and set `config.model_alias`
to compare named models from the same registry using identical cases.

With Node.js and the project's Python environment available:

```sh
PATH="$PWD/.venv/bin:$PATH" npx promptfoo eval -c evals/promptfooconfig.yaml
npx promptfoo view
```

This optional command installs/runs Promptfoo and makes real provider calls. The
checked-in cases cover factual field answers, JSON output, and numeric experience.
They are a small smoke dataset, not proof of resume quality. Expand them with
anonymized expected outcomes before using scores to choose production models.
The bridge and adapter behavior are covered by the offline suite; the Promptfoo
CLI and live model quality require a configured provider and separate execution.

References: [Promptfoo Python provider](https://www.promptfoo.dev/docs/providers/python/),
[OpenAI text generation](https://developers.openai.com/api/docs/guides/text).
