"""
Application Tracker — multi-table SQLite database
---------------------------------------------------
Tracks every job application and all associated details across 7 tables:

  applications    — one row per job applied to
  companies       — company info (deduplicated)
  contacts        — recruiters / hiring managers per application
  interview_stages — phone screen, technical, onsite, offer, etc.
  documents       — resume / cover letter / other files per application
  notes           — free-form notes and AI coaching tips
  follow_ups      — scheduled follow-up actions

Usage:
    from pipeline.app_tracker import AppTracker
    tracker = AppTracker()
    app_id = tracker.add_application(job, match_score=85, resume_pdf_path=...)
    tracker.add_note(app_id, "Prep: focus on distributed systems")
    tracker.add_stage(app_id, "phone_screen", scheduled_at="2026-11-10")
"""

import sqlite3
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    website         TEXT,
    industry        TEXT,
    size            TEXT,
    hq_location     TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER REFERENCES companies(id),
    job_title       TEXT NOT NULL,
    job_url         TEXT,
    job_board       TEXT,
    location        TEXT,
    remote          INTEGER NOT NULL DEFAULT 0,     -- 0/1 bool
    salary_min      INTEGER,
    salary_max      INTEGER,
    salary_currency TEXT NOT NULL DEFAULT 'USD',
    job_type        TEXT,                           -- full-time, contract, etc.
    match_score     INTEGER,
    status          TEXT NOT NULL DEFAULT 'prepared',
    -- status values: applied, phone_screen, technical, onsite, offer,
    --                rejected, withdrawn, ghosted, accepted
    applied_at      TEXT,
    description_snippet TEXT,
    sponsorship_required INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    name            TEXT,
    title           TEXT,
    email           TEXT,
    linkedin_url    TEXT,
    phone           TEXT,
    role            TEXT NOT NULL DEFAULT 'recruiter',
    -- role values: recruiter, hiring_manager, interviewer, referral
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS interview_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    stage_name      TEXT NOT NULL,
    -- e.g. phone_screen, technical_1, system_design, onsite, offer
    scheduled_at    TEXT,
    completed_at    TEXT,
    outcome         TEXT,   -- passed, failed, pending, cancelled
    duration_min    INTEGER,
    interviewer     TEXT,
    notes           TEXT,
    prep_tips       TEXT,   -- AI-generated coaching
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL,
    -- e.g. resume_pdf, resume_docx, cover_letter, portfolio, other
    local_path      TEXT,
    drive_url       TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    ats_keywords    TEXT,   -- JSON array of keywords used
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',  -- manual, ai, system
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS follow_ups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    -- e.g. send_thank_you, follow_up_email, check_status
    due_at          TEXT,
    completed_at    TEXT,
    done            INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_applications_status      ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company     ON applications(company_id);
CREATE INDEX IF NOT EXISTS idx_applications_applied_at  ON applications(applied_at);
CREATE INDEX IF NOT EXISTS idx_stages_application       ON interview_stages(application_id);
CREATE INDEX IF NOT EXISTS idx_followups_due            ON follow_ups(due_at) WHERE done=0;
"""


class AppTracker:
    """
    Persistent multi-table application tracker backed by SQLite.
    Thread-safe via WAL mode; each call opens a short-lived connection.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("data/applications.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Schema ─────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Companies ──────────────────────────────────────────────────────────────

    def _get_or_create_company(
        self,
        conn: sqlite3.Connection,
        name: str,
        website: str = "",
        industry: str = "",
        location: str = "",
    ) -> int:
        row = conn.execute(
            "SELECT id FROM companies WHERE name=?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO companies (name, website, industry, hq_location) VALUES (?,?,?,?)",
            (name, website, industry, location),
        )
        return cur.lastrowid

    # ── Applications ───────────────────────────────────────────────────────────

    def add_application(
        self,
        job,                           # JobListing from apify_scraper
        match_score: int = 0,
        resume_pdf_path: Optional[Path] = None,
        resume_docx_path: Optional[Path] = None,
        cover_letter_path: Optional[Path] = None,
        drive_links: Optional[dict] = None,
        ats_keywords: Optional[list] = None,
    ) -> int:
        """
        Record a prepared application from a JobListing, not a submission.
        Returns the new application ID.
        """
        import json as _json

        drive_links = drive_links or {}
        with self._conn() as conn:
            company_id = self._get_or_create_company(
                conn,
                name=job.company or "Unknown",
                location=job.location or "",
            )
            # Insert application
            cur = conn.execute(
                """
                INSERT INTO applications
                    (company_id, job_title, job_url, job_board, location,
                     remote, salary_min, salary_max, job_type, match_score,
                     status, applied_at, description_snippet, sponsorship_required)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    company_id,
                    job.title or "",
                    getattr(job, "apply_url", "") or getattr(job, "url", "") or "",
                    getattr(job, "source", "") or "",
                    job.location or "",
                    1 if (job.location or "").lower() in ("remote", "anywhere") else 0,
                    getattr(job, "salary_min", None),
                    getattr(job, "salary_max", None),
                    getattr(job, "job_type", None),
                    match_score,
                    "prepared",
                    None,
                    (job.description or "")[:500],
                    0,
                ),
            )
            app_id = cur.lastrowid

            # Documents
            kw_json = _json.dumps(ats_keywords or [])
            if resume_pdf_path:
                conn.execute(
                    "INSERT INTO documents (application_id,doc_type,local_path,drive_url,ats_keywords) VALUES (?,?,?,?,?)",
                    (app_id, "resume_pdf", str(resume_pdf_path),
                     drive_links.get("Resume PDF", ""), kw_json),
                )
            if resume_docx_path:
                conn.execute(
                    "INSERT INTO documents (application_id,doc_type,local_path,drive_url) VALUES (?,?,?,?)",
                    (app_id, "resume_docx", str(resume_docx_path),
                     drive_links.get("Resume DOCX", "")),
                )
            if cover_letter_path:
                conn.execute(
                    "INSERT INTO documents (application_id,doc_type,local_path,drive_url) VALUES (?,?,?,?)",
                    (app_id, "cover_letter", str(cover_letter_path),
                     drive_links.get("Cover Letter", "")),
                )

        logger.info("Tracked application #%d: %s @ %s (score=%d)",
                    app_id, job.title, job.company, match_score)
        return app_id

    def update_status(self, app_id: int, status: str, note: Optional[str] = None) -> None:
        with self._conn() as conn:
            if status == "applied":
                row = conn.execute("SELECT applied_at FROM applications WHERE id=?", (app_id,)).fetchone()
                if row is not None and row["applied_at"] is None:
                    conn.execute("UPDATE applications SET applied_at=? WHERE id=?",
                                 (datetime.now().isoformat(), app_id))
                    conn.execute(
                        "INSERT INTO follow_ups (application_id,action,due_at) VALUES (?,?,datetime('now','+7 days'))",
                        (app_id, "follow_up_email"),
                    )
            conn.execute(
                "UPDATE applications SET status=?, updated_at=datetime('now') WHERE id=?",
                (status, app_id),
            )
            if note:
                conn.execute(
                    "INSERT INTO notes (application_id,content,source) VALUES (?,?,'system')",
                    (app_id, f"Status changed to '{status}': {note}"),
                )

    # ── Contacts ───────────────────────────────────────────────────────────────

    def add_contact(
        self,
        app_id: int,
        name: str = "",
        title: str = "",
        email: str = "",
        linkedin_url: str = "",
        role: str = "recruiter",
        notes: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO contacts (application_id,name,title,email,linkedin_url,role,notes) VALUES (?,?,?,?,?,?,?)",
                (app_id, name, title, email, linkedin_url, role, notes),
            )
            return cur.lastrowid

    # ── Interview stages ───────────────────────────────────────────────────────

    def add_stage(
        self,
        app_id: int,
        stage_name: str,
        scheduled_at: Optional[str] = None,
        interviewer: str = "",
        notes: str = "",
        prep_tips: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO interview_stages
                   (application_id,stage_name,scheduled_at,interviewer,notes,prep_tips,outcome)
                   VALUES (?,?,?,?,?,?,'pending')""",
                (app_id, stage_name, scheduled_at, interviewer, notes, prep_tips),
            )
            # Update application status to reflect current stage
            conn.execute(
                "UPDATE applications SET status=?, updated_at=datetime('now') WHERE id=?",
                (stage_name, app_id),
            )
            return cur.lastrowid

    def complete_stage(
        self, stage_id: int, outcome: str, notes: str = ""
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE interview_stages SET outcome=?, completed_at=datetime('now'), notes=? WHERE id=?",
                (outcome, notes, stage_id),
            )

    # ── Notes ──────────────────────────────────────────────────────────────────

    def add_note(
        self, app_id: int, content: str, source: str = "manual"
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO notes (application_id,content,source) VALUES (?,?,?)",
                (app_id, content, source),
            )
            return cur.lastrowid

    # ── Follow-ups ─────────────────────────────────────────────────────────────

    def add_follow_up(
        self,
        app_id: int,
        action: str,
        due_at: Optional[str] = None,
        notes: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO follow_ups (application_id,action,due_at,notes) VALUES (?,?,?,?)",
                (app_id, action, due_at, notes),
            )
            return cur.lastrowid

    def complete_follow_up(self, follow_up_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE follow_ups SET done=1, completed_at=datetime('now') WHERE id=?",
                (follow_up_id,),
            )

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_application(self, app_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT a.*, c.name as company_name
                   FROM applications a
                   LEFT JOIN companies c ON c.id=a.company_id
                   WHERE a.id=?""",
                (app_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_applications(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    """SELECT a.*, c.name as company_name
                       FROM applications a
                       LEFT JOIN companies c ON c.id=a.company_id
                       WHERE a.status=?
                       ORDER BY a.applied_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT a.*, c.name as company_name
                       FROM applications a
                       LEFT JOIN companies c ON c.id=a.company_id
                       ORDER BY a.applied_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def pending_follow_ups(self, days_ahead: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.*, a.job_title, c.name as company_name
                   FROM follow_ups f
                   JOIN applications a ON a.id=f.application_id
                   LEFT JOIN companies c ON c.id=a.company_id
                   WHERE f.done=0
                   AND (f.due_at IS NULL OR f.due_at <= datetime('now', ?))
                   ORDER BY f.due_at ASC""",
                (f"+{days_ahead} days",),
            ).fetchall()
            return [dict(r) for r in rows]

    def summary_stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                       SUM(CASE WHEN applied_at IS NOT NULL THEN 1 ELSE 0 END) as total,
                       SUM(CASE WHEN status='prepared' THEN 1 ELSE 0 END) as prepared,
                       SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                       SUM(CASE WHEN status IN ('phone_screen','technical','onsite') THEN 1 ELSE 0 END) as in_progress,
                       SUM(CASE WHEN status='offer' THEN 1 ELSE 0 END) as offers,
                       SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected,
                       AVG(match_score) as avg_score
                   FROM applications"""
            ).fetchone()
            return dict(row) if row else {}
