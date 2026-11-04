"""
Job Search — LinkedIn-style full-page UI
"""
import json, sqlite3, sys, os, subprocess, threading, time, html as _html
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
DB_PATH    = str(ROOT / os.environ.get("JOB_DB_PATH", "jobs.db"))
_VENV_PY   = ROOT / ".venv" / "bin" / "python"
PYTHON_EXE = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

# ── User profile (for match scoring) ─────────────────────────────────────────
try:
    from config import USER_PROFILE, JOB_CRITERIA
    USER_SKILLS = {s.lower() for s in USER_PROFILE.get("skills", [])}
    USER_TITLES = [t.lower() for t in JOB_CRITERIA.get("job_titles", [])]
    USER_NAME   = USER_PROFILE.get("name", "You")
except Exception:
    USER_SKILLS, USER_TITLES, USER_NAME = set(), [], "You"

# ── Scraper registry ──────────────────────────────────────────────────────────
SCRAPERS = {
    "remoteok":"RemoteOK","remotive":"Remotive","jobicy":"Jobicy",
    "arbeitnow":"ArbeitNow","themuse":"The Muse","hn":"Hacker News",
    "careerjet":"Careerjet","linkedin":"LinkedIn","weworkremotely":"WeWorkRemotely",
    "dice":"Dice","glassdoor":"Glassdoor","ycombinator":"YCombinator",
    "github":"GitHub","indeed":"Indeed","monster":"Monster","usajobs":"USAJobs",
}
RELIABLE = ["remoteok","remotive","jobicy","arbeitnow","themuse","hn","careerjet","linkedin"]

# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Search", page_icon="💼",
                   layout="wide", initial_sidebar_state="collapsed")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ── Reset ─────────────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }
html, body { height: 100%; margin: 0;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
[data-testid="stAppViewContainer"] { background: #f3f2ef !important; }
[data-testid="stHeader"] { display: none !important; }
.block-container { padding: 0 !important; max-width: 100% !important; margin: 0 !important; }
[data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: 100% !important; }
[data-testid="stMain"] { padding: 0 !important; }
footer, #MainMenu { display: none !important; }

/* ── Top nav ────────────────────────────────────────────────────────────── */
.ln-nav {
    position: sticky; top: 0; z-index: 999;
    background: #fff;
    border-bottom: 1px solid #e0ddd8;
    display: flex; align-items: center;
    padding: 0 20px; height: 56px; gap: 10px;
    box-shadow: 0 2px 6px rgba(0,0,0,.07);
}
.ln-logo {
    background: #0a66c2; color: #fff;
    font-weight: 900; font-size: 20px;
    width: 36px; height: 36px; border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; letter-spacing: -1px;
}
.ln-search-wrap {
    display: flex; align-items: center;
    background: #eef3f8; border-radius: 22px;
    padding: 0 14px; height: 38px; gap: 8px;
    width: 260px; border: 1.5px solid transparent;
    transition: border-color .15s, background .15s;
}
.ln-search-icon { color: #0a66c2; font-size: 14px; }
.ln-search-txt  { font-size: 13px; color: #555; font-weight: 500; }
.ln-nav-links   { display: flex; gap: 2px; margin-left: 12px; }
.ln-nav-link {
    display: flex; flex-direction: column; align-items: center;
    padding: 6px 16px; border-radius: 6px; cursor: pointer;
    color: #666; font-size: 11px; font-weight: 600; gap: 2px;
    border-bottom: 2px solid transparent;
    transition: color .1s; text-decoration: none; white-space: nowrap;
}
.ln-nav-link:hover { color: #1d2226; }
.ln-nav-link.active { color: #1d2226; border-bottom-color: #1d2226; }
.ln-nav-icon { font-size: 19px; }
.ln-spacer { flex: 1; }
.ln-nav-stat {
    font-size: 12px; color: #0a66c2; font-weight: 700;
    background: #eef3f8; border-radius: 14px;
    padding: 5px 14px; border: 1px solid #c9dff5;
    white-space: nowrap;
}

/* ── Filter inputs ───────────────────────────────────────────────────────── */
.filter-inputs [data-testid="stTextInput"] label,
.filter-inputs [data-testid="stSelectbox"] label,
.filter-inputs [data-testid="stMultiSelect"] label,
.filter-inputs [data-testid="stNumberInput"] label { display: none; }
.filter-inputs [data-testid="stTextInput"] input,
.filter-inputs [data-testid="stSelectbox"] div[data-baseweb],
.filter-inputs [data-testid="stMultiSelect"] div[data-baseweb] {
    border-radius: 22px !important; font-size: 13px !important;
}

/* ── Left panel header ──────────────────────────────────────────────────── */
.lp-header {
    background: #fff; border-bottom: 1px solid #e8e5e0;
    padding: 14px 16px 12px;
    position: sticky; top: 0; z-index: 10;
}
.lp-header-query { font-size: 16px; font-weight: 700; color: #1d2226; margin-bottom: 4px; }
.lp-header-meta {
    display: flex; align-items: center;
    justify-content: space-between;
    font-size: 13px; color: #666;
}
.alert-btn {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12px; font-weight: 700; color: #0a66c2;
    cursor: pointer; padding: 3px 10px; border-radius: 14px;
    border: 1.5px solid #0a66c2; transition: background .12s;
    background: #fff;
}
.alert-btn:hover { background: #eef3f8; }
.alert-btn.on { background: #0a66c2; color: #fff; }

/* ── Job card ────────────────────────────────────────────────────────────── */
.jcard {
    padding: 14px 16px 12px;
    border-bottom: 1px solid #e8e5e0;
    background: #fff; cursor: pointer;
    transition: background .12s;
    position: relative;
}
.jcard:hover { background: #f9f7f4; }
.jcard.active {
    background: #eef3f8;
    border-left: 3px solid #0a66c2;
    padding-left: 13px;
}
.jcard-top { display: flex; gap: 12px; align-items: flex-start; }
.jcard-logo {
    width: 52px; height: 52px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; font-weight: 800; color: #fff;
    flex-shrink: 0;
    box-shadow: 0 1px 5px rgba(0,0,0,.14);
}
.jcard-info { flex: 1; min-width: 0; }
.jcard-title {
    font-size: 15px; font-weight: 700; color: #0a66c2;
    line-height: 1.35; margin-bottom: 3px;
    overflow: hidden; text-overflow: ellipsis;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.jcard-company { font-size: 14px; font-weight: 500; color: #1d2226; margin-bottom: 2px; }
.jcard-location { font-size: 12px; color: #666; margin-bottom: 5px; line-height: 1.4; }
.jcard-salary { font-size: 13px; color: #057642; font-weight: 700; margin-bottom: 6px; }
.jcard-footer { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.jtag-new {
    font-size: 11px; font-weight: 700; color: #057642;
    background: #e6f4ea; border-radius: 4px; padding: 2px 7px;
}
.jtag-easy {
    font-size: 11px; font-weight: 700; color: #0a66c2;
    background: #eef3f8; border-radius: 4px; padding: 2px 7px;
    border: 1px solid #c9dff5;
}
.jtag-type {
    font-size: 11px; color: #444; font-weight: 500;
    background: #f3f2ef; border-radius: 4px; padding: 2px 7px;
    border: 1px solid #e0ddd8;
}
.jtag-src {
    font-size: 11px; color: #888; font-weight: 500;
    background: #fafaf8; border-radius: 4px; padding: 2px 7px;
    border: 1px solid #e8e5e0;
}
.jtag-time { font-size: 11px; color: #bbb; margin-left: auto; }
.match-bar {
    height: 3px; border-radius: 2px; background: #e8e5e0;
    margin-top: 8px; overflow: hidden;
}
.match-fill { height: 100%; border-radius: 2px; }
.match-pct-inline {
    font-size: 10px; color: #888; margin-top: 3px;
}

/* ── Detail pane ─────────────────────────────────────────────────────────── */
.dp-card {
    background: #fff; border-radius: 10px;
    box-shadow: 0 0 0 1px rgba(0,0,0,.08), 0 2px 8px rgba(0,0,0,.04);
    margin-bottom: 12px; overflow: hidden;
}
.dp-header { padding: 22px 24px 18px; }
.dp-company-row {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px;
}
.dp-company-left { display: flex; align-items: center; gap: 14px; }
.dp-logo {
    width: 58px; height: 58px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; font-weight: 800; color: #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,.14); flex-shrink: 0;
}
.dp-company-name { font-size: 15px; font-weight: 700; color: #1d2226; margin-bottom: 2px; }
.dp-company-sub  { font-size: 12px; color: #888; }
.dp-actions { display: flex; gap: 6px; }
.dp-icon-btn {
    width: 34px; height: 34px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    border: 1.5px solid #c5c3be; color: #666; font-size: 15px;
    cursor: pointer; background: #fff; transition: background .1s;
}
.dp-icon-btn:hover { background: #f3f2ef; }
.dp-title {
    font-size: 23px; font-weight: 800; color: #1d2226;
    line-height: 1.25; margin-bottom: 10px;
}
.dp-meta-row {
    display: flex; align-items: center; gap: 0;
    flex-wrap: wrap; font-size: 13px; color: #555;
    margin-bottom: 12px; line-height: 1.8;
}
.dp-meta-item { display: inline-flex; align-items: center; gap: 4px; }
.dp-meta-dot { margin: 0 6px; color: #ccc; }
.dp-salary { font-size: 15px; font-weight: 700; color: #057642; }
.dp-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.dp-chip {
    border: 1.5px solid #c5c3be; border-radius: 22px;
    padding: 5px 16px; font-size: 13px; color: #1d2226;
    font-weight: 500; background: #fff;
}
.dp-chip.type { border-color: #0a66c2; color: #0a66c2; background: #eef3f8; }
.dp-chip.remote { border-color: #057642; color: #057642; background: #e6f4ea; }
.dp-btns { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.dp-apply {
    display: inline-flex; align-items: center; gap: 6px;
    background: #f55d2c; color: #fff; border: none;
    border-radius: 24px; padding: 10px 26px;
    font-size: 14px; font-weight: 700; cursor: pointer;
    text-decoration: none; transition: background .15s;
    box-shadow: 0 2px 6px rgba(245,93,44,.35);
    letter-spacing: .2px;
}
.dp-apply:hover { background: #d94f24; color: #fff; }
.dp-save {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1.5px solid #1d2226; color: #1d2226; background: #fff;
    border-radius: 24px; padding: 9px 22px;
    font-size: 14px; font-weight: 600; cursor: pointer;
    transition: background .12s;
}
.dp-save:hover { background: #f3f2ef; }
.dp-save.saved { border-color: #0a66c2; color: #0a66c2; background: #eef3f8; }

/* ── Match card ──────────────────────────────────────────────────────────── */
.match-card {
    border: 1.5px solid #e0ddd8; border-radius: 10px;
    padding: 16px 18px; background: #fafaf8;
    margin-bottom: 12px;
}
.match-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.match-avatar {
    width: 38px; height: 38px; border-radius: 50%; background: #0a66c2;
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 14px; font-weight: 800; flex-shrink: 0;
}
.match-text { flex: 1; }
.match-title { font-size: 14px; font-weight: 700; color: #1d2226; margin-bottom: 2px; }
.match-sub   { font-size: 12px; color: #666; }
.match-pct-badge {
    font-size: 22px; font-weight: 800; color: #0a66c2;
    min-width: 52px; text-align: right;
}
.match-score-bar { height: 6px; background: #e8e5e0; border-radius: 3px; margin: 4px 0 10px; }
.match-score-fill { height: 100%; border-radius: 3px; }
.match-skills { display: flex; gap: 6px; flex-wrap: wrap; }
.skill-chip { font-size: 12px; padding: 3px 10px; border-radius: 12px; font-weight: 500; }
.skill-match { background: #dbeafe; color: #1d4ed8; border: 1px solid #bfdbfe; }
.skill-miss  { background: #f3f2ef; color: #888; border: 1px solid #e0ddd8; }

/* ── Benefits ────────────────────────────────────────────────────────────── */
.benefits-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;
}
.benefit-item {
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; color: #1d2226;
    background: #f8f7f5; border-radius: 8px; padding: 8px 12px;
    border: 1px solid #ece9e4;
}
.benefit-icon { font-size: 16px; flex-shrink: 0; }

/* ── Sections ────────────────────────────────────────────────────────────── */
.dp-section { padding: 18px 24px; }
.dp-section-title {
    font-size: 17px; font-weight: 700; color: #1d2226;
    margin-bottom: 12px; padding-bottom: 8px;
    border-bottom: 1px solid #f0ede8;
}
.dp-body { font-size: 14px; line-height: 1.72; color: #1d2226; }
.dp-req-item {
    display: flex; align-items: flex-start; gap: 10px;
    font-size: 14px; color: #1d2226; margin-bottom: 8px;
    line-height: 1.55; padding: 4px 0;
}
.dp-req-dot { color: #0a66c2; font-size: 18px; line-height: 1.2; flex-shrink: 0; }
hr.dp-div { border: none; border-top: 1px solid #f0ede8; margin: 0; }

/* ── Scrape tab ──────────────────────────────────────────────────────────── */
.scrape-wrap { max-width: 1000px; margin: 28px auto; padding: 0 20px; }
.scrape-hero {
    background: linear-gradient(135deg, #0a66c2 0%, #003e8a 100%);
    border-radius: 12px; padding: 32px 32px 28px; color: #fff;
    margin-bottom: 20px; position: relative; overflow: hidden;
}
.scrape-hero::before {
    content: ''; position: absolute; top: -40px; right: -40px;
    width: 220px; height: 220px; border-radius: 50%;
    background: rgba(255,255,255,.06);
}
.scrape-hero::after {
    content: ''; position: absolute; bottom: -60px; right: 80px;
    width: 160px; height: 160px; border-radius: 50%;
    background: rgba(255,255,255,.04);
}
.scrape-hero h2 { margin: 0 0 8px; font-size: 26px; font-weight: 800; position: relative; }
.scrape-hero p  { margin: 0; opacity: .85; font-size: 14px; line-height: 1.6; position: relative; }
.stat-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.stat-box {
    background: #fff; border-radius: 10px; padding: 16px 20px;
    box-shadow: 0 0 0 1px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.04);
    min-width: 100px; text-align: center; flex: 1;
}
.stat-box .num { font-size: 28px; font-weight: 800; color: #0a66c2; }
.stat-box .lbl {
    font-size: 11px; color: #888; margin-top: 3px;
    text-transform: uppercase; letter-spacing: .5px; font-weight: 500;
}
.scrape-form {
    background: #fff; border-radius: 10px;
    box-shadow: 0 0 0 1px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.04);
    padding: 24px 28px; margin-bottom: 20px;
}
.source-chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 14px; }
.s-chip {
    font-size: 12px; padding: 4px 12px; border-radius: 14px;
    font-weight: 500; border: 1.5px solid;
}
.s-chip.ok  { background: #eef3f8; color: #0a66c2; border-color: #c9dff5; }
.s-chip.meh { background: #f8f7f5; color: #999; border-color: #e0ddd8; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: #fff; border-bottom: 2px solid #f0ede8;
    padding: 0 20px; gap: 0;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    font-size: 14px !important; font-weight: 600 !important;
    padding: 14px 22px !important; color: #666 !important;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -2px !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    color: #0a66c2 !important;
    border-bottom: 2px solid #0a66c2 !important;
}
[data-testid="stTabs"] [data-baseweb="tab-panel"] { padding: 0 !important; }
[data-testid="stVerticalBlockBorderWrapper"] { height: 100%; }

/* ── jcard select button ─────────────────────────────────────────────────── */
div[class*="st-key-jcard_"] button {
    border-radius: 22px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
}
</style>
""", unsafe_allow_html=True)

# ── DB helpers ────────────────────────────────────────────────────────────────
def _db_conn():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

def _ensure_schema():
    with _db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT,
            job_type TEXT, salary_min INTEGER, salary_max INTEGER, salary_raw TEXT,
            description TEXT, requirements TEXT, nice_to_have TEXT,
            apply_url TEXT, posted_date TEXT, company_about TEXT,
            contact_email TEXT, source TEXT, scraped_at TEXT NOT NULL,
            UNIQUE(title, company, apply_url)
        );
        CREATE INDEX IF NOT EXISTS idx_title    ON job_applications(title);
        CREATE INDEX IF NOT EXISTS idx_company  ON job_applications(company);
        CREATE INDEX IF NOT EXISTS idx_location ON job_applications(location);
        CREATE INDEX IF NOT EXISTS idx_salary   ON job_applications(salary_min, salary_max);
        """)

_ensure_schema()

def query_jobs(title="", company="", location="", job_type="",
               min_salary=None, sources=None, date_days=None,
               experience=None, limit=500) -> list[dict]:
    conds, params = [], []
    if title:
        conds.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ?)")
        params += [f"%{title.lower()}%", f"%{title.lower()}%"]
    if company:
        conds.append("LOWER(company) LIKE ?"); params.append(f"%{company.lower()}%")
    if location:
        conds.append("LOWER(location) LIKE ?"); params.append(f"%{location.lower()}%")
    if job_type:
        conds.append("LOWER(job_type) = ?"); params.append(job_type.lower())
    if min_salary:
        conds.append("(salary_max IS NOT NULL AND salary_max >= ?)"); params.append(min_salary)
    if sources:
        conds.append(f"source IN ({','.join('?'*len(sources))})"); params += sources
    if date_days:
        conds.append("julianday('now') - julianday(scraped_at) <= ?"); params.append(date_days)
    if experience:
        kw = {"Senior":"senior","Mid":"mid","Junior":"junior","Entry":"entry"}.get(experience,"")
        if kw:
            conds.append("LOWER(title) LIKE ?"); params.append(f"%{kw}%")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with _db_conn() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM job_applications {where} ORDER BY scraped_at DESC LIMIT ?",
            params + [limit]).fetchall()]

def get_job(jid: int) -> Optional[dict]:
    with _db_conn() as c:
        row = c.execute("SELECT * FROM job_applications WHERE id=?", (jid,)).fetchone()
    if not row: return None
    r = dict(row)
    for f in ("requirements","nice_to_have"):
        if r.get(f):
            try: r[f] = json.loads(r[f])
            except: pass
    return r

def total_jobs() -> int:
    with _db_conn() as c: return c.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0]

def jobs_by_source() -> dict:
    with _db_conn() as c:
        return {r["src"]: r["cnt"] for r in c.execute(
            "SELECT COALESCE(source,'?') src, COUNT(*) cnt FROM job_applications GROUP BY src ORDER BY cnt DESC"
        ).fetchall()}

def sources_in_db() -> list:
    with _db_conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT source FROM job_applications WHERE source IS NOT NULL ORDER BY source"
        ).fetchall()]

# ── Helpers ───────────────────────────────────────────────────────────────────
COLORS = ["#0a66c2","#057642","#7a3e98","#c37d16","#c0392b",
          "#16a085","#2980b9","#8e44ad","#d35400","#27ae60",
          "#e74c3c","#e67e22","#1abc9c","#c0392b","#2c3e50"]

def _color(s): return COLORS[hash(s) % len(COLORS)]

def _initials(s):
    ws = s.strip().split()[:2]
    return "".join(w[0].upper() for w in ws if w)[:2] or "?"

def _ago(ds):
    if not ds: return ""
    try:
        # Handle Unix timestamps (int or numeric string)
        ts = int(str(ds))
        d = datetime.utcfromtimestamp(ts).date()
    except (ValueError, TypeError, OSError):
        try:
            d = datetime.fromisoformat(str(ds)[:10]).date()
        except Exception:
            return ""
    n = (date.today() - d).days
    if n <= 0: return "Today"
    if n == 1: return "1d ago"
    if n < 7:  return f"{n}d ago"
    if n < 30: return f"{n//7}w ago"
    return f"{n//30}mo ago"

def _salary(row):
    if row.get("salary_raw"): return row["salary_raw"]
    if row.get("salary_min") and row.get("salary_max"):
        return f"${row['salary_min']:,} – ${row['salary_max']:,}"
    return ""

def _match_score(job: dict) -> tuple[int, list[str], list[str]]:
    """Return (score 0-100, matched_skills, missing_skills)."""
    if not USER_SKILLS: return 0, [], []
    text = " ".join(filter(None, [
        job.get("title",""), job.get("description",""),
        json.dumps(job.get("requirements") or ""),
    ])).lower()
    matched = [s for s in USER_SKILLS if s in text]
    missing = [s for s in USER_SKILLS if s not in text]
    score = int(len(matched) / max(len(USER_SKILLS), 1) * 100)
    return score, matched[:6], missing[:4]

BENEFIT_MAP = {
    "401(k)":"💰","dental":"🦷","vision":"👁","health":"🏥","medical":"🏥",
    "remote":"🏠","flexible":"⏰","pto":"🏖","vacation":"🏖","stock":"📈",
    "equity":"📈","bonus":"🎁","commuter":"🚇","disability":"🛡","life insurance":"🛡",
    "parental":"👶","tuition":"📚","gym":"💪","wellness":"🌿",
}

def _extract_benefits(desc: str) -> list[tuple[str,str]]:
    if not desc: return []
    dl = desc.lower()
    found = []
    for kw, icon in BENEFIT_MAP.items():
        if kw in dl:
            found.append((icon, kw.title()))
    return found[:6]

def _exp_level(title: str) -> str:
    tl = title.lower()
    if any(w in tl for w in ("senior","sr.","principal","staff","lead")): return "Senior"
    if any(w in tl for w in ("junior","jr.","entry","associate")): return "Entry"
    if any(w in tl for w in ("director","vp","head of","chief")): return "Director"
    return "Mid"

def _is_new(ds):
    try: return (date.today() - datetime.fromisoformat(ds[:10]).date()).days <= 2
    except: return False

# ── Session state ─────────────────────────────────────────────────────────────
if "sel_id"    not in st.session_state: st.session_state["sel_id"]    = None
if "saved"     not in st.session_state: st.session_state["saved"]     = set()
if "dismissed" not in st.session_state: st.session_state["dismissed"] = set()
if "alert_on"  not in st.session_state: st.session_state["alert_on"]  = False

# ─────────────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────────────
tab_jobs, tab_saved, tab_scrape = st.tabs([
    "  💼  Jobs  ",
    f"  🔖  Saved ({len(st.session_state['saved'])})  ",
    "  🔍  Scrape  ",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — JOBS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_jobs:
    total = total_jobs()

    # ── Top nav ───────────────────────────────────────────────────────────────
    st.markdown(f"""
<div class="ln-nav">
  <div class="ln-logo">in</div>
  <div class="ln-search-wrap">
    <span class="ln-search-icon">🔍</span>
    <span class="ln-search-txt">Search jobs, skills, companies</span>
  </div>
  <div class="ln-nav-links">
    <a class="ln-nav-link active" href="#">
      <span class="ln-nav-icon">💼</span>Jobs
    </a>
    <a class="ln-nav-link" href="#">
      <span class="ln-nav-icon">🔖</span>Saved
    </a>
    <a class="ln-nav-link" href="#">
      <span class="ln-nav-icon">🔍</span>Scrape
    </a>
  </div>
  <div class="ln-spacer"></div>
  <span class="ln-nav-stat">📊 {total:,} jobs</span>
</div>
""", unsafe_allow_html=True)

    # ── Filter inputs (hidden labels via CSS) ─────────────────────────────────
    st.markdown('<div class="filter-inputs">', unsafe_allow_html=True)
    fc = st.columns([3, 2, 1.6, 1.8, 1.4, 1.4, 1])
    f_title    = fc[0].text_input("q",  placeholder="🔍 Title, skill, keyword", key="f_title")
    f_location = fc[1].text_input("l",  placeholder="📍 Location or Remote",    key="f_location")
    f_date     = fc[2].selectbox("date", ["Any time","Past 24h","Past week","Past month"], key="f_date")
    f_exp      = fc[3].selectbox("exp",  ["All levels","Senior","Mid","Junior","Entry","Director"], key="f_exp")
    f_type     = fc[4].selectbox("type", ["All types","remote","full-time","part-time","contract"], key="f_type")
    avail_src  = sources_in_db()
    f_src      = fc[5].multiselect("src", avail_src, placeholder="All sources", key="f_src")
    f_salary   = fc[6].number_input("sal", value=0, step=25000, min_value=0,
                                    placeholder="Min $", key="f_salary",
                                    help="Minimum annual salary (USD)")
    st.markdown('</div>', unsafe_allow_html=True)

    date_map = {"Past 24h":1, "Past week":7, "Past month":30}

    rows = query_jobs(
        title      = f_title,
        location   = f_location,
        job_type   = "" if f_type == "All types" else f_type,
        min_salary = int(f_salary) if f_salary else None,
        sources    = f_src if f_src else None,
        date_days  = date_map.get(f_date),
        experience = None if f_exp == "All levels" else f_exp,
    )
    # Remove dismissed
    rows = [r for r in rows if r["id"] not in st.session_state["dismissed"]]

    if total == 0:
        st.markdown("""
        <div style="text-align:center;padding:80px 20px;color:#888">
          <div style="font-size:52px;margin-bottom:16px">💼</div>
          <div style="font-size:20px;font-weight:700;color:#555;margin-bottom:8px">No jobs yet</div>
          <div style="font-size:14px">Go to the <strong>Scrape</strong> tab to fetch your first jobs.</div>
        </div>""", unsafe_allow_html=True)
        st.stop()

    # ── Build query label for panel header ────────────────────────────────────
    q_label = f_title or "Jobs"
    l_label = f_location or "Everywhere"

    # ── Two-pane ──────────────────────────────────────────────────────────────
    left_col, right_col = st.columns([5, 7], gap="small")

    # ───────────────────────────────────────────────────
    # LEFT — job list
    # ───────────────────────────────────────────────────
    with left_col:

        alert_icon = "🔔" if st.session_state["alert_on"] else "🔔"
        alert_cls  = "on" if st.session_state["alert_on"] else ""
        st.markdown(f"""
<div class="lp-header">
  <div class="lp-header-query">{q_label} — {l_label}</div>
  <div class="lp-header-meta">
    <span style="font-weight:600;color:#1d2226">{len(rows):,}</span>&nbsp;<span>result{"s" if len(rows)!=1 else ""}</span>
    <span class="alert-btn {alert_cls}">{alert_icon} Set alert</span>
  </div>
</div>
""", unsafe_allow_html=True)

        # Alert toggle button (invisible, overlays the icon)
        if st.button("Toggle alert", key="alert_toggle",
                     help="Toggle job alert", use_container_width=False):
            st.session_state["alert_on"] = not st.session_state["alert_on"]
            st.rerun()

        if not rows:
            st.markdown("""
<div style="padding:50px 16px;text-align:center;color:#999">
  <div style="font-size:36px;margin-bottom:10px">🔎</div>
  <div style="font-size:15px;font-weight:600;color:#666">No matching jobs</div>
  <div style="font-size:13px;margin-top:4px">Try adjusting your filters</div>
</div>""", unsafe_allow_html=True)
        else:
            # Scrollable list
            with st.container(height=680):
                for row in rows:
                    jid      = row["id"]
                    is_sel   = st.session_state["sel_id"] == jid
                    is_saved = jid in st.session_state["saved"]
                    color    = _color(row["company"])
                    init     = _initials(row["company"])
                    sal      = _salary(row)
                    loc      = row.get("location") or ""
                    jtype    = (row.get("job_type") or "").replace("-"," ").title()
                    src      = row.get("source") or ""
                    posted   = row.get("posted_date") or row.get("scraped_at") or ""
                    has_url  = bool(row.get("apply_url"))
                    new_tag  = _is_new(posted)
                    exp_lv   = _exp_level(row["title"])
                    score, _, _ = _match_score(row)

                    active_cls = "active" if is_sel else ""

                    # Escape all user data to prevent broken HTML
                    e_title   = _html.escape(row["title"])
                    e_company = _html.escape(row["company"])
                    e_loc     = _html.escape(loc)
                    e_src     = _html.escape(src)
                    e_sal     = _html.escape(sal)

                    sal_html = f'<div class="jcard-salary">💰 {e_sal}</div>' if e_sal else ""

                    match_color = "#057642" if score >= 70 else "#0a66c2" if score >= 40 else "#c37d16"
                    match_html = ""
                    if score > 0:
                        match_html = f"""<div class="match-bar">
  <div class="match-fill" style="width:{score}%;background:{match_color}"></div>
</div>
<div class="match-pct-inline" style="color:{match_color}">{score}% match</div>"""

                    ftags = []
                    if new_tag:
                        ftags.append('<span class="jtag-new">New</span>')
                    if jtype:
                        ftags.append(f'<span class="jtag-type">{_html.escape(jtype)}</span>')
                    if exp_lv and exp_lv != "Mid":
                        ftags.append(f'<span class="jtag-type">{exp_lv}</span>')
                    if has_url:
                        ftags.append('<span class="jtag-easy">Easy Apply</span>')
                    if e_src:
                        ftags.append(f'<span class="jtag-src">{e_src}</span>')
                    if is_saved:
                        ftags.append('<span class="jtag-src">🔖 Saved</span>')
                    ago = _ago(posted)
                    if ago:
                        ftags.append(f'<span class="jtag-time">{ago}</span>')

                    st.markdown(f"""
<div class="jcard {active_cls}">
  <div class="jcard-top">
    <div class="jcard-logo" style="background:{color}">{init}</div>
    <div class="jcard-info">
      <div class="jcard-title">{e_title}</div>
      <div class="jcard-company">{e_company}</div>
      <div class="jcard-location">📍 {e_loc}</div>
      {sal_html}
      <div class="jcard-footer">{"".join(ftags)}</div>
      {match_html}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

                    # Action row: select + save + dismiss
                    btn_c1, btn_c2, btn_c3 = st.columns([5, 1, 1])
                    with btn_c1:
                        if st.button(
                            "View details →" if not is_sel else "✓ Viewing",
                            key=f"jcard_{jid}",
                            use_container_width=True,
                            type="primary" if is_sel else "secondary",
                        ):
                            st.session_state["sel_id"] = jid
                            st.rerun()
                    with btn_c2:
                        save_icon = "🔖" if not is_saved else "✅"
                        if st.button(save_icon, key=f"save_{jid}", help="Save job"):
                            if is_saved:
                                st.session_state["saved"].discard(jid)
                            else:
                                st.session_state["saved"].add(jid)
                            st.rerun()
                    with btn_c3:
                        if st.button("✕", key=f"dim_{jid}", help="Dismiss"):
                            st.session_state["dismissed"].add(jid)
                            if st.session_state["sel_id"] == jid:
                                st.session_state["sel_id"] = None
                            st.rerun()

    # ───────────────────────────────────────────────────
    # RIGHT — detail panel
    # ───────────────────────────────────────────────────
    with right_col:
        sel_id = st.session_state["sel_id"]

        if sel_id is None:
            st.markdown("""
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
            height:620px;background:#fff;border-radius:8px;
            box-shadow:0 0 0 1px rgba(0,0,0,.08);color:#aaa;text-align:center;padding:40px">
  <div style="font-size:56px;margin-bottom:16px">👈</div>
  <div style="font-size:18px;font-weight:700;color:#666;margin-bottom:8px">Select a job to view details</div>
  <div style="font-size:14px;color:#999">Click any job card on the left<br>to see the full description here.</div>
</div>""", unsafe_allow_html=True)
        else:
            job = get_job(int(sel_id))
            if not job:
                st.warning("Job not found.")
            else:
                color     = _color(job["company"])
                init      = _initials(job["company"])
                sal       = _salary(job)
                loc       = job.get("location") or "Location not specified"
                jtype     = (job.get("job_type") or "").replace("-"," ").title()
                exp_lv    = _exp_level(job["title"])
                src       = job.get("source") or "—"
                posted    = job.get("posted_date") or job.get("scraped_at") or ""
                apply_url = job.get("apply_url") or ""
                is_saved  = sel_id in st.session_state["saved"]
                score, matched, missing = _match_score(job)
                benefits  = _extract_benefits(job.get("description","") or "")
                desc      = job.get("description") or ""
                req       = job.get("requirements")
                nth       = job.get("nice_to_have")
                about_co  = job.get("company_about") or ""

                # Escape all user data for safe HTML rendering
                e_title    = _html.escape(job["title"])
                e_company  = _html.escape(job["company"])
                e_loc      = _html.escape(loc)
                e_src      = _html.escape(src)
                e_sal      = _html.escape(sal)
                e_jtype    = _html.escape(jtype)
                e_exp_lv   = _html.escape(exp_lv)

                apply_html = (
                    f'<a class="dp-apply" href="{_html.escape(apply_url)}" target="_blank">Apply Now ↗</a>'
                    if apply_url else
                    '<span class="dp-apply" style="opacity:.5;cursor:default">No apply link</span>'
                )
                save_cls  = "saved" if is_saved else ""

                is_remote = "remote" in (job.get("job_type","") or "").lower() or "remote" in loc.lower()

                with st.container(height=680):
                    # ── Header card ───────────────────────────────────────────
                    ago_str    = _ago(posted)
                    sal_meta   = f'<span class="dp-meta-dot">·</span><span class="dp-meta-item dp-salary">💰 {e_sal}</span>' if e_sal else ""
                    ago_meta   = f'<span class="dp-meta-dot">·</span><span class="dp-meta-item">📅 {ago_str}</span>' if ago_str else ""
                    type_chip  = f'<span class="dp-chip type">{e_jtype}</span>' if e_jtype else ""
                    exp_chip   = f'<span class="dp-chip">{e_exp_lv}</span>' if e_exp_lv else ""
                    rem_chip   = '<span class="dp-chip remote">🏠 Remote</span>' if is_remote else ""
                    st.markdown(f"""
<div class="dp-card">
  <div class="dp-header">
    <div class="dp-company-row">
      <div class="dp-company-left">
        <div class="dp-logo" style="background:{color}">{init}</div>
        <div>
          <div class="dp-company-name">{e_company}</div>
          <div class="dp-company-sub">{e_src} · {ago_str}</div>
        </div>
      </div>
      <div class="dp-actions">
        <div class="dp-icon-btn" title="Share">↗</div>
        <div class="dp-icon-btn" title="More">•••</div>
      </div>
    </div>
    <div class="dp-title">{e_title}</div>
    <div class="dp-meta-row">
      <span class="dp-meta-item">📍 {e_loc}</span>
      {sal_meta}{ago_meta}
    </div>
    <div class="dp-chips">{type_chip}{exp_chip}{rem_chip}</div>
    <div class="dp-btns">{apply_html}</div>
  </div>
""", unsafe_allow_html=True)

                    # Save / dismiss buttons in Streamlit (for state)
                    sb1, sb2, sb3 = st.columns([2, 1, 1])
                    with sb1:
                        pass
                    with sb2:
                        if st.button(
                            "🔖 Saved" if is_saved else "🔖 Save",
                            key="dp_save",
                            use_container_width=True,
                        ):
                            if is_saved:
                                st.session_state["saved"].discard(sel_id)
                            else:
                                st.session_state["saved"].add(sel_id)
                            st.rerun()
                    with sb3:
                        if st.button("✕ Dismiss", key="dp_dismiss", use_container_width=True):
                            st.session_state["dismissed"].add(sel_id)
                            st.session_state["sel_id"] = None
                            st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)

                    # ── Match card ────────────────────────────────────────────
                    if score > 0 and USER_SKILLS:
                        bar_color = "#057642" if score >= 70 else "#0a66c2" if score >= 40 else "#c37d16"
                        match_text = (
                            "Strong match — qualifications align well" if score >= 70
                            else "Moderate match — some qualifications overlap" if score >= 40
                            else "Partial match — a few skills overlap"
                        )
                        m_chips = "".join(
                            f'<span class="skill-chip skill-match">{s}</span>' for s in matched
                        ) + "".join(
                            f'<span class="skill-chip skill-miss">{s}</span>' for s in missing[:3]
                        )
                        st.markdown(f"""
<div class="match-card">
  <div class="match-header">
    <div class="match-avatar">{_initials(USER_NAME)}</div>
    <div class="match-text">
      <div class="match-title">{match_text}</div>
      <div class="match-sub">Based on your profile skills</div>
    </div>
    <div class="match-pct-badge" style="color:{bar_color}">{score}%</div>
  </div>
  <div class="match-score-bar">
    <div class="match-score-fill" style="width:{score}%;background:{bar_color}"></div>
  </div>
  <div class="match-skills">{m_chips}</div>
</div>
""", unsafe_allow_html=True)

                    # ── Benefits ──────────────────────────────────────────────
                    if benefits:
                        b_items = "".join(
                            f'<div class="benefit-item"><span class="benefit-icon">{ic}</span>{nm}</div>'
                            for ic, nm in benefits
                        )
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:12px">
  <div class="dp-section">
    <div class="dp-section-title">Benefits</div>
    <div class="benefits-grid">{b_items}</div>
  </div>
</div>
""", unsafe_allow_html=True)

                    # ── About the job ─────────────────────────────────────────
                    if desc:
                        short = desc[:2500] + ("…" if len(desc) > 2500 else "")
                        # Escape HTML entities
                        safe = (short
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;"))
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:10px">
  <div class="dp-section">
    <div class="dp-section-title">About the job</div>
    <div class="dp-body" style="white-space:pre-wrap">{safe}</div>
  </div>
</div>
""", unsafe_allow_html=True)

                    # ── Requirements ──────────────────────────────────────────
                    if req:
                        items = req if isinstance(req, list) else [req]
                        req_html = "".join(
                            f'<div class="dp-req-item"><span class="dp-req-dot">•</span>{i}</div>'
                            for i in items
                        )
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:10px">
  <div class="dp-section">
    <div class="dp-section-title">Requirements</div>
    {req_html}
  </div>
</div>
""", unsafe_allow_html=True)

                    if nth:
                        items = nth if isinstance(nth, list) else [nth]
                        nth_html = "".join(
                            f'<div class="dp-req-item"><span class="dp-req-dot" style="color:#057642">◦</span>{i}</div>'
                            for i in items
                        )
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:10px">
  <div class="dp-section">
    <div class="dp-section-title">Nice to have</div>
    {nth_html}
  </div>
</div>
""", unsafe_allow_html=True)

                    # ── About Company ─────────────────────────────────────────
                    if about_co:
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:10px">
  <div class="dp-section">
    <div class="dp-section-title">About {job["company"]}</div>
    <div class="dp-body">{about_co}</div>
  </div>
</div>
""", unsafe_allow_html=True)

                    # ── Contact ───────────────────────────────────────────────
                    if job.get("contact_email"):
                        st.markdown(f"""
<div class="dp-card" style="margin-bottom:10px">
  <div class="dp-section" style="padding:12px 24px">
    📧 <strong>Contact:</strong> {job["contact_email"]}
  </div>
</div>
""", unsafe_allow_html=True)

                    with st.expander("🔧 Raw record"):
                        st.json(job)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SAVED JOBS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_saved:
    saved_ids = list(st.session_state["saved"])
    if not saved_ids:
        st.markdown("""
<div style="text-align:center;padding:80px 20px;color:#999">
  <div style="font-size:52px;margin-bottom:16px">🔖</div>
  <div style="font-size:18px;font-weight:700;color:#666;margin-bottom:8px">No saved jobs yet</div>
  <div style="font-size:14px">Click the 🔖 icon on any job card to save it here.</div>
</div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"### 🔖 Saved Jobs ({len(saved_ids)})")
        for jid in saved_ids:
            job = get_job(jid)
            if not job: continue
            color = _color(job["company"])
            init  = _initials(job["company"])
            sal   = _salary(job)
            with st.container():
                c1, c2 = st.columns([8, 1])
                with c1:
                    st.markdown(f"""
<div class="dp-card" style="padding:14px 16px;display:flex;gap:12px;align-items:center">
  <div class="jcard-logo" style="background:{color};width:44px;height:44px;border-radius:6px;
       display:inline-flex;align-items:center;justify-content:center;
       font-size:15px;font-weight:800;color:#fff;flex-shrink:0">{init}</div>
  <div>
    <div style="font-size:15px;font-weight:700;color:#0a66c2">{job["title"]}</div>
    <div style="font-size:13px;color:#1d2226">{job["company"]}</div>
    <div style="font-size:12px;color:#666">📍 {job.get("location") or "—"}
      {(" · 💰 " + sal) if sal else ""}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
                with c2:
                    if st.button("Remove", key=f"unsave_{jid}"):
                        st.session_state["saved"].discard(jid)
                        st.rerun()
                    if apply_url := job.get("apply_url"):
                        st.link_button("Apply →", apply_url)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SCRAPE
# ═══════════════════════════════════════════════════════════════════════════════
with tab_scrape:
    st.markdown('<div class="scrape-wrap">', unsafe_allow_html=True)

    # Hero
    by_src  = jobs_by_source()
    n_total = total_jobs()
    n_src   = len(by_src)
    st.markdown(f"""
<div class="scrape-hero">
  <h2>🔍 Fetch New Jobs</h2>
  <p>Pull live listings from 16 boards — LinkedIn, Dice, RemoteOK, Careerjet, Hacker News & more.<br>
     Results are deduplicated and saved to your local database instantly.</p>
</div>
""", unsafe_allow_html=True)

    # Stats
    cols = st.columns(min(max(n_src, 1) + 2, 8))
    cols[0].markdown(f"""
<div class="stat-box"><div class="num">{n_total:,}</div><div class="lbl">Total jobs</div></div>
""", unsafe_allow_html=True)
    cols[1].markdown(f"""
<div class="stat-box"><div class="num">{n_src}</div><div class="lbl">Sources</div></div>
""", unsafe_allow_html=True)
    for i, (src, cnt) in enumerate(list(by_src.items())[:6]):
        cols[i + 2].markdown(f"""
<div class="stat-box"><div class="num">{cnt}</div><div class="lbl">{src}</div></div>
""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Form
    st.markdown('<div class="scrape-form">', unsafe_allow_html=True)
    mode = st.radio("mode", ["Single source", "All reliable sources (recommended)"],
                    horizontal=True, label_visibility="collapsed")

    fc1, fc2, fc3 = st.columns([3, 2, 1])
    with fc1:
        if mode.startswith("Single"):
            scraper_key = st.selectbox(
                "Job site", list(SCRAPERS.keys()),
                format_func=lambda k: SCRAPERS[k],
            )
        else:
            scraper_key = None
            chips = "".join(
                f'<span class="s-chip {"ok" if s in RELIABLE else "meh"}">{SCRAPERS.get(s,s)}</span>'
                for s in SCRAPERS
            )
            st.markdown(f'<div class="source-chips">{chips}</div>', unsafe_allow_html=True)
        query_input = st.text_input("Search query", value="data engineer",
                                    placeholder="e.g. senior python engineer")
    with fc2:
        location_input = st.text_input("Location", value="Remote",
                                       placeholder="Remote, New York, London…")
        max_jobs = st.slider("Max results per source", 1, 30, 10)
    with fc3:
        st.markdown("<br><br>", unsafe_allow_html=True)
        run_btn = st.button("▶  Run Scraper", type="primary", use_container_width=True)

    # Build source list
    if mode.startswith("Single") and scraper_key:
        src_str = f"{scraper_key}:{query_input}"
        if location_input and scraper_key in ("indeed","arbeitnow","careerjet","linkedin"):
            src_str += f":{location_input}"
        sources_to_run = [src_str]
    else:
        sources_to_run = [
            f"{s}:{query_input}:{location_input}" if location_input else f"{s}:{query_input}"
            for s in RELIABLE
        ]

    preview = "  ·  ".join(f"`{s}`" for s in sources_to_run)
    st.caption(f"Will scrape: {preview}")
    st.markdown('</div>', unsafe_allow_html=True)

    log_area = st.empty()

    if run_btn:
        log_lines: list[str] = []

        def _stream(source_list: list[str]):
            for src in source_list:
                env = {
                    **os.environ,
                    "JOB_DB_PATH":  DB_PATH,
                    "_UI_SOURCE":   src,
                    "_UI_MAX_JOBS": str(max_jobs),
                }
                proc = subprocess.Popen(
                    [PYTHON_EXE, str(ROOT / "scraper.py")],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env, cwd=str(ROOT),
                )
                assert proc.stdout
                for line in proc.stdout:
                    log_lines.append(line.rstrip())
                proc.wait()
                log_lines.append(f"── {src}  exit:{proc.returncode} ──\n")

        t = threading.Thread(target=_stream, args=(sources_to_run,), daemon=True)
        t.start()
        bar, tick = st.progress(0, text="Scraping…"), 0
        while t.is_alive():
            time.sleep(0.4); tick = (tick+3)%95
            bar.progress(tick, text="Scraping…")
            log_area.code("\n".join(log_lines[-35:]), language="text")
        t.join()
        bar.progress(100, text="Complete ✓")
        log_area.code("\n".join(log_lines), language="text")
        st.success("Done! Switch to the **Jobs** tab to browse new results.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
