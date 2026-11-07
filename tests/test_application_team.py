import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.application_team import ApplicationTeam


class ApplicationTeamTests(unittest.TestCase):
    def setUp(self):
        identity = patch('submission.identity.application_email', return_value=None)
        identity.start()
        self.addCleanup(identity.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resume = Path(self.temp.name) / 'resume.pdf'
        self.resume.write_bytes(b'test resume')
        self.saved = {'personal': {'first_name': 'Sample', 'last_name': 'Candidate', 'email': 'new@example.test'},
                      'professional': {'skills': ['Python'], 'current_title': 'Engineer'},
                      'screening_defaults': {'Work authorization': 'Yes'}}
        self.jobs = [{'title': 'Engineer', 'company': 'Example', 'description': 'Python engineer',
                      'apply_url': 'https://jobs.lever.co/example/1',
                      'questions': ['Work authorization', 'Unknown question']}]

    def test_offline_no_network_ai_or_browser_and_current_email(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network')), \
             patch('pipeline.job_matcher.resolve', side_effect=AssertionError('AI configuration')), \
             patch('agents.application_team.ModelClient', side_effect=AssertionError('AI')), \
             patch('submission.auto_apply.AutoApplier.apply', side_effect=AssertionError('submit')):
            team = ApplicationTeam()
            profile = team.profile_agent.run(self.saved, {'email': 'old@example.test'})
            self.assertEqual(profile.email, 'new@example.test')
            report = team.run(self.jobs, self.saved, self.resume)
        app = report['applications'][0]
        self.assertFalse(app['submission']['submitted'])
        self.assertFalse(app['submission']['ready_for_review'])
        self.assertEqual(app['screening']['unanswered'], ['Unknown question'])

    def test_ai_requires_credentials(self):
        with patch('agents.application_team.resolve', return_value=SimpleNamespace(api_key='')):
            with self.assertRaisesRegex(ValueError, 'credentials missing'):
                ApplicationTeam('ai').run(self.jobs, self.saved, self.resume)

    def test_ai_routes_matching_resume_and_fields(self):
        response = lambda text: SimpleNamespace(content=[SimpleNamespace(text=text)])
        with patch('agents.application_team.resolve', return_value=SimpleNamespace(api_key='test')), \
             patch('agents.application_team.ModelClient') as client, \
             patch('pipeline.document_builder.build_pdf', side_effect=lambda resume, path: path.write_bytes(b'test PDF')), \
             patch('pipeline.resume_generator.ResumeGenerator') as generator:
            client.return_value.messages.create.side_effect = [
                response('{"score": 85, "match_reasons": [], "gaps": []}'),
                response('{"answer": null}')]
            generator.return_value.generate_resume.return_value.full_text.return_value = 'Tailored sample'
            app = ApplicationTeam('ai').run(self.jobs, self.saved, self.resume, output_dir=self.temp.name)['applications'][0]
            self.assertEqual([c.args[0] for c in client.call_args_list], ['matching', 'fields'])
            self.assertEqual(app['documents']['source'], 'ai')
            self.assertEqual(app['matching']['score'], 85)
            self.assertFalse(app['submission']['submitted'])

    def test_non_ai_submission_uses_reviewed_profile_and_resume(self):
        self.jobs[0]['questions'] = ['Work authorization']
        with patch('submission.auto_apply.AutoApplier') as applier:
            applier.return_value.apply.return_value = {'success': True}
            app = ApplicationTeam(threshold=0).run(self.jobs, self.saved, self.resume, submit=True)['applications'][0]
            self.assertTrue(applier.call_args.kwargs['submission_approved'])
            self.assertFalse(applier.call_args.kwargs['dry_run'])
            self.assertEqual(applier.return_value._profile['personal']['email'], 'new@example.test')
            self.assertTrue(app['submission']['submitted'])

    def test_unknown_questions_block_submission(self):
        with patch('submission.auto_apply.AutoApplier') as applier:
            app = ApplicationTeam(threshold=0).run(self.jobs, self.saved, self.resume, submit=True)['applications'][0]
            applier.assert_not_called()
            self.assertEqual(app['submission']['status'], 'blocked')

    def test_job_failure_isolated(self):
        report = ApplicationTeam().run([{}, self.jobs[0]], self.saved, self.resume)
        self.assertEqual(report['applications'][0]['status'], 'failed')
        self.assertIn('submission', report['applications'][1])


if __name__ == '__main__':
    unittest.main()
