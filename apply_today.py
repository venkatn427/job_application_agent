"""
Apply Today
-----------
Scrapes jobs posted today from free APIs (Greenhouse + Lever + RemoteOK),
scores them against the candidate profile, tailors resumes with Claude
(if ANTHROPIC_API_KEY is set), and submits applications via Playwright.
Tracks every application in data/applications.db.

Usage:
    python apply_today.py --dry-run          # See matching jobs, don't submit
    python apply_today.py --limit 3          # Apply to top 3 matches
    python apply_today.py --threshold 75     # Only apply to score ≥75
    python apply_today.py                    # Apply to all matches ≥70
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("apply_today")

ROOT = Path(__file__).parent


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_profile() -> dict:
    """Load submission/profile.json for Playwright field filling."""
    path = ROOT / "submission" / "profile.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    return json.loads(path.read_text())


def _load_candidate_profile():
    """
    Load or build a CandidateProfile.
    Without ANTHROPIC_API_KEY, falls back to a minimal profile built from
    submission/profile.json so keyword scoring still works.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        from pipeline.profile_analyzer import ProfileAnalyzer
        resume_path = ROOT / "data" / "master_resume" / "resume.pdf"
        analyzer = ProfileAnalyzer(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            cache_dir=ROOT / "data",
        )
        return analyzer.analyze(resume_path=resume_path if resume_path.exists() else None)

    # Keyword-only fallback — build from submission/profile.json
    from pipeline.profile_analyzer import CandidateProfile
    sub = json.loads((ROOT / "submission" / "profile.json").read_text())
    personal = sub.get("personal", {})
    prof = sub.get("professional", {})
    return CandidateProfile(
        name=f"{personal.get('first_name','')} {personal.get('last_name','')}".strip(),
        email=personal.get("email", ""),
        phone=personal.get("phone", ""),
        location=personal.get("city", ""),
        linkedin_url=prof.get("linkedin_url", ""),
        github_url=prof.get("github_url", ""),
        current_title=prof.get("current_title", ""),
        years_experience=float(prof.get("years_experience", 0)),
        skills=prof.get("skills", []),
        technologies=prof.get("technologies", []),
        programming_languages=prof.get("programming_languages", []),
        tools=prof.get("tools", []),
    )


def _load_settings() -> dict:
    """Load config/settings.yaml."""
    import yaml
    path = ROOT / "config" / "settings.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _scrape_today(queries: list[str]) -> list:
    """
    Scrape all supported job portals for today's postings.
    Free (no key): Greenhouse, Lever, Dice, RemoteOK, USAJobs*
    Paid (Apify token): LinkedIn, Indeed
    *USAJobs requires USAJOBS_API_KEY
    """
    from pipeline.apify_scraper import (
        GreenhouseScraper, LeverScraper, DiceScraper,
        RemoteOKScraper, USAJobsScraper,
    )

    settings = _load_settings()
    job_search = settings.get("job_search", {})
    greenhouse_companies = job_search.get("greenhouse_companies", [])
    lever_companies = job_search.get("lever_companies", [])

    all_jobs: list = []

    # ── Greenhouse (needs company slugs from settings.yaml) ───────────────────
    if greenhouse_companies:
        for q in queries:
            try:
                jobs = GreenhouseScraper().search(greenhouse_companies, q, 20)
                all_jobs.extend(jobs)
                if jobs:
                    logger.info("[Greenhouse] %d jobs for %r", len(jobs), q)
            except Exception as e:
                logger.warning("Greenhouse scrape failed for %r: %s", q, e)

    # ── Lever (needs company slugs from settings.yaml) ────────────────────────
    if lever_companies:
        for q in queries:
            try:
                jobs = LeverScraper().search(lever_companies, q, 20)
                all_jobs.extend(jobs)
                if jobs:
                    logger.info("[Lever] %d jobs for %r", len(jobs), q)
            except Exception as e:
                logger.warning("Lever scrape failed for %r: %s", q, e)

    # ── Dice (Easy Apply, posted today) ───────────────────────────────────────
    for q in queries:
        try:
            jobs = DiceScraper().search(q, max_results=25)
            all_jobs.extend(jobs)
            if jobs:
                logger.info("[Dice] %d jobs for %r", len(jobs), q)
        except Exception as e:
            logger.warning("Dice scrape failed for %r: %s", q, e)

    # ── RemoteOK ──────────────────────────────────────────────────────────────
    for q in queries:
        try:
            jobs = RemoteOKScraper().search(q, max_results=20)
            all_jobs.extend(jobs)
            if jobs:
                logger.info("[RemoteOK] %d jobs for %r", len(jobs), q)
        except Exception as e:
            logger.warning("RemoteOK scrape failed for %r: %s", q, e)

    # ── USAJobs (optional, needs USAJOBS_API_KEY) ─────────────────────────────
    for q in queries:
        try:
            jobs = USAJobsScraper().search(q, max_results=10)
            all_jobs.extend(jobs)
            if jobs:
                logger.info("[USAJobs] %d jobs for %r", len(jobs), q)
        except Exception as e:
            logger.warning("USAJobs scrape failed for %r: %s", q, e)

    # ── Apify (LinkedIn + Indeed) if token is set ──────────────────────────────
    if os.environ.get("APIFY_API_TOKEN"):
        from pipeline.apify_scraper import LinkedInApifyScraper, IndeedApifyScraper
        from pipeline.apify_scraper import ApifyClient
        client = ApifyClient(os.environ["APIFY_API_TOKEN"])
        apify_scrapers = [
            ("LinkedIn", LinkedInApifyScraper(client)),
            ("Indeed",   IndeedApifyScraper(client)),
        ]
        for name, scraper in apify_scrapers:
            for q in queries:
                try:
                    jobs = scraper.search(q, "Remote", 15)
                    all_jobs.extend(jobs)
                    if jobs:
                        logger.info("[%s] %d jobs for %r", name, len(jobs), q)
                except Exception as e:
                    logger.warning("%s (Apify) failed for %r: %s", name, q, e)

    # ── Deduplicate by URL / company+title ────────────────────────────────────
    seen: set = set()
    unique = []
    for j in all_jobs:
        key = getattr(j, "url", "") or f"{j.company}|{j.title}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    logger.info("Total unique jobs scraped across all portals: %d", len(unique))
    return unique


def _score_jobs(jobs: list, profile, threshold: int) -> list:
    """Return jobs scored ≥threshold, sorted by score desc."""
    from pipeline.job_matcher import JobMatcher
    matcher = JobMatcher(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    scored = []
    for job in jobs:
        score = matcher.keyword_score(job, profile)
        if score >= threshold:
            scored.append((score, job))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _tailor_resume(job, profile, score: int) -> tuple[Path, Path, str]:
    """
    Generate tailored resume + cover letter for a job.
    Falls back to master resume if no ANTHROPIC_API_KEY.
    Returns (resume_pdf_path, cover_letter_path, keywords_json).
    """
    import json as _json

    output_dir = ROOT / "output" / "applications" / _safe_name(job.company, job.title)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No API key — use master resume as-is
        master = ROOT / "data" / "master_resume" / "resume.pdf"
        logger.info("No ANTHROPIC_API_KEY — using master resume for %s", job.title)
        return master, None, "[]"

    from pipeline.resume_generator import ResumeGenerator
    from pipeline.document_builder import build_pdf, build_docx, build_cover_letter_docx

    gen = ResumeGenerator(api_key=os.environ["ANTHROPIC_API_KEY"])
    tailored = gen.generate_resume(job, profile, match_score=score)
    cover_text = gen.generate_cover_letter(job, profile)

    pdf_path = output_dir / "resume.pdf"
    docx_path = output_dir / "resume.docx"
    cl_path = output_dir / "cover_letter.docx"

    build_pdf(tailored, pdf_path)
    build_docx(tailored, docx_path)
    build_cover_letter_docx(cover_text, job.company, job.title,
                             tailored.candidate_name, cl_path)

    kw_json = _json.dumps(tailored.ats_keywords_used)
    return pdf_path, cl_path, kw_json


def _safe_name(*parts: str) -> str:
    name = " — ".join(str(p) for p in parts if p)
    return "".join(c if c.isalnum() or c in " —_-" else "_" for c in name)[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Apply to today's best-matching jobs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score and display jobs, don't submit")
    parser.add_argument("--limit", type=int, default=5,
                        help="Max applications to submit (default 5)")
    parser.add_argument("--threshold", type=int, default=70,
                        help="Minimum match score 0-100 (default 70)")
    parser.add_argument("--queries", nargs="+",
                        default=["Senior Software Engineer", "Staff Engineer",
                                 "Senior Backend Engineer", "Principal Engineer"],
                        help="Job search queries")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN — no applications will be submitted ===")

    # ── Load profile ─────────────────────────────────────────────────────────
    logger.info("Loading candidate profile...")
    submission_profile = _load_profile()
    try:
        candidate_profile = _load_candidate_profile()
        logger.info("Loaded profile: %s, %.0f years exp, %d skills",
                    candidate_profile.name, candidate_profile.years_experience,
                    len(candidate_profile.skills_flat()))
    except Exception as e:
        logger.warning("Could not load full profile (%s) — keyword scoring only", e)
        candidate_profile = None

    # ── Scrape ───────────────────────────────────────────────────────────────
    logger.info("Scraping jobs posted today...")
    jobs = _scrape_today(args.queries)
    if not jobs:
        logger.error("No jobs found. Check your internet connection.")
        sys.exit(1)

    # ── Score ────────────────────────────────────────────────────────────────
    if candidate_profile:
        scored = _score_jobs(jobs, candidate_profile, args.threshold)
    else:
        # Can't score without profile — take first N jobs
        scored = [(0, j) for j in jobs[:args.limit]]

    if not scored:
        logger.info("No jobs matched the ≥%d threshold. Try --threshold 60.", args.threshold)
        sys.exit(0)

    # ── Preview ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Jobs matching today (≥{args.threshold}/100) — top {args.limit}")
    print(f"{'='*60}")
    for i, (score, job) in enumerate(scored[:args.limit], 1):
        url = job.apply_url or "no url"
        print(f"  {i:2}. [{score:3}/100] {job.title} @ {job.company}")
        print(f"       {url}")
    print()

    if args.dry_run:
        print("Dry run complete. Remove --dry-run to submit.")
        return

    # ── Confirm before submitting ─────────────────────────────────────────────
    print(f"About to submit {min(len(scored), args.limit)} application(s).")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return

    # ── Apply ────────────────────────────────────────────────────────────────
    from pipeline.app_tracker import AppTracker
    from submission.auto_apply import AutoApplier

    tracker = AppTracker(db_path=ROOT / "data" / "applications.db")
    creds_path = str(ROOT / "submission" / ".secrets.env")

    results = []
    for score, job in scored[:args.limit]:
        url = job.apply_url or ""
        if not url:
            logger.warning("No URL for %s @ %s — skipping", job.title, job.company)
            continue

        logger.info("Applying: %s @ %s (score=%d)", job.title, job.company, score)

        # Tailor resume
        try:
            resume_path, cl_path, kw_json = _tailor_resume(job, candidate_profile, score)
        except Exception as e:
            logger.error("Resume tailoring failed: %s — using master resume", e)
            resume_path = ROOT / "data" / "master_resume" / "resume.pdf"
            cl_path = None
            kw_json = "[]"

        # Submit via Playwright
        applier = AutoApplier(
            credentials_path=creds_path,
            headless=True,
            dry_run=False,
        )
        applier._profile = {
            "name": submission_profile.get("personal", {}).get("first_name", "") + " " +
                    submission_profile.get("personal", {}).get("last_name", ""),
            "email": submission_profile.get("personal", {}).get("email", ""),
            "phone": submission_profile.get("personal", {}).get("phone", ""),
            "location": submission_profile.get("personal", {}).get("city", ""),
            "linkedin_url": submission_profile.get("professional", {}).get("linkedin_url", ""),
            "github_url": submission_profile.get("professional", {}).get("github_url", ""),
            "current_title": submission_profile.get("professional", {}).get("current_title", ""),
        }

        result = applier.apply(
            job_url=url,
            resume_pdf_path=resume_path,
            cover_letter_path=cl_path,
        )

        # Track locally
        try:
            import json as _json
            kw_list = _json.loads(kw_json)
            app_id = tracker.add_application(
                job=job,
                match_score=score,
                resume_pdf_path=resume_path,
                cover_letter_path=cl_path,
                ats_keywords=kw_list,
            )
            if result.get("success"):
                tracker.update_status(app_id, "applied",
                                      f"Submitted via Playwright on {datetime.now().date()}")
            else:
                tracker.update_status(app_id, "failed",
                                      f"Submit error: {result.get('error', 'unknown')}")
        except Exception as e:
            logger.error("Tracking failed: %s", e)

        status = "✓ Applied" if result.get("success") else f"✗ Failed ({result.get('error')})"
        results.append((job.title, job.company, score, status))
        logger.info("%s — %s @ %s", status, job.title, job.company)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}  RESULTS  {'='*60}")
    for title, company, score, status in results:
        print(f"  {status:30}  [{score:3}]  {title} @ {company}")
    print()

    stats = tracker.summary_stats()
    print(f"Total tracked applications: {stats.get('total', 0)} "
          f"({stats.get('in_progress', 0)} in progress, {stats.get('offers', 0)} offers)")
    print(f"Records saved to: {ROOT / 'data' / 'applications.db'}")


if __name__ == "__main__":
    main()
