"""Offline regression tests; no external requests, credentials or submissions."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pipeline.app_tracker import AppTracker
from pipeline.apify_scraper import JobListing
from pipeline.profile_analyzer import CandidateProfile
from pipeline.workflow import Pipeline
from submission.auto_apply import AutoApplier
import api_server


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tracker = AppTracker(Path(self.temp.name) / "tracker.db")
        self.job = JobListing(title="Engineer", company="Example",
                              apply_url="https://example.com/jobs/1")

    def test_prepare_is_not_applied_and_preserves_url(self):
        app_id = self.tracker.add_application(self.job)
        row = self.tracker.get_application(app_id)
        self.assertEqual(row["status"], "prepared")
        self.assertIsNone(row["applied_at"])
        self.assertEqual(row["job_url"], self.job.apply_url)
        self.assertEqual(self.tracker.pending_follow_ups(), [])
        self.assertEqual(self.tracker.summary_stats()["total"], 0)
        self.assertEqual(self.tracker.summary_stats()["prepared"], 1)

    def test_confirmation_sets_timestamp_and_follow_up_once(self):
        app_id = self.tracker.add_application(self.job)
        self.tracker.update_status(app_id, "applied")
        first = self.tracker.get_application(app_id)["applied_at"]
        self.tracker.update_status(app_id, "applied")
        self.assertEqual(first, self.tracker.get_application(app_id)["applied_at"])
        self.assertIsNotNone(first)
        self.assertEqual(len(self.tracker.pending_follow_ups(days_ahead=8)), 1)

    def test_foreign_keys_enabled_on_each_connection(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.tracker.add_note(999, "orphan")


class SubmissionTests(unittest.TestCase):
    def test_default_is_dry_run(self):
        with patch.dict(os.environ, {}, clear=True):
            result = AutoApplier().apply("https://example.com/jobs/1")
        self.assertEqual(result["error"], "dry_run")

    def test_live_without_approval_never_imports_browser(self):
        result = AutoApplier(dry_run=False).apply("https://example.com/jobs/1")
        self.assertEqual(result["error"], "submission_approval_required")

    def test_environment_cannot_grant_approval(self):
        with patch.dict(os.environ, {"AUTO_APPLY_DRY_RUN": "false"}):
            result = AutoApplier().apply("https://example.com/jobs/1")
        self.assertEqual(result["error"], "submission_approval_required")

    def test_empty_profile_stops_before_fetch(self):
        pipeline = object.__new__(Pipeline)
        pipeline.tracker = Mock()
        pipeline.tracker.get_spreadsheet_url.return_value = ""
        pipeline._load_profile = Mock(return_value=CandidateProfile())
        pipeline._fetch_jobs = Mock()
        with self.assertRaises(ValueError):
            pipeline.run()
        pipeline._fetch_jobs.assert_not_called()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_rejects_cross_origin(self):
        response = self.client.put("/api/keys", json={}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403)

    def test_rejects_remote_and_rebound_host(self):
        self.assertEqual(self.client.get("/api/keys/status", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/keys/status", headers={"Host": "attacker.example"}).status_code, 403)

    def test_rejects_non_json_mutation(self):
        self.assertEqual(self.client.put("/api/keys", data="x=y").status_code, 415)

    def test_rejects_non_object_json(self):
        self.assertEqual(self.client.put("/api/keys", json=["unexpected"]).status_code, 400)

    def test_partial_key_update_keeps_other_keys(self):
        env = self.root / ".env"
        env.write_text("ANTHROPIC_API_KEY=test-old\nOPENAI_API_KEY=test-other\nCUSTOM=value\n")
        with patch.object(api_server, "ENV_PATH", env), patch.dict(os.environ, {}, clear=True):
            response = self.client.put("/api/keys", json={"ANTHROPIC_API_KEY": "test-new"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("OPENAI_API_KEY=test-other", env.read_text())
        self.assertIn("CUSTOM=value", env.read_text())
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_rejects_key_line_injection(self):
        self.assertEqual(self.client.put("/api/keys", json={"ANTHROPIC_API_KEY": "x\nINJECTED=y"}).status_code, 400)

    def test_apply_requires_explicit_approval(self):
        with patch.object(api_server.threading, "Thread") as thread:
            response = self.client.post("/api/jobs/apply", json={"url": "https://example.com/jobs/1", "dry_run": False})
        self.assertEqual(response.status_code, 400)
        thread.assert_not_called()

    def test_default_apply_does_not_prepare_or_launch(self):
        with patch.object(api_server.threading, "Thread") as thread:
            response = self.client.post("/api/jobs/apply", json={"url": "https://example.com/jobs/1"})
        self.assertEqual(response.status_code, 200)
        task = self.client.get(f"/api/jobs/apply/{response.json['task_id']}/status").json
        self.assertEqual(task["result"]["error"], "dry_run")
        thread.assert_not_called()

    def test_rejects_non_boolean_dry_run(self):
        response = self.client.post("/api/jobs/apply", json={"url": "https://example.com/jobs/1", "dry_run": "false"})
        self.assertEqual(response.status_code, 400)

    def test_prepare_without_resume_is_error_not_application(self):
        with patch.object(api_server, "ROOT", self.root), patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), patch.object(api_server, "_tracker") as tracker:
            response = self.client.post("/api/jobs/prepare", json={"title": "Engineer", "company": "Example", "mode": "ai"})
        self.assertEqual(response.status_code, 422)
        tracker.assert_not_called()

    def test_prepare_uses_canonical_job_contract(self):
        from pipeline.resume_generator import ResumeGenerator
        def fail_after_contract_check(job, profile, match_score):
            self.assertIsInstance(job, JobListing)
            self.assertEqual(job.requirements, [])
            raise RuntimeError("Simulated provider outage")
        master = self.root / "data/master_resume/resume.pdf"
        master.parent.mkdir(parents=True)
        master.write_bytes(b"fixture")
        tracker = Mock()
        tracker.add_application.return_value = 1
        with patch.object(api_server, "ROOT", self.root), patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), patch("apply_today._load_candidate_profile", return_value=CandidateProfile()), patch.object(ResumeGenerator, "generate_resume", side_effect=fail_after_contract_check) as generate, patch.object(api_server, "_tracker", return_value=tracker):
            response = self.client.post("/api/jobs/prepare", json={"title": "Engineer", "company": "Example", "mode": "ai"})
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once()
        self.assertFalse(response.json["ai_tailored"])


if __name__ == "__main__":
    unittest.main()
