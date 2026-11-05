"""
Portal Credential Store
-----------------------
Fernet-encrypted SQLite store for job portal login credentials.
Passwords are encrypted at rest; never returned in list calls.
Key file: data/.portal_key (auto-created, mode 0o600).

Usage:
    store = PortalStore()
    id = store.add_portal("LinkedIn", "https://linkedin.com/login",
                          "me@example.com", "s3cr3t", platform="linkedin")
    username, password = store.get_credentials(id)
"""

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS portals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    url           TEXT NOT NULL,
    username      TEXT NOT NULL,
    password_enc  TEXT NOT NULL,
    platform      TEXT,
    notes         TEXT,
    last_used     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_portals_name ON portals(name);
CREATE INDEX IF NOT EXISTS idx_portals_platform ON portals(platform);
"""


def _get_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes().strip()
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    logger.info("Generated new portal encryption key at %s", key_path)
    return key


class PortalStore:
    """Thread-safe portal credential store with Fernet encryption."""

    def __init__(self,
                 db_path: Optional[Path] = None,
                 key_path: Optional[Path] = None):
        self.db_path = db_path or Path("data/applications.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path = key_path or Path("data/.portal_key")
        self._fernet = None  # lazy
        self._init_schema()

    def _get_fernet(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet
            key = _get_or_create_key(self._key_path)
            self._fernet = Fernet(key)
        return self._fernet

    def _encrypt(self, plaintext: str) -> str:
        return self._get_fernet().encrypt(plaintext.encode()).decode()

    def _decrypt(self, token: str) -> str:
        return self._get_fernet().decrypt(token.encode()).decode()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def list_portals(self) -> list[dict]:
        """Return all portals WITHOUT password_enc."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, url, username, platform, notes, "
                "last_used, created_at, updated_at FROM portals ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_portal(self, name: str, url: str, username: str, password: str,
                   platform: Optional[str] = None,
                   notes: Optional[str] = None) -> int:
        enc = self._encrypt(password)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO portals (name, url, username, password_enc, platform, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, url, username, enc, platform, notes)
            )
            return cur.lastrowid

    def update_portal(self, portal_id: int, *,
                      name: Optional[str] = None,
                      url: Optional[str] = None,
                      username: Optional[str] = None,
                      password: Optional[str] = None,
                      notes: Optional[str] = None) -> None:
        fields, vals = [], []
        if name     is not None: fields.append("name=?");         vals.append(name)
        if url      is not None: fields.append("url=?");          vals.append(url)
        if username is not None: fields.append("username=?");     vals.append(username)
        if password is not None:
            fields.append("password_enc=?")
            vals.append(self._encrypt(password))
        if notes    is not None: fields.append("notes=?");        vals.append(notes)
        if not fields:
            return
        fields.append("updated_at=datetime('now')")
        vals.append(portal_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE portals SET {', '.join(fields)} WHERE id=?", vals
            )

    def delete_portal(self, portal_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM portals WHERE id=?", (portal_id,))

    def get_credentials(self, portal_id: int) -> tuple[str, str]:
        """Return (username, plaintext_password). Only call in apply flow."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT username, password_enc FROM portals WHERE id=?",
                (portal_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Portal {portal_id} not found")
        return row["username"], self._decrypt(row["password_enc"])

    def get_portal(self, portal_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, url, username, platform, notes, last_used "
                "FROM portals WHERE id=?", (portal_id,)
            ).fetchone()
        return dict(row) if row else None

    def touch_last_used(self, portal_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE portals SET last_used=datetime('now') WHERE id=?",
                (portal_id,)
            )

    def get_creds_as_env_dict(self, portal_id: int) -> dict:
        """Return {PLATFORM_EMAIL: ..., PLATFORM_PASSWORD: ..., APPLY_EMAIL: ..., APPLY_PASSWORD: ...}
        compatible with auto_apply._load_credentials() format."""
        portal = self.get_portal(portal_id)
        if not portal:
            return {}
        username, password = self.get_credentials(portal_id)
        prefix = (portal.get("platform") or "apply").upper().replace("-", "_")
        return {
            f"{prefix}_EMAIL":    username,
            f"{prefix}_PASSWORD": password,
            "APPLY_EMAIL":        username,
            "APPLY_PASSWORD":     password,
        }

    def get_default_credentials(self) -> Optional[tuple[str, str]]:
        """Return (email, password) for the universal 'default' portal entry, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM portals WHERE platform='default' LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return self.get_credentials(row["id"])

    def find_by_platform(self, platform: str) -> Optional[dict]:
        """Return first portal matching the given platform string."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, url, username, platform FROM portals "
                "WHERE platform=? ORDER BY last_used DESC NULLS LAST LIMIT 1",
                (platform,)
            ).fetchone()
        return dict(row) if row else None
