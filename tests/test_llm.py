"""Provider contracts and routing tests. Never call an external provider."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from pipeline.llm import ModelClient, resolve, is_configured

CONFIG = {
    'models': {
        'writer': {'provider': 'openai', 'model': 'writer-model'},
        'reviewer': {'provider': 'anthropic', 'model': 'review-model'},
        'local': {'provider': 'openai_compatible', 'model': 'local-model',
                  'base_url': 'http://localhost:11434/v1', 'api_key_env': 'LOCAL_KEY'},
    },
    'routes': {'default': 'writer', 'matching': 'reviewer', 'fields': 'local'},
}
ENV = {'OPENAI_API_KEY': 'fake-openai', 'ANTHROPIC_API_KEY': 'fake-claude', 'LOCAL_KEY': 'fake-local'}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_routes_keep_models_and_credentials_separate(self):
        for task, provider, model, key in [('resume', 'openai', 'writer-model', 'fake-openai'),
                                         ('matching', 'anthropic', 'review-model', 'fake-claude'),
                                         ('fields', 'openai_compatible', 'local-model', 'fake-local')]:
            settings = resolve(task, config=CONFIG)
            self.assertEqual((settings.provider, settings.model, settings.api_key), (provider, model, key))

    def test_openai_only_auto_and_no_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake'}, clear=True):
            self.assertEqual(resolve(config={}).provider, 'openai')
            self.assertTrue(is_configured(config={}))
            # Passing the resolved key must not switch the auto provider to Anthropic.
            self.assertEqual(resolve(config={}, api_key='fake').provider, 'openai')
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_configured(config={}))
            with self.assertRaises(ValueError):
                ModelClient(config={}).messages.create(messages=[], max_tokens=10)

    def test_bad_route_and_provider(self):
        for config in ({'routes': {'default': 'missing'}}, {'provider': 'unknown'}, {'provider': 'openai_compatible'}):
            with self.assertRaises(ValueError):
                resolve(config=config)

    def test_environment_overrides(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'openai', 'AI_MODEL': 'override'}):
            result = resolve('matching', config=CONFIG)
        self.assertEqual((result.provider, result.model), ('openai', 'override'))

    def test_openai_request_normalizes_caching_and_text(self):
        response = Mock(ok=True)
        response.json.return_value = {'status': 'completed', 'output': [
            {'type': 'reasoning'}, {'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]}]}
        with patch('pipeline.llm.requests.post', return_value=response) as post:
            result = ModelClient('resume', config=CONFIG).messages.create(
                model='legacy-claude-id', max_tokens=42,
                system=[{'type': 'text', 'text': 'system', 'cache_control': {'type': 'ephemeral'}}],
                messages=[{'role': 'user', 'content': [{'type': 'text', 'text': 'profile', 'cache_control': {'type': 'ephemeral'}}]}])
        self.assertEqual(result.content[0].text, 'answer')
        body = post.call_args.kwargs['json']
        self.assertEqual(body, {'model': 'writer-model', 'instructions': 'system', 'input': [{'role': 'user', 'content': 'profile'}], 'max_output_tokens': 42, 'store': False})
        self.assertEqual(post.call_args.args[0], 'https://api.openai.com/v1/responses')

    def test_compatible_endpoint_and_truncation(self):
        response = Mock(ok=True)
        response.json.return_value = {'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]}
        with patch('pipeline.llm.requests.post', return_value=response) as post:
            result = ModelClient('fields', config=CONFIG).messages.create(system='system', messages=[{'role': 'user', 'content': 'question'}], max_tokens=20)
        self.assertEqual(result.stop_reason, 'max_tokens')
        self.assertEqual(post.call_args.args[0], 'http://localhost:11434/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer fake-local')

    def test_anthropic_preserves_cache_blocks(self):
        client = Mock()
        client.messages.create.return_value = Mock(content=[Mock(type='text', text='answer')], stop_reason='end_turn')
        system = [{'type': 'text', 'text': 'cached', 'cache_control': {'type': 'ephemeral'}}]
        with patch('anthropic.Anthropic', return_value=client):
            result = ModelClient('matching', config=CONFIG).messages.create(system=system, messages=[], max_tokens=20)
        self.assertEqual(result.content[0].text, 'answer')
        self.assertEqual(client.messages.create.call_args.kwargs['system'], system)
        self.assertEqual(client.messages.create.call_args.kwargs['model'], 'review-model')

    def test_http_errors_do_not_expose_body_or_key(self):
        with patch('pipeline.llm.requests.post', return_value=Mock(ok=False, status_code=401, text='secret')):
            with self.assertRaisesRegex(RuntimeError, r'^openai request failed \(HTTP 401\)$'):
                ModelClient(config=CONFIG).messages.create(messages=[], max_tokens=10)

    def test_pipeline_components_use_task_routes(self):
        from pipeline.profile_analyzer import ProfileAnalyzer
        from pipeline.job_matcher import JobMatcher
        from pipeline.resume_generator import ResumeGenerator
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = ProfileAnalyzer(cache_dir=Path(tmp), ai_config=CONFIG)
            self.assertEqual(analyzer.client.settings.model, 'writer-model')
        self.assertEqual(JobMatcher(ai_config=CONFIG).client.settings.model, 'review-model')
        self.assertEqual(ResumeGenerator(ai_config=CONFIG).client.settings.model, 'writer-model')

    def test_promptfoo_bridge_uses_production_routes(self):
        from evals.provider import call_api
        with patch('evals.provider.load_config', return_value=CONFIG), patch('pipeline.llm.ADAPTERS', {'openai': Mock(return_value=Mock(content=[Mock(text='3')]))}):
            result = call_api('question', {'config': {'task': 'profile'}}, {})
        self.assertEqual(result, {'output': '3'})

    def test_config_api_round_trip_and_validation(self):
        import api_server
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.yaml"
            settings.write_text("{}")
            with patch.object(api_server, "SETTINGS_PATH", settings):
                client = api_server.app.test_client()
                self.assertEqual(client.put('/api/config', json={'ai': CONFIG}).status_code, 200)
                self.assertEqual(client.get('/api/config').json['ai'], CONFIG)
                self.assertEqual(yaml.safe_load(settings.read_text())['ai'], CONFIG)
                for bad in ({'routes': {'default': 'missing'}}, {'models': []}, {'api_key': 'do-not-store'}):
                    self.assertEqual(client.put('/api/config', json={'ai': bad}).status_code, 400)
                self.assertEqual(yaml.safe_load(settings.read_text())['ai'], CONFIG)
