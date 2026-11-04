"""
Job Application Agent — CLI Entry Point
-----------------------------------------
Run the job application automation pipeline from the command line.

Usage:
    python main.py run              # Run full pipeline
    python main.py run --refresh    # Re-parse profile before running
    python main.py profile          # Parse and display candidate profile
    python main.py score <job_url>  # Score a single job URL
    python main.py stats            # Show application statistics
    python main.py schedule         # Start the daily scheduler
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Bootstrap ──────────────────────────────────────────────────────────────────
load_dotenv()
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
(ROOT_DIR / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT_DIR / "logs" / "pipeline.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

SETTINGS_PATH = ROOT_DIR / os.environ.get("PIPELINE_SETTINGS_PATH", "config/settings.yaml")


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    """Run the full pipeline."""
    from pipeline.workflow import Pipeline, PipelineConfig

    print("🚀 Starting Job Application Pipeline...")
    config = PipelineConfig.from_yaml(SETTINGS_PATH)
    pipeline = Pipeline(config)
    summary = pipeline.run(force_profile_refresh=args.refresh)

    print("\n" + "=" * 60)
    print(summary.to_text())
    return 0 if not summary.errors else 1


def cmd_profile(args) -> int:
    """Parse and display the candidate profile."""
    from pipeline.profile_analyzer import ProfileAnalyzer
    from pipeline.workflow import PipelineConfig

    config = PipelineConfig.from_yaml(SETTINGS_PATH)
    analyzer = ProfileAnalyzer(cache_dir=config.data_dir)
    profile = analyzer.analyze(
        resume_path=config.resume_path,
        linkedin_export_path=config.linkedin_export_path,
        force_refresh=args.refresh,
    )

    print("\n" + "=" * 60)
    print(f"👤 Candidate Profile: {profile.name}")
    print("=" * 60)
    print(f"Email       : {profile.email}")
    print(f"Location    : {profile.location}")
    print(f"Current     : {profile.current_title}")
    print(f"Experience  : {profile.years_experience:.1f} years")
    print(f"Target roles: {', '.join(profile.target_titles)}")
    print(f"\nSkills ({len(profile.skills_flat())}):")
    print("  " + ", ".join(profile.skills_flat()[:20]))
    print(f"\nDomain expertise:")
    print("  " + ", ".join(profile.domain_expertise))
    print(f"\nWork history:")
    for exp in profile.work_experience[:5]:
        print(f"  • {exp.title} @ {exp.company} ({exp.start_date} – {exp.end_date or 'Present'})")
    print(f"\nEducation:")
    for edu in profile.education:
        print(f"  • {edu.degree} — {edu.institution} ({edu.graduation_year})")
    if profile.certifications:
        print(f"\nCertifications:")
        for cert in profile.certifications:
            print(f"  • {cert.name} ({cert.issuer})")
    print()
    return 0


def cmd_score(args) -> int:
    """Score a single job URL against the candidate profile."""
    import requests
    from bs4 import BeautifulSoup
    from pipeline.apify_scraper import JobListing
    from pipeline.profile_analyzer import ProfileAnalyzer
    from pipeline.job_matcher import JobMatcher
    from pipeline.workflow import PipelineConfig

    url = args.url
    print(f"🔍 Fetching: {url}")
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        soup = BeautifulSoup(resp.text, "lxml")
        description = soup.get_text(separator="\n")[:6000]
        title = (soup.find("title") or soup.find("h1") or soup.find("h2"))
        job_title = title.get_text().strip()[:100] if title else "Unknown Role"
    except Exception as e:
        print(f"❌ Could not fetch URL: {e}")
        return 1

    job = JobListing(title=job_title, company=args.company or "Company", description=description, apply_url=url)
    config = PipelineConfig.from_yaml(SETTINGS_PATH)
    analyzer = ProfileAnalyzer(cache_dir=config.data_dir)
    profile = analyzer.analyze(resume_path=config.resume_path)
    matcher = JobMatcher(threshold=config.match_threshold, use_claude=True)
    result = matcher.score_job(job, profile)

    print("\n" + "=" * 60)
    print(result.summary())
    print(f"\nMatch reasons:")
    for r in result.match_reasons:
        print(f"  • {r}")
    if result.gaps:
        print(f"\nGaps:")
        for g in result.gaps:
            print(f"  • {g}")
    if result.interview_prep_topics:
        print(f"\nInterview prep:")
        for t in result.interview_prep_topics:
            print(f"  • {t}")
    print()
    return 0


def cmd_stats(args) -> int:
    """Show application statistics from the database."""
    import sqlite3
    from datetime import datetime, timedelta

    db_path = ROOT_DIR / "data" / "pipeline.db"
    csv_path = ROOT_DIR / "logs" / "applications.csv"

    print("\n📊 Application Statistics")
    print("=" * 50)

    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            total = conn.execute("SELECT COUNT(*) FROM processed_jobs").fetchone()[0]
            matched = conn.execute("SELECT COUNT(*) FROM processed_jobs WHERE status='matched'").fetchone()[0]
            skipped = conn.execute("SELECT COUNT(*) FROM processed_jobs WHERE status='skipped'").fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(score) FROM processed_jobs WHERE status='matched'"
            ).fetchone()[0]

            print(f"Total processed : {total}")
            print(f"Matched (≥score): {matched}")
            print(f"Skipped         : {skipped}")
            print(f"Avg match score : {avg_score:.1f}/100" if avg_score else "Avg match score : N/A")

            print("\nTop Matches (All Time):")
            top = conn.execute("""
                SELECT title, company, score FROM processed_jobs
                WHERE status='matched' ORDER BY score DESC LIMIT 10
            """).fetchall()
            for r in top:
                print(f"  [{r['score']:3d}] {r['title']} @ {r['company']}")

            print("\nRun History (Last 10 Runs):")
            runs = conn.execute("""
                SELECT run_date, jobs_matched, jobs_scraped, resumes_built, duration_sec
                FROM run_log ORDER BY id DESC LIMIT 10
            """).fetchall()
            for r in runs:
                print(
                    f"  {r['run_date']} | "
                    f"scraped={r['jobs_scraped']} matched={r['jobs_matched']} "
                    f"resumes={r['resumes_built']} time={r['duration_sec']:.0f}s"
                )
    else:
        print("No pipeline database found. Run 'python main.py run' first.")

    if csv_path.exists():
        import csv
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        print(f"\n📋 Application Tracker: {len(rows)} entries ({csv_path})")

    print()
    return 0


def cmd_schedule(args) -> int:
    """Start the daily scheduler."""
    import subprocess
    cmd = [sys.executable, str(ROOT_DIR / "scheduler.py")]
    if args.run_now:
        cmd.append("--run-now")
    if args.refresh:
        cmd.append("--force-refresh")
    os.execv(sys.executable, cmd)
    return 0  # unreachable


# ─────────────────────────────────────────────────────────────────────────────
# CLI setup
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="job-agent",
        description="AI-powered job application automation agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py run                        # Full pipeline run
  python main.py run --refresh              # Re-parse profile first
  python main.py profile                    # Show parsed candidate profile
  python main.py profile --refresh          # Re-parse from disk
  python main.py score https://example.com/jobs/123 --company "Acme"
  python main.py stats                      # Application statistics
  python main.py schedule                   # Start daily scheduler
  python main.py schedule --run-now         # Run now + schedule
        """,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # run
    p_run = subparsers.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--refresh", action="store_true", help="Force re-parse of candidate profile")

    # profile
    p_profile = subparsers.add_parser("profile", help="Parse and display candidate profile")
    p_profile.add_argument("--refresh", action="store_true", help="Force re-parse from disk")

    # score
    p_score = subparsers.add_parser("score", help="Score a single job URL")
    p_score.add_argument("url", help="Job posting URL to score")
    p_score.add_argument("--company", default="", help="Company name (optional)")

    # stats
    p_stats = subparsers.add_parser("stats", help="Show application statistics")

    # schedule
    p_sched = subparsers.add_parser("schedule", help="Start the daily scheduler")
    p_sched.add_argument("--run-now", action="store_true", help="Run pipeline immediately before scheduling")
    p_sched.add_argument("--refresh", action="store_true", help="Force re-parse of profile on first run")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "run": cmd_run,
        "profile": cmd_profile,
        "score": cmd_score,
        "stats": cmd_stats,
        "schedule": cmd_schedule,
    }
    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
