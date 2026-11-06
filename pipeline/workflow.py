"""
Main Pipeline Workflow
-----------------------
Orchestrates the full end-to-end job application automation:

  1. Fetch jobs (Apify / fallback scrapers)
  2. Load & analyze candidate profile
  3. Score each job (1–100)
  4. Skip jobs below threshold
  5. Generate tailored resume + cover letter
  6. Build PDF + DOCX
  7. Upload to Google Drive
  8. Update Google Sheets tracker
  9. Send notifications
 10. Log everything

Designed to run daily via scheduler.py or manually via main.py.
"""

import os
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .apify_scraper import JobFetcher, JobListing
from .profile_analyzer import ProfileAnalyzer, CandidateProfile
from .job_matcher import JobMatcher, MatchResult
from .resume_generator import ResumeGenerator
from .document_builder import build_pdf, build_docx, build_cover_letter_docx
from .drive_manager import DriveManager
from .sheets_tracker import SheetsTracker, ApplicationRow
from .notifier import Notifier, PipelineRunSummary

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline config
# ─────────────────────────────────────────────────────────────────────────────

class PipelineConfig:
    """All pipeline settings, loaded from settings.yaml + env overrides."""

    def __init__(self, settings: dict):
        s = settings
        self.ai_config = s.get("ai", {})

        # Job search
        js = s.get("job_search", {})
        self.queries: list[str] = js.get("queries", ["Software Engineer"])
        self.locations: list[str] = js.get("locations", ["Remote"])
        self.max_per_source: int = js.get("max_per_source", 25)
        self.greenhouse_companies: list[str] = js.get("greenhouse_companies", [])
        self.lever_companies: list[str] = js.get("lever_companies", [])
        self.sources: list[str] = js.get("sources", ["linkedin", "indeed", "greenhouse", "lever"])

        # Matching
        match = s.get("matching", {})
        self.match_threshold: int = match.get("threshold", 70)
        self.use_claude_scoring: bool = match.get("use_claude_scoring", True)

        # Resume
        resume = s.get("resume", {})
        self.generate_cover_letter: bool = resume.get("generate_cover_letter", True)
        self.cover_letter_tone: str = resume.get("cover_letter_tone", "professional")
        self.resume_model: str = resume.get("model")

        # Paths — base_dir is resolved by from_yaml() relative to the YAML file;
        # when PipelineConfig is constructed directly (tests), it defaults to CWD.
        paths = s.get("paths", {})
        base = Path(paths.get("base_dir", "."))
        self.resume_path: Optional[Path] = _resolve(base, paths.get("master_resume"))
        self.linkedin_export_path: Optional[Path] = _resolve(base, paths.get("linkedin_export"))
        self.output_dir: Path = base / paths.get("output_dir", "output")
        self.data_dir: Path = base / paths.get("data_dir", "data")
        self.log_dir: Path = base / paths.get("log_dir", "logs")
        self.db_path: Path = base / paths.get("db_path", "data/pipeline.db")

        # Notifications
        notif = s.get("notifications", {})
        self.notify_on_match: bool = notif.get("on_match", True)
        self.notify_on_run_complete: bool = notif.get("on_run_complete", True)

        # Filters
        filters = s.get("filters", {})
        self.exclude_titles: list[str] = filters.get("exclude_titles", [])
        self.required_keywords: list[str] = filters.get("required_keywords", [])
        self.min_salary: int = int(filters.get("min_salary", 0))
        self.job_types: list[str] = filters.get("job_types", [])

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "PipelineConfig":
        try:
            import yaml
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Could not load %s: %s — using defaults", yaml_path, e)
            data = {}
        # Resolve base_dir relative to the YAML file's directory so the pipeline
        # always finds the right data/ and output/ folders regardless of CWD.
        yaml_dir = yaml_path.resolve().parent
        raw_base = data.get("paths", {}).get("base_dir", ".")
        resolved_base = (
            Path(raw_base) if Path(raw_base).is_absolute()
            else (yaml_dir / raw_base).resolve()
        )
        if "paths" not in data:
            data["paths"] = {}
        data["paths"]["base_dir"] = str(resolved_base)
        return cls(data)


def _resolve(base: Path, rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else base / p


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication store
# ─────────────────────────────────────────────────────────────────────────────

class PipelineDB:
    """SQLite store for processed jobs, errors, and run logs."""

    DDL = """
    CREATE TABLE IF NOT EXISTS processed_jobs (
        job_id      TEXT PRIMARY KEY,
        title       TEXT,
        company     TEXT,
        source      TEXT,
        score       INTEGER,
        status      TEXT,  -- 'matched' | 'skipped' | 'failed'
        processed_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS run_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date    TEXT NOT NULL,
        jobs_scraped INTEGER DEFAULT 0,
        jobs_matched INTEGER DEFAULT 0,
        jobs_skipped INTEGER DEFAULT 0,
        resumes_built INTEGER DEFAULT 0,
        errors      TEXT,  -- JSON list
        duration_sec REAL
    );
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.DDL)

    def is_processed(self, job_id: str) -> bool:
        """Return True only for jobs that completed successfully (matched or skipped).
        Failed jobs are allowed to retry on the next run."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM processed_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return False
        return row[0] in ("matched", "skipped")

    def mark_processed(self, job: JobListing, score: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO processed_jobs
                   (job_id, title, company, source, score, status, processed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    job.job_id, job.title, job.company, job.source,
                    score, status,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def log_run(self, run_date: str, summary: dict, duration: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO run_log
                   (run_date, jobs_scraped, jobs_matched, jobs_skipped,
                    resumes_built, errors, duration_sec)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    run_date,
                    summary.get("scraped", 0),
                    summary.get("matched", 0),
                    summary.get("skipped", 0),
                    summary.get("resumes", 0),
                    json.dumps(summary.get("errors", [])),
                    duration,
                ),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class Pipeline:
    """
    Full job application automation pipeline.

    Usage:
        config = PipelineConfig.from_yaml(Path("config/settings.yaml"))
        pipeline = Pipeline(config)
        pipeline.run()
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.config.log_dir.mkdir(parents=True, exist_ok=True)

        # Core components
        self.db = PipelineDB(self.config.db_path)
        self.fetcher = JobFetcher(apify_token=os.environ.get("APIFY_API_TOKEN"))
        self.profile_analyzer = ProfileAnalyzer(cache_dir=self.config.data_dir, ai_config=self.config.ai_config)
        self.matcher = JobMatcher(
            threshold=self.config.match_threshold,
            use_claude=self.config.use_claude_scoring,
            ai_config=self.config.ai_config,
        )
        self.resume_gen = ResumeGenerator(model=self.config.resume_model, ai_config=self.config.ai_config)
        self.drive = DriveManager()
        self.tracker = SheetsTracker(
            local_csv_path=self.config.log_dir / "applications.csv"
        )
        self.notifier = Notifier()
        self._errors: list[str] = []

    # ── Main run ────────────────────────────────────────────────────────────────

    def run(self, force_profile_refresh: bool = False) -> PipelineRunSummary:
        """Execute the full pipeline. Returns a run summary."""
        import time
        start = time.monotonic()
        self._errors = []
        run_date = datetime.now().strftime("%Y-%m-%d")
        logger.info("=" * 60)
        logger.info("Pipeline run started: %s", run_date)

        summary = PipelineRunSummary(run_date=run_date)
        summary.spreadsheet_url = self.tracker.get_spreadsheet_url() or ""

        # Step 1: Load candidate profile
        profile = self._load_profile(force_profile_refresh)
        if not profile.name or not (profile.skills_flat() or profile.work_experience):
            raise ValueError("Candidate profile must contain a name and skills or work experience before scoring")

        # Step 2: Fetch jobs
        jobs = self._fetch_jobs()
        summary.jobs_scraped = len(jobs)

        # Step 3: Deduplicate against already-processed jobs
        new_jobs = [j for j in jobs if not self.db.is_processed(j.job_id)]
        logger.info("New jobs to process: %d (of %d scraped)", len(new_jobs), len(jobs))

        # Step 4: Score all new jobs
        results = self.matcher.score_batch(new_jobs, profile)
        summary.jobs_scored = len(results)

        matched = [r for r in results if r.recommended]
        skipped = [r for r in results if not r.recommended]
        summary.jobs_matched = len(matched)
        summary.jobs_skipped = len(skipped)

        # Log skipped
        for r in skipped:
            logger.info("SKIP [%d] %s @ %s", r.score, r.job.title, r.job.company)
            self.db.mark_processed(r.job, r.score, "skipped")

        # Step 5: Process matched jobs
        for result in matched:
            success = self._process_match(result, profile, summary)
            if not success:
                self._errors.append(f"Failed: {result.job.title} @ {result.job.company}")

        # Step 6: Log run
        duration = time.monotonic() - start
        summary.errors = self._errors
        self.db.log_run(run_date, {
            "scraped": summary.jobs_scraped,
            "matched": summary.jobs_matched,
            "skipped": summary.jobs_skipped,
            "resumes": summary.resumes_generated,
            "errors": self._errors,
        }, duration)

        logger.info(
            "Pipeline complete: %d matched, %d skipped, %d resumes, %.1fs",
            summary.jobs_matched, summary.jobs_skipped,
            summary.resumes_generated, duration,
        )

        # Step 7: Notify
        if self.config.notify_on_run_complete:
            self.notifier.send_run_summary(summary)

        self._write_run_report(summary)
        return summary

    # ── Steps ───────────────────────────────────────────────────────────────────

    def _load_profile(self, force: bool) -> CandidateProfile:
        logger.info("Loading candidate profile...")
        try:
            return self.profile_analyzer.analyze(
                resume_path=self.config.resume_path,
                linkedin_export_path=self.config.linkedin_export_path,
                force_refresh=force,
            )
        except Exception as e:
            logger.error("Profile loading failed: %s", e)
            self._errors.append(f"Profile load error: {e}")
            return CandidateProfile()

    def _fetch_jobs(self) -> list[JobListing]:
        logger.info("Fetching job listings...")
        try:
            return self.fetcher.fetch_all(
                queries=self.config.queries,
                locations=self.config.locations,
                max_per_source=self.config.max_per_source,
                greenhouse_companies=self.config.greenhouse_companies,
                lever_companies=self.config.lever_companies,
                sources=self.config.sources,
                exclude_titles=self.config.exclude_titles,
            )
        except Exception as e:
            logger.error("Job fetch failed: %s", e)
            self._errors.append(f"Job fetch error: {e}")
            return []

    def _process_match(
        self,
        result: MatchResult,
        profile: CandidateProfile,
        summary: PipelineRunSummary,
    ) -> bool:
        job = result.job
        logger.info("Processing match [%d] %s @ %s", result.score, job.title, job.company)

        try:
            # Prepare output paths
            safe = _safe_name(f"{job.company}_{job.title}")
            job_dir = self.config.output_dir / safe
            job_dir.mkdir(parents=True, exist_ok=True)

            # Generate resume
            tailored = self.resume_gen.generate_resume(job, profile, match_score=result.score)

            # Build documents
            pdf_path = job_dir / "Tailored_Resume.pdf"
            docx_path = job_dir / "Tailored_Resume.docx"
            build_pdf(tailored, pdf_path)
            build_docx(tailored, docx_path)

            # Cover letter
            cl_path: Optional[Path] = None
            if self.config.generate_cover_letter:
                cl_text = self.resume_gen.generate_cover_letter(
                    job, profile, tone=self.config.cover_letter_tone
                )
                cl_path = job_dir / "Cover_Letter.docx"
                build_cover_letter_docx(cl_text, job.company, job.title, profile.name, cl_path)

            # Match analysis file
            analysis = self._build_analysis(result)
            analysis_path = job_dir / "Match_Analysis.txt"
            analysis_path.write_text(analysis, encoding="utf-8")

            # Upload to Drive — optional; errors here don't fail the job
            resume_link = str(pdf_path)
            try:
                drive_links = self.drive.upload_application_package(
                    company=job.company,
                    job_title=job.title,
                    job_description=job.description or "",
                    resume_pdf_path=pdf_path,
                    resume_docx_path=docx_path,
                    cover_letter_docx_path=cl_path,
                    match_analysis_text=analysis,
                )
                resume_link = drive_links.get("Resume PDF") or resume_link
            except Exception as drive_err:
                logger.warning("Drive upload failed for %r @ %r: %s — continuing", job.title, job.company, drive_err)

            # Sheets tracker — also optional
            try:
                self.tracker.upsert(ApplicationRow(
                    company=job.company,
                    job_title=job.title,
                    job_url=job.apply_url or "",
                    match_score=result.score,
                    resume_link=resume_link,
                    status="Pending",
                    source=job.source or "",
                    missing_skills=", ".join(result.missing_keywords[:8]),
                    interview_topics=", ".join(result.interview_prep_topics[:5]),
                ))
            except Exception as sheets_err:
                logger.warning("Sheets upsert failed for %r @ %r: %s — continuing", job.title, job.company, sheets_err)

            # DB
            self.db.mark_processed(job, result.score, "matched")
            summary.resumes_generated += 1
            summary.top_matches.append({
                "title": job.title,
                "company": job.company,
                "score": result.score,
                "url": job.apply_url or "",
            })

            # Per-match notification
            if self.config.notify_on_match:
                self.notifier.send_match_alert(
                    job.title, job.company, result.score, job.apply_url or ""
                )

            logger.info("SUCCESS: %s @ %s → %s", job.title, job.company, job_dir)
            return True

        except Exception as e:
            logger.error("Process match failed for %r @ %r: %s", job.title, job.company, e, exc_info=True)
            self.db.mark_processed(job, result.score, "failed")
            return False

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _build_analysis(self, result: MatchResult) -> str:
        job = result.job
        lines = [
            f"MATCH ANALYSIS",
            f"==============",
            f"Job Title  : {job.title}",
            f"Company    : {job.company}",
            f"Location   : {job.location or 'Not specified'}",
            f"Source     : {job.source or 'Unknown'}",
            f"Apply URL  : {job.apply_url or 'N/A'}",
            f"Match Score: {result.score}/100",
            f"",
            f"MATCHED KEYWORDS ({len(result.matched_keywords)}):",
            ", ".join(result.matched_keywords) or "None",
            f"",
            f"MISSING KEYWORDS ({len(result.missing_keywords)}):",
            ", ".join(result.missing_keywords) or "None",
            f"",
            f"MATCH REASONS:",
            *[f"  • {r}" for r in result.match_reasons],
            f"",
            f"GAPS TO ADDRESS:",
            *(([f"  • {g}" for g in result.gaps]) if result.gaps else ["  None identified"]),
            f"",
            f"INTERVIEW PREP TOPICS:",
            *(([f"  • {t}" for t in result.interview_prep_topics]) if result.interview_prep_topics
              else ["  Not generated"]),
            f"",
            f"Processed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        return "\n".join(lines)

    def _write_run_report(self, summary: PipelineRunSummary) -> None:
        report_path = self.config.log_dir / f"run_{summary.run_date}.txt"
        report_path.write_text(summary.to_text(), encoding="utf-8")
        logger.info("Run report saved to %s", report_path)


def _safe_name(s: str, max_len: int = 60) -> str:
    """Convert to a filesystem-safe name."""
    import re
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:max_len]
