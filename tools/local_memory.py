"""
Local project memory for Claude Code
------------------------------------
A small SQLite full-text store, so a session can look up one fact instead of
re-reading source files. Nothing leaves this machine.

    python tools/local_memory.py serve                     # MCP server (stdio)
    python tools/local_memory.py save <key> "<content>"
    python tools/local_memory.py search "<query>"
    python tools/local_memory.py list
    python tools/local_memory.py delete <key>

Saving the same key again replaces the entry. Content that looks like a
credential is refused, because the database is a plain unencrypted file.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / ".claude" / "local_memory.db"
MAX_CONTENT_CHARS = 2000
DEFAULT_LIMIT = 5
MAX_LIMIT = 20

_SECRET_RE = re.compile(
    r"apify_api_\w{10,}|sk-ant-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_\w{20,}"
    r"|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|hooks\.slack\.com/services/\S+|discord(?:app)?\.com/api/webhooks/\S+"
)


class MemoryStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5("
                "key, content, updated UNINDEXED, tokenize='porter unicode61')"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save(self, key: str, content: str) -> str:
        key, content = key.strip(), content.strip()
        if not key or not content:
            raise ValueError("key and content are both required")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(
                f"content is {len(content)} chars; the limit is {MAX_CONTENT_CHARS}. "
                "Split it into smaller entries."
            )
        if _SECRET_RE.search(key) or _SECRET_RE.search(content):
            raise ValueError("refused: this looks like a credential. Store a pointer to it instead.")
        with self._connect() as conn:
            existed = conn.execute("DELETE FROM memories WHERE key = ?", (key,)).rowcount > 0
            conn.execute(
                "INSERT INTO memories (key, content, updated) VALUES (?, ?, ?)",
                (key, content, date.today().isoformat()),
            )
        return f"{'updated' if existed else 'saved'}: {key}"

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Best matches first. Any query word may match, and word stems count."""
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        limit = max(1, min(limit, MAX_LIMIT))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, content, updated FROM memories WHERE memories MATCH ? "
                "ORDER BY bm25(memories, 5.0, 1.0) LIMIT ?",
                (match, limit),
            ).fetchall()
        return [{"key": k, "content": c, "updated": u} for k, c, u in rows]

    def list_keys(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, updated FROM memories ORDER BY key").fetchall()
        return [{"key": k, "updated": u} for k, u in rows]

    def delete(self, key: str) -> str:
        with self._connect() as conn:
            found = conn.execute("DELETE FROM memories WHERE key = ?", (key.strip(),)).rowcount > 0
        return f"deleted: {key}" if found else f"not found: {key}"


def format_results(rows: list[dict]) -> str:
    if not rows:
        return "No matches."
    return "\n\n".join(f"[{r['key']}] (updated {r['updated']})\n{r['content']}" for r in rows)


def build_server(store: MemoryStore):
    from fastmcp import FastMCP

    mcp = FastMCP("local-memory")

    @mcp.tool
    def memory_search(query: str, limit: int = DEFAULT_LIMIT) -> str:
        """Search saved project facts. Try this before re-reading source files, and
        verify anything you plan to rely on, since entries can go stale."""
        return format_results(store.search(query, limit))

    @mcp.tool
    def memory_save(key: str, content: str) -> str:
        """Save one short fact under a kebab-case key, replacing any entry with that key.
        Never store secrets."""
        return store.save(key, content)

    @mcp.tool
    def memory_list() -> str:
        """List saved keys and when each was last updated."""
        rows = store.list_keys()
        return "\n".join(f"{r['key']} ({r['updated']})" for r in rows) or "Empty."

    @mcp.tool
    def memory_delete(key: str) -> str:
        """Delete an entry that is wrong or no longer true."""
        return store.delete(key)

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local project memory")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    p_save = sub.add_parser("save")
    p_save.add_argument("key")
    p_save.add_argument("content")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    sub.add_parser("list")
    p_delete = sub.add_parser("delete")
    p_delete.add_argument("key")
    args = parser.parse_args(argv)

    store = MemoryStore(args.db)
    if args.command == "serve":
        build_server(store).run(show_banner=False)
    elif args.command == "save":
        try:
            print(store.save(args.key, args.content))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    elif args.command == "search":
        print(format_results(store.search(args.query, args.limit)))
    elif args.command == "list":
        for r in store.list_keys():
            print(f"{r['key']} ({r['updated']})")
    elif args.command == "delete":
        print(store.delete(args.key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
