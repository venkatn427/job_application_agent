import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from pipeline.job_store import JobStore


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(Path(self.temp.name)/'jobs.db')
        self.now = datetime(2026, 11, 5, 18, tzinfo=timezone.utc)

    def test_history_and_upsert(self):
        job = {'title': 'Engineer', 'company': 'Example', 'url': 'https://example.com/1'}
        self.store.save([job], '2026-11-04T18:00:00+00:00')
        self.store.save([{**job, 'description': 'Updated'}], '2026-11-05T17:00:00+00:00')
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.list('today', self.now)), 0)
        self.assertEqual(len(self.store.list('yesterday', self.now)), 1)
        self.assertEqual(self.store.list()[0]['description'], 'Updated')

    def test_calendar_filters(self):
        for day in (1, 2, 4, 5):
            self.store.save([{'url': f'https://example.com/{day}'}], f'2026-11-{day:02d}T12:00:00+00:00')
        self.assertEqual(len(self.store.list('today', self.now)), 1)
        self.assertEqual(len(self.store.list('yesterday', self.now)), 1)
        self.assertEqual(len(self.store.list('week', self.now)), 3)
        self.assertEqual(len(self.store.list('month', self.now)), 4)

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            self.store.list('invalid')

    def test_empty_refresh_retains_history(self):
        self.store.save([{'url': 'https://example.com/1'}])
        self.store.save([])
        self.assertEqual(len(self.store.list()), 1)
