"""Provider adapters and task routing for every AI call.

Secrets stay in environment variables. Routes select a named model from settings.
The messages facade preserves existing prompts, caching, and parsing contracts.
"""
import os
from pathlib import Path
from types import SimpleNamespace
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    'anthropic': ('ANTHROPIC_API_KEY', 'claude-sonnet-4-6', 'https://api.anthropic.com/v1'),
    'openai': ('OPENAI_API_KEY', 'gpt-5-mini', 'https://api.openai.com/v1'),
    'openai_compatible': ('LLM_API_KEY', '', ''),
}


def load_config():
    path = Path(os.environ.get('PIPELINE_SETTINGS_PATH', ROOT / 'config/settings.yaml'))
    if not path.is_absolute():
        path = ROOT / path
    settings = yaml.safe_load(path.read_text()) if path.exists() else {}
    return (settings or {}).get('ai', {})


def validate_config(config):
    """Reject broken registry shapes before a worker starts or settings are saved."""
    if not isinstance(config, dict):
        raise ValueError("AI configuration must be an object")
    for section in ("models", "routes"):
        if not isinstance(config.get(section, {}), dict):
            raise ValueError(f"ai.{section} must be an object")
    for alias, entry in config.get("models", {}).items():
        if not isinstance(entry, dict):
            raise ValueError(f"AI model {alias} must be an object")
        if "api_key" in entry:
            raise ValueError("Use api_key_env instead of storing API keys in config")
    for task, alias in config.get("routes", {}).items():
        if not isinstance(alias, str):
            raise ValueError(f"AI route {task} must name a model")
    if "api_key" in config:
        raise ValueError("Use api_key_env instead of storing API keys in config")


def resolve(task='default', *, config=None, api_key=None, model=None):
    config = load_config() if config is None else config
    validate_config(config)
    routes = config.get('routes', {})
    route = routes.get(task, routes.get('default', 'default'))
    models = config.get('models', {})
    if route not in models and route != 'default':
        raise ValueError(f'Unknown AI model route: {route}')
    selected = models.get(route, {})
    provider = os.environ.get('AI_PROVIDER') or selected.get('provider', config.get('provider', 'auto'))
    if provider == 'auto':
        provider = ('anthropic' if os.environ.get('ANTHROPIC_API_KEY') else
                    'openai' if os.environ.get('OPENAI_API_KEY') else
                    'anthropic' if api_key else 'openai')
    if provider not in DEFAULTS:
        raise ValueError(f'Unsupported AI provider: {provider}')
    key_env, default_model, default_url = DEFAULTS[provider]
    key_env = selected.get('api_key_env', config.get('api_key_env', key_env))
    key = api_key or os.environ.get(key_env, '')
    chosen_model = os.environ.get('AI_MODEL') or selected.get('model') or config.get('model') or model or default_model
    base_url = os.environ.get('AI_BASE_URL') or selected.get('base_url') or config.get('base_url') or default_url
    if provider == 'openai_compatible' and (not chosen_model or not base_url):
        raise ValueError('OpenAI-compatible models require model and base_url')
    timeout = float(selected.get('timeout', config.get('timeout', 60)))
    if timeout <= 0:
        raise ValueError('AI timeout must be positive')
    return SimpleNamespace(provider=provider, model=chosen_model, api_key=key,
                           base_url=base_url.rstrip('/'), timeout=timeout)


def is_configured(task='default', **kwargs):
    return bool(resolve(task, **kwargs).api_key)


def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(block['text'] for block in content if block.get('type') == 'text')


def _response(text, truncated=False):
    if not text.strip():
        raise RuntimeError('AI provider returned no text')
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=text)],
                           stop_reason='max_tokens' if truncated else 'end_turn')


def _post(settings, path, body):
    # Do not include response bodies, headers or secrets in raised errors.
    try:
        response = requests.post(settings.base_url + path,
                                 headers={'Authorization': f'Bearer {settings.api_key}'},
                                 json=body, timeout=settings.timeout)
    except requests.RequestException:
        raise RuntimeError(f'{settings.provider} request failed') from None
    if not response.ok:
        raise RuntimeError(f'{settings.provider} request failed (HTTP {response.status_code})')
    return response.json()


def _anthropic(settings, *, messages, system, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=settings.api_key, base_url=settings.base_url,
                                 timeout=settings.timeout)
    kwargs = dict(model=settings.model, messages=messages, max_tokens=max_tokens)
    if system:
        kwargs['system'] = system
    response = client.messages.create(**kwargs)
    return _response('\n'.join(block.text for block in response.content if block.type == 'text'),
                     response.stop_reason == 'max_tokens')


def _openai(settings, *, messages, system, max_tokens):
    body = {'model': settings.model, 'input': [dict(role=m['role'], content=_text(m['content'])) for m in messages],
            'max_output_tokens': max_tokens, 'store': False}
    if system:
        body['instructions'] = _text(system)
    data = _post(settings, '/responses', body)
    text = '\n'.join(block['text'] for item in data.get('output', [])
                     if item.get('type') == 'message' for block in item.get('content', [])
                     if block.get('type') == 'output_text')
    return _response(text, data.get('status') == 'incomplete')


def _compatible(settings, *, messages, system, max_tokens):
    converted = [dict(role=m['role'], content=_text(m['content'])) for m in messages]
    if system:
        converted.insert(0, dict(role='system', content=_text(system)))
    data = _post(settings, '/chat/completions', {'model': settings.model, 'messages': converted,
                                               'max_tokens': max_tokens})
    choice = data['choices'][0]
    return _response(choice['message'].get('content') or '', choice.get('finish_reason') == 'length')


# A new backend only needs this adapter contract.
ADAPTERS = {'anthropic': _anthropic, 'openai': _openai, 'openai_compatible': _compatible}


class ModelClient:
    def __init__(self, task='default', *, config=None, api_key=None, model=None):
        self.settings = resolve(task, config=config, api_key=api_key, model=model)
        self.messages = self

    def create(self, *, messages, max_tokens, system=None, model=None):
        # Legacy call-site model arguments are deliberately ignored: routing owns models.
        if not self.settings.api_key:
            raise ValueError(f'No API key configured for {self.settings.provider}')
        return ADAPTERS[self.settings.provider](self.settings, messages=messages,
                                               system=system, max_tokens=max_tokens)
