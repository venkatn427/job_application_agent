import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import api_server


class DashboardFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        resume = self.root / 'data/master_resume/resume.pdf'
        resume.parent.mkdir(parents=True)
        resume.write_bytes(b'sample resume')

    def test_unscored_zoominfo_prepares_without_ai(self):
        with patch.object(api_server, 'ROOT', self.root), \
             patch.object(api_server, 'is_configured', side_effect=AssertionError('AI')), \
             patch.object(api_server, '_tracker') as tracker:
            tracker.return_value.add_application.return_value = 1
            response = self.client.post('/api/jobs/prepare', json={
                'title': 'Principal Data Engineer', 'company': 'ZoomInfo Technologies LLC',
                'match_score': None, 'apply_url': 'https://example.test/job'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])

    def test_bad_score_returns_validation_error(self):
        response = self.client.post('/api/jobs/prepare', json={
            'title': 'Engineer', 'company': 'Example', 'match_score': 'bad'})
        self.assertEqual(response.status_code, 400)

    def test_saved_board_survives_new_client_without_scraping(self):
        from pipeline.job_store import JobStore
        store = JobStore(self.root / 'data/discovered_jobs.db')
        store.save([{'title': 'Engineer', 'company': 'Example', 'url': 'https://example.test/1'}])
        with patch.object(api_server, 'ROOT', self.root), \
             patch.object(api_server, '_scrape_greenhouse', side_effect=AssertionError('network')):
            first = self.client.get('/api/jobs/board/saved')
            second = api_server.app.test_client().get('/api/jobs/board/saved')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json['total'], 1)
        self.assertTrue(second.json['from_database'])

    def test_board_fetch_persists_all_results_and_empty_fetch_retains_them(self):
        jobs = [{'title': 'Data Engineer', 'company': 'Example', 'url': f'https://example.test/{i}'} for i in range(3)]
        with patch.object(api_server, 'ROOT', self.root), \
             patch.object(api_server, '_read_settings', return_value={'job_search': {'greenhouse_companies': ['example']}}), \
             patch.object(api_server, '_scrape_greenhouse', side_effect=[jobs, []]), \
             patch.object(api_server, '_portal_store') as portals:
            portals.return_value.find_by_platform.return_value = None
            first = self.client.get('/api/jobs/board?q=Data%20Engineer&limit=1')
            self.client.get('/api/jobs/board?q=Data%20Engineer&limit=1')
            saved = self.client.get('/api/jobs/board/saved')
        self.assertEqual(first.json['total'], 3)
        self.assertEqual(len(first.json['jobs']), 3)
        self.assertEqual(saved.json['total'], 3)

    def test_fetch_returns_only_current_batch(self):
        fresh = [{'title': 'New job', 'company': 'Example', 'apply_url': 'https://example.test/new'}]
        with patch('pipeline.job_store.collect_jobs', return_value=fresh) as collect, \
             patch.object(api_server, '_score_matches', side_effect=lambda jobs: jobs), \
             patch.object(api_server, '_read_settings', return_value={}):
            response = self.client.post('/api/jobs/refresh-matches', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['total'], 1)
        self.assertEqual(response.json['jobs'][0]['title'], 'New job')
        self.assertTrue(collect.call_args.kwargs['all_public'])

    def test_local_apply_is_visible_and_never_gets_advisor(self):
        with patch.object(api_server.threading, 'Thread') as thread, \
             patch('submission.apply_engine.get_advisor', side_effect=AssertionError('AI')), \
             patch('submission.apply_engine.ResumeAdapter') as adapter, \
             patch('submission.auto_apply.AutoApplier') as applier:
            adapter.return_value.prepare.return_value = ('resume.pdf', None)
            applier.return_value.apply.return_value = {'success': False, 'error': 'sample'}
            response = self.client.post('/api/jobs/apply', json={
                'url': 'https://example.test/job', 'mode': 'local', 'dry_run': False,
                'submission_approved': True, 'headless': True})
            thread.call_args.kwargs['target']()
            self.assertEqual(response.status_code, 200)
            self.assertFalse(applier.call_args.kwargs['headless'])
            self.assertIsNone(adapter.call_args.kwargs['advisor'])

    def test_continue_only_releases_waiting_login(self):
        task_id = api_server._new_task({})
        event = threading.Event()
        with api_server._apply_tasks_lock:
            api_server._apply_tasks[task_id].update(status='waiting_login', login_event=event)
        response = self.client.post(f'/api/jobs/apply/{task_id}/continue', json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(event.is_set())
        self.assertEqual(self.client.post('/api/jobs/apply/missing/continue', json={}).status_code, 409)

    def test_apply_login_choices_match_job_and_current_email(self):
        portals = [
            {'id': 1, 'name': 'Dice old', 'url': 'https://www.dice.com/login', 'username': 'old@example.test'},
            {'id': 2, 'name': 'Dice current', 'url': 'https://www.dice.com/login', 'username': 'current@example.test'},
            {'id': 3, 'name': 'Employer current', 'url': 'https://employer.example.test/login', 'username': 'current@example.test'}]
        with patch.object(api_server, '_portal_store') as store, \
             patch('submission.identity.application_email', return_value='current@example.test'):
            store.return_value.list_portals.return_value = portals
            response = self.client.get('/api/portals?job_url=https://www.dice.com/jobs/1')
            self.assertEqual([p['id'] for p in response.json['portals']], [2])
            response = self.client.get('/api/portals?job_url=https://stripe.com/jobs/1')
            self.assertEqual(response.json['portals'], [])
            self.assertEqual(len(self.client.get('/api/portals').json['portals']), 3)

    def test_live_apply_rejects_unrelated_portal_before_browser(self):
        with patch.object(api_server, '_portal_store') as store, \
             patch('submission.identity.application_email', return_value='current@example.test'), \
             patch.object(api_server.threading, 'Thread') as thread:
            store.return_value.get_portal.return_value = {'url': 'https://www.dice.com/login', 'username': 'current@example.test'}
            response = self.client.post('/api/jobs/apply', json={'url': 'https://stripe.com/jobs/1',
                'portal_id': 2, 'mode': 'local', 'dry_run': False, 'submission_approved': True})
            self.assertEqual(response.status_code, 400)
            self.assertIn('another portal', response.json['error'])
            thread.assert_not_called()

    def test_public_boards_return_matches_from_every_company(self):
        from pipeline.apify_scraper import GreenhouseScraper, LeverScraper
        from unittest.mock import Mock
        for scraper, payload in ((GreenhouseScraper(), {'jobs': [
            {'title': 'Engineer', 'absolute_url': 'https://example.test/1'}]}),
            (LeverScraper(), [{'text': 'Engineer', 'hostedUrl': 'https://example.test/1'}])):
            response = Mock(status_code=200)
            response.json.return_value = payload
            with patch('pipeline.apify_scraper.requests.get', return_value=response) as request:
                jobs = scraper.search(['first', 'second'], 'Engineer', max_results=None)
            self.assertEqual(len(jobs), 2)
            self.assertEqual(request.call_count, 2)
