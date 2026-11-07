"""Offline regressions for scheduler, profile and submission confirmation."""
import unittest
from unittest.mock import Mock, patch

from submission.auto_apply import _GreenhouseApplier, _LeverApplier, _contact_profile


class ApplyFixTests(unittest.TestCase):
    def test_pending_scheduler_job(self):
        from scheduler import build_scheduler, next_run_time
        scheduler = build_scheduler()
        for job in scheduler.get_jobs():
            self.assertIsNotNone(next_run_time(job, scheduler.timezone))
        self.assertFalse(scheduler.running)

    def test_dry_run_has_no_side_effects(self):
        from apply_job import run_apply
        with patch('apply_job._resolve_resume') as resume, patch('apply_job._resolve_portal_creds') as creds:
            self.assertEqual(run_apply('https://jobs.lever.co/example/1')['error'], 'dry_run')
        resume.assert_not_called()
        creds.assert_not_called()

    def test_contact_profiles(self):
        self.assertEqual(_contact_profile({})['name'], '')
        self.assertEqual(_contact_profile({'name': 'Sample Person'})['name'], 'Sample Person')
        self.assertEqual(_contact_profile({'personal': {'first_name': 'Sample', 'last_name': 'Person'}})['name'], 'Sample Person')

    def test_submit_requires_confirmation(self):
        for applier in (_GreenhouseApplier(), _LeverApplier()):
            for confirmed in (False, True):
                page = Mock()
                page.locator.return_value.count.return_value = 0
                page.get_by_role.return_value.count.return_value = 0
                page.evaluate.return_value = []
                def wait(selector, **kwargs):
                    if kwargs.get('state') == 'visible' and not confirmed:
                        raise TimeoutError('validation errors remain')
                page.wait_for_selector.side_effect = wait
                with patch('submission.auto_apply._human_delay'):
                    self.assertEqual(applier.apply(page, {}, None, None, {}), confirmed)

    def test_ai_advisor_response_and_provider_failure(self):
        from submission.apply_engine import ClaudeFieldAdvisor
        advisor = ClaudeFieldAdvisor(api_key="synthetic-test-key")
        client = Mock()
        client.messages.create.return_value.content = [Mock(text=" Software Engineer ")]
        with patch.object(advisor, '_get_client', return_value=client):
            answer = advisor.answer_field('Current title', 'text', [],
                                          {'professional': {'current_title': 'Software Engineer'}})
            self.assertEqual(answer, 'Software Engineer')
            client.messages.create.side_effect = RuntimeError('Simulated provider outage')
            self.assertIsNone(advisor.answer_field('Current title', 'text', [], {}))
