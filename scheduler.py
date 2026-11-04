"""
Pipeline Scheduler
-------------------
Runs the job application pipeline on a daily schedule using APScheduler.
Also supports weekly analytics reports.

Usage:
    python scheduler.py             # Start the scheduler (runs in foreground)
    python scheduler.py --run-now   # Run pipeline immediately then schedule
    python scheduler.py --status    # Show next scheduled run times

Install deps: pip install apscheduler
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# ── Setup ──────────────────────────────────────────────────────────────────────
load_dotenv()

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT_DIR / "logs" / "scheduler.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Job functions (called by APScheduler)
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(force_profile_refresh: bool = False) -> None:
    """Execute the full job application pipeline."""
    from pipeline.workflow import Pipeline, PipelineConfig

    settings_path = Path(os.environ.get("PIPELINE_SETTINGS_PATH", "config/settings.yaml"))
    if not settings_path.is_absolute():
        settings_path = ROOT_DIR / settings_path

    logger.info("Starting pipeline run (settings=%s)", settings_path)
    try:
        config = PipelineConfig.from_yaml(settings_path)
        pipeline = Pipeline(config)
        summary = pipeline.run(force_profile_refresh=force_profile_refresh)
        logger.info(
            "Pipeline complete: %d matched, %d resumes",
            summary.jobs_matched, summary.resumes_generated,
        )
    except Exception as e:
        logger.error("Pipeline run failed: %s", e, exc_info=True)


def run_weekly_analytics() -> None:
    """Generate and send a weekly analytics report."""
    import sqlite3
    from pipeline.notifier import Notifier, PipelineRunSummary

    db_path = ROOT_DIR / "data" / "pipeline.db"
    if not db_path.exists():
        logger.info("No DB found — skipping weekly analytics")
        return

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Last 7 days stats
            stats = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='matched' THEN 1 ELSE 0 END) as matched,
                    SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                    AVG(CASE WHEN status='matched' THEN score END) as avg_score
                FROM processed_jobs
                WHERE processed_at >= datetime('now', '-7 days')
            """).fetchone()

            top_matches = conn.execute("""
                SELECT title, company, score
                FROM processed_jobs
                WHERE status='matched'
                AND processed_at >= datetime('now', '-7 days')
                ORDER BY score DESC
                LIMIT 10
            """).fetchall()

        summary = PipelineRunSummary(
            run_date=f"Weekly Report — {datetime.now().strftime('%Y-%m-%d')}",
            jobs_scraped=stats["total"] or 0,
            jobs_matched=stats["matched"] or 0,
            jobs_skipped=stats["skipped"] or 0,
        )
        summary.top_matches = [
            {"title": r["title"], "company": r["company"], "score": r["score"], "url": ""}
            for r in top_matches
        ]
        avg = stats["avg_score"]
        summary.tailoring_notes = f"Average match score this week: {avg:.1f}/100" if avg else ""

        notifier = Notifier()
        notifier.send_run_summary(summary)
        logger.info("Weekly analytics report sent")

    except Exception as e:
        logger.error("Weekly analytics failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler setup
# ─────────────────────────────────────────────────────────────────────────────

def build_scheduler():
    """Build and configure the APScheduler."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install apscheduler")
        raise

    import yaml
    settings_path = ROOT_DIR / "config" / "settings.yaml"
    sched_config = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                data = yaml.safe_load(f) or {}
            sched_config = data.get("scheduler", {})
        except Exception:
            pass

    run_time = sched_config.get("daily_run_time", "08:00")
    timezone = sched_config.get("timezone", "America/Los_Angeles")
    hour, minute = run_time.split(":")

    scheduler = BlockingScheduler(timezone=timezone)

    # Daily pipeline run
    scheduler.add_job(
        run_pipeline,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=timezone),
        id="daily_pipeline",
        name="Daily Job Application Pipeline",
        replace_existing=True,
        misfire_grace_time=3600,  # if missed by <1hr, still run
    )

    # Weekly analytics report (Sundays at 09:00)
    scheduler.add_job(
        run_weekly_analytics,
        trigger=CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=timezone),
        id="weekly_analytics",
        name="Weekly Analytics Report",
        replace_existing=True,
    )

    return scheduler


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Job Application Pipeline Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler.py             # Start daemon (runs at configured time daily)
  python scheduler.py --run-now   # Run pipeline right now, then start scheduler
  python scheduler.py --once      # Run pipeline once and exit (no scheduling)
  python scheduler.py --status    # Print next scheduled run times and exit
        """,
    )
    parser.add_argument("--run-now", action="store_true", help="Run pipeline immediately before scheduling")
    parser.add_argument("--once", action="store_true", help="Run pipeline once and exit")
    parser.add_argument("--status", action="store_true", help="Show next run times and exit")
    parser.add_argument("--force-refresh", action="store_true", help="Re-parse candidate profile from disk")
    args = parser.parse_args()

    # Ensure log dir exists
    (ROOT_DIR / "logs").mkdir(exist_ok=True)

    if args.once:
        logger.info("Single run mode")
        run_pipeline(force_profile_refresh=args.force_refresh)
        return

    scheduler = build_scheduler()

    if args.status:
        print("\nScheduled Jobs:")
        scheduler.start(paused=True)
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            print(f"  * {job.name}")
            print(f"    Next run: {next_run}")
        scheduler.shutdown(wait=False)
        return

    if args.run_now:
        logger.info("Running pipeline now before starting scheduler...")
        run_pipeline(force_profile_refresh=args.force_refresh)

    logger.info("Starting scheduler. Press Ctrl+C to stop.")
    try:
        # Print scheduled jobs
        for job in scheduler.get_jobs():
            logger.info("Scheduled: %s (next: %s)", job.name, job.next_run_time)
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
