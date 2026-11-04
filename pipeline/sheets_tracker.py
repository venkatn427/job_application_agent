"""
Google Sheets Application Tracker
-----------------------------------
Maintains a single Google Spreadsheet with one row per processed job:

  Company | Job Title | Job URL | Match Score | Resume Link |
  Application Status | Date Processed | Source | Missing Skills |
  Notes

Falls back gracefully to a local CSV when Sheets isn't configured.

Requires: pip install google-api-python-client google-auth
Set GOOGLE_SHEETS_SPREADSHEET_ID in .env (create a blank sheet and paste its ID).
"""

import os
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
SHEET_NAME = "Applications"
HEADERS = [
    "Company",
    "Job Title",
    "Job URL",
    "Match Score",
    "Resume Link",
    "Application Status",
    "Date Processed",
    "Source",
    "Missing Skills",
    "Interview Topics",
    "Notes",
]


@dataclass
class ApplicationRow:
    company: str
    job_title: str
    job_url: str = ""
    match_score: int = 0
    resume_link: str = ""
    status: str = "Pending"
    date_processed: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    source: str = ""
    missing_skills: str = ""
    interview_topics: str = ""
    notes: str = ""

    def to_row(self) -> list:
        return [
            self.company,
            self.job_title,
            self.job_url,
            str(self.match_score),
            self.resume_link,
            self.status,
            self.date_processed,
            self.source,
            self.missing_skills,
            self.interview_topics,
            self.notes,
        ]


class SheetsTracker:
    """
    Writes application rows to Google Sheets (primary) and a local CSV (always).
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
        local_csv_path: Optional[Path] = None,
    ):
        self.credentials_path = (
            credentials_path
            or os.environ.get("GOOGLE_DRIVE_CREDENTIALS_PATH", "")
        )
        self.spreadsheet_id = (
            spreadsheet_id
            or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
        )
        self.local_csv = local_csv_path or Path("logs/applications.csv")
        self._service = None
        self._headers_written = False

    # ── Service ────────────────────────────────────────────────────────────────

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            logger.error("google-api-python-client not installed")
            raise

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=SCOPES
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    def is_sheets_available(self) -> bool:
        return bool(
            self.credentials_path
            and Path(self.credentials_path).exists()
            and self.spreadsheet_id
        )

    # ── Sheets helpers ─────────────────────────────────────────────────────────

    def _ensure_headers(self) -> None:
        """Write header row if the sheet is empty."""
        if self._headers_written:
            return
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{SHEET_NAME}!A1:Z1")
            .execute()
        )
        if not result.get("values"):
            self._append_rows([HEADERS])
            logger.info("Wrote header row to Sheets")
        self._headers_written = True

    def _append_rows(self, rows: list[list]) -> None:
        body = {"values": rows}
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

    def _find_row(self, company: str, job_title: str) -> Optional[int]:
        """Return 1-based row index of an existing entry, or None."""
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{SHEET_NAME}!A:B",
            )
            .execute()
        )
        rows = result.get("values", [])
        for i, row in enumerate(rows):
            if len(row) >= 2 and row[0] == company and row[1] == job_title:
                return i + 1  # 1-based
        return None

    def _update_row(self, row_index: int, row_data: list) -> None:
        range_name = f"{SHEET_NAME}!A{row_index}:{chr(ord('A') + len(row_data) - 1)}{row_index}"
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            body={"values": [row_data]},
        ).execute()

    # ── CSV fallback ────────────────────────────────────────────────────────────

    def _write_csv(self, row: ApplicationRow) -> None:
        self.local_csv.parent.mkdir(parents=True, exist_ok=True)
        key = f"{row.company}|{row.job_title}"

        # Read existing rows to decide whether to insert or update
        existing_rows: list[list] = []
        if self.local_csv.exists():
            with open(self.local_csv, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                existing_rows = list(reader)

        # Find the row index (skip header at index 0)
        update_idx: int | None = None
        for i, r in enumerate(existing_rows):
            if i == 0:
                continue
            if len(r) >= 2 and f"{r[0]}|{r[1]}" == key:
                update_idx = i
                break

        if update_idx is not None:
            existing_rows[update_idx] = row.to_row()
            with open(self.local_csv, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(existing_rows)
        else:
            write_header = not existing_rows
            with open(self.local_csv, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(HEADERS)
                writer.writerow(row.to_row())

    # ── Public API ──────────────────────────────────────────────────────────────

    def upsert(self, row: ApplicationRow) -> None:
        """Insert or update an application row in Sheets + CSV."""
        # Always write CSV
        self._write_csv(row)

        if not self.is_sheets_available():
            logger.debug("Google Sheets not configured — CSV only")
            return

        try:
            self._ensure_headers()
            existing = self._find_row(row.company, row.job_title)
            if existing:
                self._update_row(existing, row.to_row())
                logger.info("Updated Sheets row for %r @ %r", row.job_title, row.company)
            else:
                self._append_rows([row.to_row()])
                logger.info("Added Sheets row for %r @ %r", row.job_title, row.company)
        except Exception as e:
            logger.error("Sheets upsert failed: %s", e)

    def update_status(self, company: str, job_title: str, status: str) -> None:
        """Update only the status column for a specific job."""
        if not self.is_sheets_available():
            return
        try:
            row_idx = self._find_row(company, job_title)
            if row_idx:
                # Status is column F (index 5, 1-based = column 6)
                range_name = f"{SHEET_NAME}!F{row_idx}"
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body={"values": [[status]]},
                ).execute()
                logger.info("Status updated for %r @ %r → %s", job_title, company, status)
        except Exception as e:
            logger.error("Status update failed: %s", e)

    def get_processed_ids(self) -> set[str]:
        """Return set of 'company|title' pairs already in the tracker."""
        ids: set[str] = set()
        # Check local CSV
        if self.local_csv.exists():
            with open(self.local_csv, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = f"{row.get('Company', '')}|{row.get('Job Title', '')}"
                    ids.add(key)
        return ids

    def get_spreadsheet_url(self) -> Optional[str]:
        if self.spreadsheet_id:
            return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        return None
