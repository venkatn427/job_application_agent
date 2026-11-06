"""
apply_job.py — Standalone CLI for human-like job application.
--------------------------------------------------------------
Works fully offline (no Claude session needed).
If a configured AI provider key is set, uses AI to tailor resume + answer unknown fields.
Otherwise, runs 100% locally with master resume + memory lookup.

Usage:
    # Basic dry-run (detect ATS offline, no submission):
    python apply_job.py --url "https://jobs.lever.co/company/job-id" --dry-run

    # Full apply with portal credentials:
    python apply_job.py --url "https://..." --portal-id 2

    # AI mode (key auto-detected from env):
    ANTHROPIC_API_KEY=sk-ant-... python apply_job.py --url "..."

    # Visible browser for debugging:
    python apply_job.py --url "..." --no-headless --dry-run
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("apply_job")


def _load_profile(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    logger.warning("Profile not found: %s", path)
    return {}


def _resolve_portal_creds(portal_id: int) -> dict:
    """Return env-dict of credentials from portal store, or empty dict."""
    if not portal_id:
        return {}
    try:
        from submission.portal_store import PortalStore
        store = PortalStore()
        return store.get_creds_as_env_dict(portal_id)
    except Exception as e:
        logger.warning("Could not load portal %s: %s", portal_id, e)
        return {}


def _resolve_resume(resume_arg: str, job_url: str, job_title: str,
                    company: str, on_step) -> tuple:
    """Return (resume_pdf, cover_letter_pdf). Uses AI if key present."""
    from submission.apply_engine import ResumeAdapter, get_advisor
    adapter = ResumeAdapter(advisor=get_advisor())
    return adapter.prepare(
        job_url=job_url,
        job_title=job_title,
        company=company,
        on_step=on_step,
    )


def run_apply(url: str, portal_id: int = None, resume: str = None,
              headless: bool = True, dry_run: bool = None,
              job_title: str = "", company: str = "", submission_approved: bool = False) -> dict:
    """
    Full apply flow. Returns result dict.
    dry_run: defaults to True; skips preparation and browser launch.
    """
    if dry_run is None:
        dry_run = True
    if not dry_run and submission_approved is not True:
        return {"success": False, "url": url, "error": "submission_approval_required"}

    steps = []
    def _step(msg):
        steps.append(msg)
        logger.info(msg)

    _step(f"Starting apply: {url}")

    if dry_run:
        from submission.auto_apply import detect_platform
        _step("DRY RUN — skipping resume preparation and browser launch")
        return {"success": False, "platform": detect_platform(url),
                "url": url, "error": "dry_run", "steps": steps}

    # Resolve credentials
    extra_creds = _resolve_portal_creds(portal_id)
    if extra_creds:
        _step(f"Loaded credentials for portal #{portal_id}")

    # Prepare resume + cover letter
    _step("Preparing resume…")
    resume_pdf, cover_letter_pdf = _resolve_resume(
        resume, url, job_title, company, _step
    )
    if resume_pdf:
        _step(f"Resume: {Path(resume_pdf).name}")
    if cover_letter_pdf:
        _step(f"Cover letter: {Path(cover_letter_pdf).name}")

    # Real apply via AutoApplier
    from submission.auto_apply import AutoApplier

    applier = AutoApplier(
        profile_path=str(ROOT / "data" / "candidate_profile.json"),
        credentials_path=str(ROOT / "submission" / ".secrets.env"),
        resume_pdf_path=resume_pdf,
        cover_letter_path=cover_letter_pdf,
        headless=headless,
        dry_run=False,
        submission_approved=submission_approved,
        extra_creds=extra_creds,
        on_step=_step,
    )

    result = applier.apply(job_url=url)
    result["steps"] = steps

    # Mark portal last-used
    if result.get("success") and portal_id:
        try:
            from submission.portal_store import PortalStore
            PortalStore().touch_last_used(portal_id)
        except Exception:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Apply to a job URL using local Playwright browser automation."
    )
    parser.add_argument("--url",        required=True,  help="Job URL to apply to")
    parser.add_argument("--portal-id",  type=int,       help="Portal store ID for credentials")
    parser.add_argument("--resume",                     help="Path to resume PDF (overrides default)")
    parser.add_argument("--title",      default="",     help="Job title (for AI resume tailoring)")
    parser.add_argument("--company",    default="",     help="Company name (for AI tailoring)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Detect ATS offline; do not prepare resumes or launch a browser")
    parser.add_argument("--no-headless", action="store_true",
                        help="Show browser window (useful for debugging/OTP)")
    parser.add_argument("--submit", action="store_true",
                        help="Explicitly approve a live submission to this URL")
    args = parser.parse_args()

    if args.submit and args.dry_run:
        parser.error("--submit and --dry-run cannot be combined")
    dry_run = not args.submit
    headless = not args.no_headless

    result = run_apply(
        url=args.url,
        portal_id=args.portal_id,
        resume=args.resume,
        headless=headless,
        dry_run=dry_run,
        job_title=args.title,
        company=args.company,
        submission_approved=args.submit,
    )

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("success") or result.get("error") == "dry_run" else 1)


if __name__ == "__main__":
    main()
