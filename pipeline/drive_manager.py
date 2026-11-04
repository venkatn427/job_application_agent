"""
Google Drive Manager
---------------------
Stores all job application outputs in a structured Google Drive folder tree:

  /Job Applications/
    /Company Name — Job Title — YYYY-MM-DD/
        Job Description.txt
        Tailored Resume.pdf
        Tailored Resume.docx
        Cover Letter.docx
        Match Analysis.txt

Requires a Google service account (recommended) or OAuth2 credentials.
Set GOOGLE_DRIVE_CREDENTIALS_PATH in your .env to point to the JSON key file.
Optionally set GOOGLE_DRIVE_ROOT_FOLDER_ID to target a specific Drive folder.

Install: pip install google-api-python-client google-auth
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]
ROOT_FOLDER_NAME = "Job Applications"


class DriveManager:
    """
    Manages Google Drive uploads for job application documents.
    Lazy-initialises the Drive API client so it doesn't fail on import
    when credentials are missing.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        root_folder_id: Optional[str] = None,
    ):
        self.credentials_path = (
            credentials_path
            or os.environ.get("GOOGLE_DRIVE_CREDENTIALS_PATH", "")
        )
        self.root_folder_id = (
            root_folder_id
            or os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
        )
        self._service = None
        self._root_id: Optional[str] = None   # cached root folder ID

    # ── Drive service ──────────────────────────────────────────────────────────

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
            logger.error(
                "Google API client not installed. "
                "Run: pip install google-api-python-client google-auth"
            )
            raise

        if not self.credentials_path or not Path(self.credentials_path).exists():
            raise FileNotFoundError(
                f"Google credentials not found at: {self.credentials_path!r}. "
                "Set GOOGLE_DRIVE_CREDENTIALS_PATH in your .env file."
            )

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def is_available(self) -> bool:
        """Check if credentials are configured (without connecting)."""
        return bool(
            self.credentials_path and Path(self.credentials_path).exists()
        )

    # ── Folder management ──────────────────────────────────────────────────────

    def _get_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Return folder ID, creating it if it doesn't exist."""
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name='{escaped}' and mimeType='application/vnd.google-apps.folder' "
            "and trashed=false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = (
            self.service.files()
            .list(q=query, fields="files(id, name)", spaces="drive")
            .execute()
        )
        files = results.get("files", [])
        if files:
            return files[0]["id"]

        # Create folder
        meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            meta["parents"] = [parent_id]

        folder = self.service.files().create(body=meta, fields="id").execute()
        folder_id = folder.get("id")
        logger.info("Created Drive folder: %r → %s", name, folder_id)
        return folder_id

    def _ensure_root(self) -> str:
        """Get/create the top-level 'Job Applications' folder."""
        if self._root_id:
            return self._root_id
        if self.root_folder_id:
            self._root_id = self.root_folder_id
        else:
            self._root_id = self._get_or_create_folder(ROOT_FOLDER_NAME)
        return self._root_id

    def _job_folder(self, company: str, title: str) -> str:
        """Get/create the per-job subfolder."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_company = company.replace("/", "-").replace("\\", "-")[:40]
        safe_title = title.replace("/", "-").replace("\\", "-")[:40]
        folder_name = f"{safe_company} — {safe_title} — {date_str}"
        root_id = self._ensure_root()
        return self._get_or_create_folder(folder_name, parent_id=root_id)

    # ── File upload ────────────────────────────────────────────────────────────

    def _upload_file(
        self,
        local_path: Path,
        drive_filename: str,
        parent_id: str,
        mime_type: Optional[str] = None,
    ) -> str:
        """Upload a local file and return its Drive URL."""
        from googleapiclient.http import MediaFileUpload

        _mime_map = {
            ".pdf":  "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt":  "text/plain",
            ".json": "application/json",
        }
        if mime_type is None:
            mime_type = _mime_map.get(local_path.suffix.lower(), "application/octet-stream")

        file_meta = {"name": drive_filename, "parents": [parent_id]}
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)

        file_obj = (
            self.service.files()
            .create(body=file_meta, media_body=media, fields="id, webViewLink")
            .execute()
        )
        url = file_obj.get("webViewLink", "")
        logger.info("Uploaded %r → %s", drive_filename, url)
        return url

    def _upload_text(self, content: str, drive_filename: str, parent_id: str) -> str:
        """Upload a plain text string as a Drive file."""
        import io
        from googleapiclient.http import MediaIoBaseUpload

        file_meta = {"name": drive_filename, "parents": [parent_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype="text/plain"
        )
        file_obj = (
            self.service.files()
            .create(body=file_meta, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return file_obj.get("webViewLink", "")

    # ── Public API ─────────────────────────────────────────────────────────────

    def upload_application_package(
        self,
        company: str,
        job_title: str,
        job_description: str,
        resume_pdf_path: Optional[Path] = None,
        resume_docx_path: Optional[Path] = None,
        cover_letter_docx_path: Optional[Path] = None,
        match_analysis_text: Optional[str] = None,
    ) -> dict[str, str]:
        """
        Upload all files for a job application to a new Drive subfolder.
        Returns a dict mapping file name → Drive view URL.
        """
        if not self.is_available():
            logger.warning("Google Drive not configured — skipping upload")
            return {}

        links: dict[str, str] = {}
        folder_id = self._job_folder(company, job_title)

        # Job description
        if job_description:
            url = self._upload_text(job_description, "Job Description.txt", folder_id)
            links["Job Description"] = url

        # Resume PDF
        if resume_pdf_path and resume_pdf_path.exists():
            url = self._upload_file(resume_pdf_path, "Tailored Resume.pdf", folder_id)
            links["Resume PDF"] = url

        # Resume DOCX
        if resume_docx_path and resume_docx_path.exists():
            url = self._upload_file(resume_docx_path, "Tailored Resume.docx", folder_id)
            links["Resume DOCX"] = url

        # Cover letter
        if cover_letter_docx_path and cover_letter_docx_path.exists():
            url = self._upload_file(cover_letter_docx_path, "Cover Letter.docx", folder_id)
            links["Cover Letter"] = url

        # Match analysis
        if match_analysis_text:
            url = self._upload_text(match_analysis_text, "Match Analysis.txt", folder_id)
            links["Match Analysis"] = url

        logger.info(
            "Uploaded %d files for %r @ %r",
            len(links), job_title, company,
        )
        return links

    def get_folder_url(self, company: str, job_title: str) -> Optional[str]:
        """Return the Drive folder URL for a job (if it exists)."""
        try:
            folder_id = self._job_folder(company, job_title)
            return f"https://drive.google.com/drive/folders/{folder_id}"
        except Exception as e:
            logger.warning("Could not get folder URL: %s", e)
            return None
