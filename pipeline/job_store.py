"""Persistent discovery history, independent of resume generation/submission."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import contextmanager


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS discovered_jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_first_seen ON discovered_jobs(first_seen)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, jobs, seen_at=None):
        stamp = seen_at or datetime.now(timezone.utc).isoformat()
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo('America/Chicago'))
        stamp = stamp.astimezone(timezone.utc).isoformat()
        with self.connect() as db:
            for job in jobs:
                identity = job.get('apply_url') or job.get('url') or f"{job.get('company')}|{job.get('title')}"
                key = hashlib.sha256(identity.encode()).hexdigest()
                db.execute("INSERT INTO discovered_jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=MAX(last_seen,excluded.last_seen)",
                           (key, json.dumps(job), stamp, stamp))

    def list(self, period='all', now=None):
        local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('America/Chicago'))
        today = local.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = {'today': today, 'yesterday': today-timedelta(days=1),
                  'week': today-timedelta(days=today.weekday()), 'month': today.replace(day=1)}
        if period not in {*starts, 'all'}:
            raise ValueError('Invalid period')
        clause, args = '', []
        if period != 'all':
            end = today if period == 'yesterday' else local
            clause = ' WHERE first_seen>=? AND first_seen<?'
            args = [starts[period].astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()]
        with self.connect() as db:
            rows = db.execute('SELECT payload,first_seen,last_seen FROM discovered_jobs'+clause+' ORDER BY first_seen DESC', args).fetchall()
        return [{**json.loads(row[0]), 'first_seen': row[1], 'last_seen': row[2]} for row in rows]


def collect_jobs(root, *, return_jobs=False, all_public=False):
    from .workflow import PipelineConfig
    from .apify_scraper import JobFetcher
    config = PipelineConfig.from_yaml(Path(root)/'config/settings.yaml')
    jobs = JobFetcher().fetch_all(queries=config.queries, locations=config.locations,
        max_per_source=config.max_per_source, sources=config.sources,
        greenhouse_companies=config.greenhouse_companies,
        lever_companies=config.lever_companies, exclude_titles=config.exclude_titles,
        **({'all_public': True} if all_public else {}))
    payload = [j.to_dict() for j in jobs]
    JobStore(Path(root)/'data/discovered_jobs.db').save(payload)
    return payload if return_jobs else len(jobs)


if __name__ == '__main__':
    import argparse
    import logging
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.daily:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler(timezone='America/Chicago')
        scheduler.add_job(collect_jobs, 'cron', hour=8, args=[root],
                          max_instances=1, coalesce=True, misfire_grace_time=3600)
        logging.info('Discovery scheduled daily at 08:00 America/Chicago; keep this process running')
        scheduler.start()
    else:
        print(f'Saved {collect_jobs(root)} fetched jobs')
