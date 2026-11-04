"""
MCP Server — Job Application Store
------------------------------------
Exposes tools over the Model Context Protocol (fastMCP) so that any
MCP-compatible LLM client (Claude Desktop, Claude Code, etc.) can:

  • store_job_application  — insert / upsert a scraped job into SQLite
  • query_jobs             — run a natural-language-style structured query
  • get_job_by_id          — fetch one record by primary key
  • list_sources           — return distinct job sources in the DB

Run:
    python server.py                      # stdio transport (default)
    python server.py --transport sse      # SSE transport on :8000

Install deps:
    pip install fastmcp pydantic
"""

import sqlite3
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("JOB_DB_PATH", "jobs.db")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS job_applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    location        TEXT,
    job_type        TEXT,
    salary_min      INTEGER,
    salary_max      INTEGER,
    salary_raw      TEXT,
    description     TEXT,
    requirements    TEXT,   -- JSON array stored as text
    nice_to_have    TEXT,   -- JSON array stored as text
    apply_url       TEXT,
    posted_date     TEXT,
    company_about   TEXT,
    contact_email   TEXT,
    source          TEXT,
    scraped_at      TEXT    NOT NULL,
    UNIQUE(title, company, apply_url)   -- prevent duplicate inserts
);

CREATE INDEX IF NOT EXISTS idx_title    ON job_applications(title);
CREATE INDEX IF NOT EXISTS idx_company  ON job_applications(company);
CREATE INDEX IF NOT EXISTS idx_location ON job_applications(location);
CREATE INDEX IF NOT EXISTS idx_salary   ON job_applications(salary_min, salary_max);
"""


@contextmanager
def get_db():
    """Yield a SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(DDL)


# ---------------------------------------------------------------------------
# Schema / validation
# ---------------------------------------------------------------------------

SALARY_RE = re.compile(r"\$?([\d,]+)k?", re.IGNORECASE)


def _parse_salary(raw: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """
    Parse a salary string like '$150k-$200k', '150000-200000', '$180k'
    into (min_usd, max_usd) integer tuple.  Returns (None, None) on failure.
    """
    if not raw:
        return None, None
    matches = SALARY_RE.findall(raw.replace(",", ""))
    values = []
    for m in matches:
        val = int(m)
        if val < 1000:          # e.g. "150" really means 150 000
            val *= 1000
        values.append(val)
    if len(values) == 0:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    return min(values), max(values)


class JobApplication(BaseModel):
    """Schema for a single job posting coming into the MCP tool."""

    title:          str                 = Field(...,  description="Job title")
    company:        str                 = Field(...,  description="Company name")
    location:       Optional[str]       = Field(None, description="City/state/country or 'Remote'")
    job_type:       Optional[str]       = Field(None, description="full-time | part-time | contract | remote")
    salary_range:   Optional[str]       = Field(None, description="Raw salary string, e.g. '$150k-$200k'")
    description:    Optional[str]       = Field(None, description="Full job description")
    requirements:   Optional[list[str]] = Field(None, description="Required skills / qualifications")
    nice_to_have:   Optional[list[str]] = Field(None, description="Preferred skills")
    apply_url:      Optional[str]       = Field(None, description="Application or source URL")
    posted_date:    Optional[str]       = Field(None, description="ISO date string or human-readable date")
    company_about:  Optional[str]       = Field(None, description="Short company description")
    contact_email:  Optional[str]       = Field(None, description="HR / hiring manager email")
    source:         Optional[str]       = Field(None, description="Which scraper/board produced this record")

    @field_validator("title", "company", mode="before")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = str(v).strip()
        if not v:
            raise ValueError("Field must not be empty")
        return v

    @field_validator("job_type", mode="before")
    @classmethod
    def normalise_job_type(cls, v):
        if v is None:
            return v
        v = str(v).lower().strip()
        mapping = {
            "fulltime": "full-time", "full_time": "full-time",
            "parttime": "part-time", "part_time": "part-time",
            "contractor": "contract",
        }
        return mapping.get(v, v)


class QueryFilters(BaseModel):
    """Filters accepted by the query_jobs tool."""

    title_contains:     Optional[str]   = Field(None, description="Substring match on job title (case-insensitive)")
    company:            Optional[str]   = Field(None, description="Exact or partial company name")
    location_contains:  Optional[str]   = Field(None, description="Substring match on location")
    job_type:           Optional[str]   = Field(None, description="full-time | part-time | contract | remote")
    min_salary:         Optional[int]   = Field(None, description="Minimum salary (USD/year)")
    max_salary:         Optional[int]   = Field(None, description="Maximum salary (USD/year)")
    keyword:            Optional[str]   = Field(None, description="Search in description + requirements")
    limit:              int             = Field(20,   description="Max rows to return (1-100)", ge=1, le=100)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="job-application-store",
    instructions=(
        "Use store_job_application to persist scraped job postings. "
        "Use query_jobs to filter and retrieve them. "
        "All salaries are stored as annual USD integers."
    ),
)


@mcp.tool()
def store_job_application(job: JobApplication) -> dict:
    """
    Insert or update a single job posting in the SQLite database.

    Returns a dict with 'status' ('inserted' | 'updated' | 'unchanged'),
    the record 'id', and any validation warnings.
    """
    salary_min, salary_max = _parse_salary(job.salary_range)

    row = {
        "title":         job.title,
        "company":       job.company,
        "location":      job.location,
        "job_type":      job.job_type,
        "salary_min":    salary_min,
        "salary_max":    salary_max,
        "salary_raw":    job.salary_range,
        "description":   job.description,
        "requirements":  json.dumps(job.requirements) if job.requirements else None,
        "nice_to_have":  json.dumps(job.nice_to_have) if job.nice_to_have else None,
        "apply_url":     job.apply_url,
        "posted_date":   job.posted_date,
        "company_about": job.company_about,
        "contact_email": job.contact_email,
        "source":        job.source,
        "scraped_at":    datetime.now(timezone.utc).isoformat(),
    }

    upsert_sql = """
        INSERT INTO job_applications
            (title, company, location, job_type, salary_min, salary_max,
             salary_raw, description, requirements, nice_to_have, apply_url,
             posted_date, company_about, contact_email, source, scraped_at)
        VALUES
            (:title, :company, :location, :job_type, :salary_min, :salary_max,
             :salary_raw, :description, :requirements, :nice_to_have, :apply_url,
             :posted_date, :company_about, :contact_email, :source, :scraped_at)
        ON CONFLICT(title, company, apply_url) DO UPDATE SET
            location      = excluded.location,
            job_type      = excluded.job_type,
            salary_min    = excluded.salary_min,
            salary_max    = excluded.salary_max,
            salary_raw    = excluded.salary_raw,
            description   = excluded.description,
            requirements  = excluded.requirements,
            nice_to_have  = excluded.nice_to_have,
            posted_date   = excluded.posted_date,
            company_about = excluded.company_about,
            contact_email = excluded.contact_email,
            source        = excluded.source,
            scraped_at    = excluded.scraped_at
    """

    with get_db() as conn:
        cursor = conn.execute(upsert_sql, row)
        record_id = cursor.lastrowid

        # SQLite sets lastrowid to the existing row's id on no-change conflicts
        status = "inserted" if cursor.rowcount == 1 else "updated"

    warnings = []
    if salary_min is None and job.salary_range:
        warnings.append(f"Could not parse salary from '{job.salary_range}'")

    return {"status": status, "id": record_id, "warnings": warnings}


@mcp.tool()
def query_jobs(filters: QueryFilters) -> list[dict]:
    """
    Query stored job applications with optional filters.

    Example natural-language equivalents handled by the agent:
      "Show me all Remote Senior Data Engineer roles paying over $150k"
        → title_contains="Data Engineer", location_contains="Remote", min_salary=150000

      "Find contract Python jobs at startups"
        → keyword="Python", job_type="contract"
    """
    conditions: list[str] = []
    params: list = []

    if filters.title_contains:
        conditions.append("LOWER(title) LIKE ?")
        params.append(f"%{filters.title_contains.lower()}%")

    if filters.company:
        conditions.append("LOWER(company) LIKE ?")
        params.append(f"%{filters.company.lower()}%")

    if filters.location_contains:
        conditions.append("LOWER(location) LIKE ?")
        params.append(f"%{filters.location_contains.lower()}%")

    if filters.job_type:
        conditions.append("LOWER(job_type) = ?")
        params.append(filters.job_type.lower())

    if filters.min_salary is not None:
        conditions.append("(salary_max IS NOT NULL AND salary_max >= ?)")
        params.append(filters.min_salary)

    if filters.max_salary is not None:
        conditions.append("(salary_min IS NOT NULL AND salary_min <= ?)")
        params.append(filters.max_salary)

    if filters.keyword:
        kw = f"%{filters.keyword.lower()}%"
        conditions.append(
            "(LOWER(description) LIKE ? OR LOWER(requirements) LIKE ? OR LOWER(title) LIKE ?)"
        )
        params.extend([kw, kw, kw])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, title, company, location, job_type,
               salary_min, salary_max, salary_raw,
               apply_url, posted_date, source, scraped_at
        FROM job_applications
        {where}
        ORDER BY scraped_at DESC
        LIMIT ?
    """
    params.append(filters.limit)

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(r) for r in rows]


@mcp.tool()
def get_job_by_id(job_id: int) -> Optional[dict]:
    """Fetch a single job posting by its database ID (includes full description)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM job_applications WHERE id = ?", (job_id,)
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    for field in ("requirements", "nice_to_have"):
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except json.JSONDecodeError:
                pass
    return result


@mcp.tool()
def list_sources() -> list[str]:
    """Return all distinct job sources (boards / companies) in the database."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM job_applications WHERE source IS NOT NULL ORDER BY source"
        ).fetchall()
    return [r["source"] for r in rows]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Job Application MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode (default: stdio for Claude Desktop / claude-code)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE transport")
    args = parser.parse_args()

    print(f"[server] Initialising database at {DB_PATH!r}", flush=True)
    init_db()

    if args.transport == "sse":
        print(f"[server] Starting SSE transport on port {args.port}", flush=True)
        mcp.run(transport="sse", port=args.port)
    else:
        print("[server] Starting stdio transport", flush=True)
        mcp.run(transport="stdio")
