"""
JobFlow API Server
------------------
Flask backend that serves the dashboard UI and exposes REST endpoints
the frontend calls to show real data from the job application agent.

Endpoints:
  GET  /                          → serves dashboard.html
  GET  /api/stats                 → AppTracker summary stats
  GET  /api/applications          → list applications (?status=applied&limit=50)
  PATCH /api/applications/<id>    → update status  {status: "phone_screen"}
  GET  /api/follow-ups            → pending follow-ups
  GET  /api/inbox                 → applications formatted as inbox emails
  GET  /api/jobs/search           → live job search (?q=...&limit=20)
  POST /api/jobs/prepare          → tailor resume + cover letter for a job
                                    body: {title, company, url, description, source}

Usage:
    python api_server.py           # http://localhost:5050
    python api_server.py --port 8080
"""

from pipeline.llm import is_configured
import argparse
import json
import logging
import os
import sys
import threading
import uuid as _uuid_mod
from urllib.parse import urlsplit
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request, send_file, abort

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api_server")

app = Flask(__name__, static_folder=str(ROOT / "static"))

@app.before_request
def local_request_only():
    """This credential-bearing development server is loopback/same-origin only."""
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "local access only"}), 403
    if request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
        return jsonify({"error": "invalid host"}), 403
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify({"error": "cross-origin access denied"}), 403
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify({"error": "cross-site access denied"}), 403
    if request.method in {"POST", "PUT", "PATCH"} and not request.is_json:
        return jsonify({"error": "application/json required"}), 415
    if request.method in {"POST", "PUT", "PATCH"} and not isinstance(request.get_json(), dict):
        return jsonify({"error": "JSON object required"}), 400

DASHBOARD_HTML = ROOT / "static" / "dashboard.html"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tracker():
    from pipeline.app_tracker import AppTracker
    return AppTracker(db_path=ROOT / "data" / "applications.db")


def _safe_name(*parts: str) -> str:
    name = " — ".join(str(p) for p in parts if p)
    return "".join(c if c.isalnum() or c in " —_-" else "_" for c in name)[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if DASHBOARD_HTML.exists():
        return send_file(str(DASHBOARD_HTML))
    abort(404, "dashboard.html not found — run setup first")


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    try:
        import sqlite3 as _sqlite3
        db_path = ROOT / "data" / "applications.db"
        s = _tracker().summary_stats()
        total = s.get("total") or 0
        in_prog = s.get("in_progress") or 0
        response_rate = round((in_prog / total * 100) if total else 0, 1)

        # Applications per day for the last 7 days (Mon–Sun of current week)
        week_counts = [0] * 7
        this_week = 0
        if db_path.exists():
            con = _sqlite3.connect(str(db_path))
            con.row_factory = _sqlite3.Row
            rows = con.execute(
                """SELECT date(applied_at) as d, COUNT(*) as n
                   FROM applications
                   WHERE applied_at >= date('now', '-6 days')
                   GROUP BY d"""
            ).fetchall()
            con.close()
            from datetime import date, timedelta
            today = date.today()
            day_map = {(today - timedelta(days=i)).isoformat(): 6 - i for i in range(7)}
            for r in rows:
                idx = day_map.get(r["d"])
                if idx is not None:
                    week_counts[idx] = r["n"]
                    this_week += r["n"]

        return jsonify({
            "total":         total,
            "applied":       s.get("applied") or 0,
            "in_progress":   in_prog,
            "offers":        s.get("offers") or 0,
            "rejected":      s.get("rejected") or 0,
            "avg_score":     round(s.get("avg_score") or 0, 1),
            "response_rate": response_rate,
            "this_week":     this_week,
            "week_counts":   week_counts,
        })
    except Exception as e:
        log.error("stats error: %s", e)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Applications
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/applications")
def list_applications():
    status = request.args.get("status")
    limit  = int(request.args.get("limit", 50))
    try:
        rows = _tracker().list_applications(status=status, limit=limit)
        return jsonify(rows)
    except Exception as e:
        log.error("list_applications error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/applications/<int:app_id>", methods=["PATCH"])
def update_application(app_id):
    body = request.get_json(force=True) or {}
    new_status = body.get("status", "").strip()
    note       = body.get("note", "").strip()
    if not new_status:
        return jsonify({"error": "status required"}), 400
    valid = {"prepared", "failed", "applied","phone_screen","technical","onsite","offer",
             "rejected","withdrawn","ghosted","accepted"}
    if new_status not in valid:
        return jsonify({"error": f"invalid status '{new_status}'"}), 400
    try:
        _tracker().update_status(app_id, new_status, note or None)
        return jsonify({"ok": True, "app_id": app_id, "status": new_status})
    except Exception as e:
        log.error("update_application error: %s", e)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Follow-ups
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/follow-ups")
def follow_ups():
    days = int(request.args.get("days", 7))
    try:
        rows = _tracker().pending_follow_ups(days_ahead=days)
        return jsonify(rows)
    except Exception as e:
        log.error("follow_ups error: %s", e)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Inbox — applications as email-style items
# ─────────────────────────────────────────────────────────────────────────────

def _inbox_subject(row: dict) -> str:
    status = row.get("status", "applied")
    title  = row.get("job_title", "the position")
    co     = row.get("company_name") or "the company"
    if status == "applied":
        return f"Thanks for applying to {co}!"
    if status == "phone_screen":
        return f"Interview Scheduled — {title} at {co}"
    if status in ("technical", "onsite"):
        return f"Next steps: {title} interview at {co}"
    if status == "offer":
        return f"Offer received — {title} at {co}"
    if status == "rejected":
        return f"Update on your application to {co}"
    if status == "ghosted":
        return f"Following up: {title} at {co}"
    if status == "accepted":
        return f"Accepted — {title} at {co}"
    return f"Application update: {title} at {co}"


def _inbox_tag(status: str) -> str:
    return {
        "applied":      "applied",
        "phone_screen": "interview",
        "technical":    "interview",
        "onsite":       "interview",
        "offer":        "offer",
        "rejected":     "rejection",
        "withdrawn":    "action",
        "ghosted":      "action",
        "accepted":     "offer",
    }.get(status, "applied")


def _time_ago(iso: str) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
        hours = int(diff.total_seconds() / 3600)
        if hours < 1:
            return "just now"
        if hours < 24:
            return f"{hours} hour{'s' if hours>1 else ''} ago"
        days = hours // 24
        if days < 7:
            return f"{days} day{'s' if days>1 else ''} ago"
        return dt.strftime("%b %d")
    except Exception:
        return iso[:10]


@app.route("/api/inbox")
def inbox():
    try:
        rows = _tracker().list_applications(limit=200)
        items = []
        for r in rows:
            items.append({
                "id":         r["id"],
                "from":       f"{r.get('company_name') or 'Company'} Hiring Team",
                "subject":    _inbox_subject(r),
                "company":    r.get("company_name") or "Unknown",
                "time":       _time_ago(r.get("applied_at") or r.get("created_at", "")),
                "tag":        _inbox_tag(r.get("status", "applied")),
                "job_title":  r.get("job_title", ""),
                "job_url":    r.get("job_url") or "",
                "status":     r.get("status", "applied"),
                "match_score": r.get("match_score") or 0,
                "source":     r.get("job_board") or "",
            })
        return jsonify(items)
    except Exception as e:
        log.error("inbox error: %s", e)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Job Matches — cached scrape results
# ─────────────────────────────────────────────────────────────────────────────

MATCHES_CACHE = ROOT / "data" / "job_matches_cache.json"


def _load_matches_cache() -> dict:
    if MATCHES_CACHE.exists():
        import json as _json
        return _json.loads(MATCHES_CACHE.read_text())
    return {"jobs": [], "fetched_at": "", "queries": []}


def _score_matches(jobs: list) -> list:
    """Add match_score to each job using keyword matcher if profile is available."""
    try:
        from pipeline.job_matcher import JobMatcher
        from pipeline.profile_analyzer import ProfileAnalyzer

        profile = ProfileAnalyzer(cache_dir=ROOT / 'data').load_cached()
        if profile is None:
            return [{**j, 'match_score': None} for j in jobs]
        matcher = JobMatcher(use_claude=False)

        class _FakeJob:
            pass

        scored = []
        for j in jobs:
            fake = _FakeJob()
            fake.title = j.get("title", "")
            fake.company = j.get("company", "")
            fake.description = j.get("description", "")
            fake.location = j.get("location", "")
            fake.requirements = []
            try:
                score = matcher.score_job(fake, profile).score
            except Exception:
                score = 0
            scored.append({**j, "match_score": score})
        scored.sort(key=lambda x: x["match_score"], reverse=True)
        return scored
    except Exception as e:
        log.warning("Scoring unavailable: %s", e)
        return jobs


@app.route("/api/jobs/matches")
def jobs_matches():
    """Return discovery history filtered by first-seen date."""
    from pipeline.job_store import JobStore
    store = JobStore(ROOT / 'data/discovered_jobs.db')
    if not store.list():
        cache = _load_matches_cache()
        if cache.get('jobs'):
            store.save(cache['jobs'], cache.get('fetched_at') or None)
    try:
        jobs = store.list(request.args.get('period', 'all'))
    except ValueError:
        return jsonify({'error': 'Invalid period'}), 400
    scored = _score_matches(jobs)
    return jsonify({
        "jobs":       scored,
        "total":      len(scored),
        "fetched_at": max((j['last_seen'] for j in jobs), default=''),
        "queries":    _read_settings().get('job_search', {}).get('queries', []),
    })


def _legacy_refresh_matches():
    """
    Refresh the job matches cache by running all free scrapers.
    Runs synchronously — may take 10-30s.
    """
    import json as _json
    import datetime

    body = request.get_json(force=True) or {}
    queries = body.get("queries") or [
        "Senior Data Engineer", "Staff Data Engineer",
        "Principal Data Engineer", "Data Platform Engineer",
    ]

    from pipeline.apify_scraper import (
        WeWorkRemotelyScraper, TheMuseScraper, RemoteOKScraper,
    )

    all_jobs = []

    for q in queries:
        for Cls, src in [(RemoteOKScraper, "remoteok"),
                         (WeWorkRemotelyScraper, "weworkremotely"),
                         (TheMuseScraper, "themuse")]:
            try:
                jobs = Cls().search(q, max_results=15)
                for j in jobs:
                    all_jobs.append({
                        "title":       j.title,
                        "company":     j.company,
                        "location":    j.location or "",
                        "url":         getattr(j, "apply_url", "") or "",
                        "apply_url":   getattr(j, "apply_url", "") or "",
                        "source":      src,
                        "salary_range": "",
                        "description": (j.description or "")[:400],
                        "match_score": 0,
                        "posted_at":   str(getattr(j, "posted_date", "") or ""),
                    })
                log.info("[%s] %d jobs for %r", src, len(jobs), q)
            except Exception as e:
                log.warning("[%s] failed for %r: %s", src, q, e)

    # Dedup
    seen, unique = set(), []
    for j in all_jobs:
        key = j["url"] or f"{j['company']}|{j['title']}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    cache = {
        "fetched_at": datetime.datetime.now().isoformat(),
        "queries":    queries,
        "jobs":       unique,
    }
    MATCHES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MATCHES_CACHE.write_text(_json.dumps(cache, indent=2))
    log.info("Refreshed matches cache: %d unique jobs", len(unique))

    scored = _score_matches(unique)
    return jsonify({
        "ok":    True,
        "total": len(scored),
        "jobs":  scored,
        "fetched_at": cache["fetched_at"],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Live job search
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/jobs/refresh-matches', methods=['POST'])
def refresh_matches():
    from pipeline.job_store import collect_jobs
    try:
        from datetime import datetime, timezone
        jobs = collect_jobs(ROOT, return_jobs=True, all_public=True)
        stamp = datetime.now(timezone.utc).isoformat()
        current = [{**job, 'last_seen': stamp} for job in jobs]
        return jsonify({'ok': True, 'fetched': len(current), 'total': len(current),
                        'jobs': _score_matches(current), 'fetched_at': stamp,
                        'queries': _read_settings().get('job_search', {}).get('queries', []),
                        'notice': 'Fresh results from configured sources. Public Greenhouse and Lever boards are uncapped; other portals may limit results or require access.'})
    except Exception:
        log.exception('Job collection failed')
        return jsonify({'error': 'Job collection failed; previous jobs retained'}), 502


@app.route("/api/jobs/search")
def jobs_search():
    q      = request.args.get("q", "").strip()
    limit  = int(request.args.get("limit", 20))
    source = request.args.get("source", "all").lower()   # all | remoteok | dice | wwr | muse

    if not q:
        return jsonify({"error": "q is required"}), 400

    try:
        from pipeline.apify_scraper import (
            DiceScraper, RemoteOKScraper,
            WeWorkRemotelyScraper, TheMuseScraper,
        )

        all_jobs = []

        if source in ("all", "remoteok"):
            try:
                jobs = RemoteOKScraper().search(q, max_results=limit)
                all_jobs.extend(jobs)
                log.info("[RemoteOK] %d jobs for %r", len(jobs), q)
            except Exception as e:
                log.warning("RemoteOK failed: %s", e)

        if source in ("all", "dice"):
            try:
                jobs = DiceScraper().search(q, max_results=limit)
                all_jobs.extend(jobs)
                log.info("[Dice] %d jobs for %r", len(jobs), q)
            except Exception as e:
                log.warning("Dice failed: %s", e)

        if source in ("all", "wwr"):
            try:
                jobs = WeWorkRemotelyScraper().search(q, max_results=limit)
                all_jobs.extend(jobs)
                log.info("[WWR] %d jobs for %r", len(jobs), q)
            except Exception as e:
                log.warning("WeWorkRemotely failed: %s", e)

        if source in ("all", "muse"):
            try:
                jobs = TheMuseScraper().search(q, max_results=limit)
                all_jobs.extend(jobs)
                log.info("[TheMuse] %d jobs for %r", len(jobs), q)
            except Exception as e:
                log.warning("TheMuse failed: %s", e)

        # Deduplicate
        seen: set = set()
        unique = []
        for j in all_jobs:
            key = getattr(j, "url", "") or f"{j.company}|{j.title}"
            if key not in seen:
                seen.add(key)
                unique.append(j)

        # Score if profile available
        scored = []
        try:
            from pipeline.job_matcher import JobMatcher
            from apply_today import _load_candidate_profile
            profile = _load_candidate_profile()
            matcher = JobMatcher()
            for j in unique:
                score = matcher.keyword_score(j, profile)
                scored.append((score, j))
            scored.sort(key=lambda x: x[0], reverse=True)
        except Exception as e:
            log.warning("Scoring unavailable: %s", e)
            scored = [(0, j) for j in unique]

        result = []
        for score, j in scored[:limit]:
            result.append({
                "title":       j.title or "",
                "company":     j.company or "",
                "location":    j.location or "",
                "url":         getattr(j, "url", "") or "",
                "apply_url":   getattr(j, "apply_url", "") or getattr(j, "url", "") or "",
                "source":      getattr(j, "source", "") or "",
                "description": (j.description or "")[:600],
                "posted_at":   str(getattr(j, "posted_at", "") or ""),
                "match_score": score,
            })

        return jsonify({"jobs": result, "total": len(result), "query": q})

    except Exception as e:
        log.error("jobs_search error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Prepare application (tailor resume + cover letter)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/jobs/prepare", methods=["POST"])
def prepare_job():
    """
    Tailor resume + generate cover letter for a selected job.
    Body: {title, company, url, description, source, match_score}
    Returns: {output_dir, resume_pdf, cover_letter, keywords, app_id}
    """
    body = request.get_json(force=True) or {}
    mode = body.get('mode', 'local')
    if mode not in ('local', 'ai'):
        return jsonify({'error': 'mode must be local or ai'}), 400
    title   = (body.get("title") or "").strip()
    company = (body.get("company") or "").strip()
    url     = (body.get("url") or body.get('apply_url') or "").strip()
    desc    = body.get("description", "")
    source  = body.get("source", "")
    try:
        score = int(body.get('match_score') or 0)
        if not 0 <= score <= 100:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return jsonify({'error': 'match_score must be a number between 0 and 100'}), 400

    if not title or not company:
        return jsonify({"error": "title and company are required"}), 400

    from pipeline.apify_scraper import JobListing
    job = JobListing(title=title, company=company)
    job.title       = title
    job.company     = company
    job.location    = body.get("location", "Remote")
    job.url         = url
    job.apply_url   = url
    job.source      = source
    job.description = desc
    job.salary_min  = None
    job.salary_max  = None
    job.job_type    = None
    job.posted_at   = None

    output_dir = ROOT / "output" / "applications" / _safe_name(company, title)
    output_dir.mkdir(parents=True, exist_ok=True)

    resume_pdf  = None
    cl_path     = None
    kw_list     = []

    # Try AI-tailored resume; fall back to master
    if mode == 'ai' and is_configured("resume"):
        try:
            from apply_today import _load_candidate_profile
            from pipeline.resume_generator import ResumeGenerator
            from pipeline.document_builder import build_pdf, build_docx, build_cover_letter_docx

            profile = _load_candidate_profile()
            gen = ResumeGenerator()
            tailored = gen.generate_resume(job, profile, match_score=score)
            cover_text = gen.generate_cover_letter(job, profile)

            pdf_path  = output_dir / "resume.pdf"
            docx_path = output_dir / "resume.docx"
            cl_docx   = output_dir / "cover_letter.docx"

            build_pdf(tailored, pdf_path)
            build_docx(tailored, docx_path)
            build_cover_letter_docx(cover_text, company, title,
                                    tailored.candidate_name, cl_docx)

            resume_pdf = str(pdf_path)
            cl_path    = str(cl_docx)
            kw_list    = tailored.ats_keywords_used
            log.info("AI-tailored resume ready: %s", pdf_path)
        except Exception as e:
            log.warning("AI tailoring failed (%s) — using master resume", e)

    if not resume_pdf:
        master = ROOT / "data" / "master_resume" / "resume.pdf"
        if master.exists():
            resume_pdf = str(master)
            log.info("Using master resume: %s", master)
        else:
            log.warning("No master resume found at %s", master)

    if not resume_pdf or not Path(resume_pdf).is_file():
        return jsonify({"error": "No resume could be prepared; configure a master resume or fix tailoring"}), 422

    # Track in DB
    app_id = None
    try:
        tracker = _tracker()
        app_id = tracker.add_application(
            job=job,
            match_score=score,
            resume_pdf_path=Path(resume_pdf) if resume_pdf else None,
            cover_letter_path=Path(cl_path) if cl_path else None,
            ats_keywords=kw_list,
        )
        log.info("Tracked application #%d: %s @ %s", app_id, title, company)
    except Exception as e:
        log.warning("DB tracking failed: %s", e)

    return jsonify({
        "ok":          True,
        "app_id":      app_id,
        "output_dir":  str(output_dir),
        "resume_pdf":  resume_pdf,
        "cover_letter": cl_path,
        "keywords":    kw_list,
        "ai_tailored": bool(mode == 'ai' and kw_list),
        "message":     (
            f"AI-tailored resume + cover letter ready for {title} @ {company}"
            if kw_list else
            f"Master resume prepared for {title} @ {company} in {mode} mode."
        ),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Profile  (profile.json  — personal + professional fields)
# ─────────────────────────────────────────────────────────────────────────────

PROFILE_PATH = ROOT / "submission" / "profile.json"
SAFE_PROFILE_KEYS = {
    "personal": ["first_name", "last_name", "city", "state", "country", "zip"],
    "professional": ["current_title", "years_experience", "desired_salary_usd",
                     "linkedin_url", "github_url", "portfolio_url"],
    "work_authorization": ["authorized_to_work_us", "require_sponsorship", "security_clearance"],
    "preferences": ["willing_to_relocate", "remote_preference", "available_start_date",
                    "notice_period_days"],
}

@app.route("/api/profile", methods=["GET"])
def get_profile():
    if not PROFILE_PATH.exists():
        return jsonify({k: {} for k in SAFE_PROFILE_KEYS})
    with open(PROFILE_PATH) as f:
        full = json.load(f)
    out = {}
    for section, fields in SAFE_PROFILE_KEYS.items():
        sec = full.get(section, {})
        out[section] = {k: sec.get(k) for k in fields}
    return jsonify(out)

@app.route("/api/profile", methods=["PUT"])
def save_profile():
    body = request.get_json(force=True) or {}
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH) as f:
            full = json.load(f)
    else:
        full = {}
    for section, fields in SAFE_PROFILE_KEYS.items():
        if section in body:
            if section not in full:
                full[section] = {}
            for k in fields:
                if k in body[section]:
                    full[section][k] = body[section][k]
    from submission.identity import current_profile
    full = current_profile(full)
    with open(PROFILE_PATH, "w") as f:
        json.dump(full, f, indent=2)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Config  (settings.yaml  — job search, matching, resume settings)
# ─────────────────────────────────────────────────────────────────────────────

SETTINGS_PATH = ROOT / "config" / "settings.yaml"

def _read_settings() -> dict:
    import yaml
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH) as f:
        return yaml.safe_load(f) or {}

def _write_settings(data: dict):
    import yaml
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

@app.route("/api/config", methods=["GET"])
def get_config():
    s = _read_settings()
    return jsonify({
        "queries":          s.get("job_search", {}).get("queries", []),
        "locations":        s.get("job_search", {}).get("locations", []),
        "sources":          s.get("job_search", {}).get("sources", []),
        "threshold":        s.get("matching", {}).get("threshold", 70),
        "use_claude":       s.get("matching", {}).get("use_claude_scoring", True),
        "cover_letter":     s.get("resume", {}).get("generate_cover_letter", True),
        "cover_letter_tone":s.get("resume", {}).get("cover_letter_tone", "professional"),
        "model":            s.get("resume", {}).get("model"),
        "ai":               s.get("ai", {}),
        "exclude_titles":   s.get("filters", {}).get("exclude_titles", []),
        "min_salary":       s.get("filters", {}).get("min_salary", 0),
        "greenhouse_companies": s.get("job_search", {}).get("greenhouse_companies", []),
        "lever_companies":  s.get("job_search", {}).get("lever_companies", []),
    })

@app.route("/api/config", methods=["PUT"])
def save_config():
    body = request.get_json(force=True) or {}
    s = _read_settings()
    if "ai" in body:
        from pipeline.llm import resolve
        try:
            ai = body["ai"]
            if not isinstance(ai, dict):
                raise ValueError("ai must be an object")
            for task in set(ai.get("routes", {})) | {"default"}:
                resolve(task, config=ai)
        except (ValueError, TypeError, AttributeError) as exc:
            return jsonify({"error": str(exc)}), 400
        s["ai"] = ai
    js = s.setdefault("job_search", {})
    mt = s.setdefault("matching", {})
    rv = s.setdefault("resume", {})
    fl = s.setdefault("filters", {})
    if "queries"   in body: js["queries"]   = [str(q).strip() for q in body["queries"] if str(q).strip()]
    if "locations" in body: js["locations"] = [str(l).strip() for l in body["locations"] if str(l).strip()]
    if "sources"   in body: js["sources"]   = body["sources"]
    if "greenhouse_companies" in body: js["greenhouse_companies"] = body["greenhouse_companies"]
    if "lever_companies"      in body: js["lever_companies"]      = body["lever_companies"]
    if "threshold"       in body: mt["threshold"]          = int(body["threshold"])
    if "use_claude"      in body: mt["use_claude_scoring"] = bool(body["use_claude"])
    if "cover_letter"    in body: rv["generate_cover_letter"] = bool(body["cover_letter"])
    if "cover_letter_tone" in body: rv["cover_letter_tone"] = body["cover_letter_tone"]
    if "model"           in body: rv["model"]              = body["model"]
    if "exclude_titles"  in body: fl["exclude_titles"]     = body["exclude_titles"]
    if "min_salary"      in body: fl["min_salary"]         = int(body["min_salary"])
    _write_settings(s)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# API Keys  (read/write .env  — never return values, only presence)
# ─────────────────────────────────────────────────────────────────────────────

ENV_PATH = ROOT / ".env"
ALLOWED_KEYS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "SERP_API_KEY"}

def _read_env_keys() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, _ = line.partition("=")
                env[k.strip()] = True
    # also check os.environ
    for k in ALLOWED_KEYS:
        if os.environ.get(k):
            env[k] = True
    return env

@app.route("/api/keys/status", methods=["GET"])
def keys_status():
    present = _read_env_keys()
    return jsonify({k: bool(present.get(k)) for k in ALLOWED_KEYS})

@app.route("/api/keys", methods=["PUT"])
def save_keys():
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict) or any(
        not isinstance(v, str) or "\n" in v or "\r" in v
        for k, v in body.items() if k in ALLOWED_KEYS
    ):
        return jsonify({"error": "key values must be single-line strings"}), 400
    updates = {k: body[k].strip() for k in ALLOWED_KEYS if k in body and body[k].strip()}
    lines = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.partition("=")[0].strip()
                if k in updates:
                    continue   # will re-add from body
            lines.append(line)
    for k, v in updates.items():
        lines.append(f"{k}={v}")
    import tempfile
    fd, temp_path = tempfile.mkstemp(dir=ENV_PATH.parent, prefix=".env-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(temp_path, ENV_PATH)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    os.environ.update(updates)
    return jsonify({"ok": True, "saved": list(updates)})


# ─────────────────────────────────────────────────────────────────────────────
# Multi-source job board  (Greenhouse + Lever + existing scrapers + sponsor)
# ─────────────────────────────────────────────────────────────────────────────

SPONSOR_KEYWORDS = [
    "h-1b", "h1b", "visa sponsor", "sponsorship available", "sponsor visa",
    "work authorization", "green card", "eb-2", "eb-3", "tn visa",
    "will sponsor", "open to sponsorship",
]

def _detect_sponsor(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in SPONSOR_KEYWORDS)

def _title_matches(title: str, query: str) -> bool:
    q_words = [w for w in query.lower().split() if len(w) > 2]
    t = title.lower()
    if not q_words:
        return True
    matched = sum(1 for w in q_words if w in t)
    # For 1–3 word queries require all words; for 4+ allow one miss
    required = len(q_words) if len(q_words) <= 3 else len(q_words) - 1
    return matched >= required

def _scrape_greenhouse(company: str, query: str) -> list:
    import urllib.request
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
        req = urllib.request.Request(url, headers={"User-Agent": "JobFlow/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        jobs = []
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if not _title_matches(title, query):
                continue
            loc_name = (j.get("location") or {}).get("name", "")
            desc = j.get("content", "")
            jobs.append({
                "title": title, "company": company.replace("-", " ").title(),
                "location": loc_name, "url": j.get("absolute_url", ""),
                "apply_url": j.get("absolute_url", ""), "source": "Greenhouse",
                "description": desc[:500], "posted_at": (j.get("updated_at") or "")[:10],
                "salary_range": "", "match_score": 0,
                "sponsor": _detect_sponsor(desc),
            })
        return jobs
    except Exception as e:
        log.debug("Greenhouse %s error: %s", company, e)
        return []

def _scrape_lever(company: str, query: str) -> list:
    import urllib.request
    try:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        req = urllib.request.Request(url, headers={"User-Agent": "JobFlow/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        jobs = []
        for j in data:
            title = j.get("text", "")
            if not _title_matches(title, query):
                continue
            loc = (j.get("categories") or {}).get("location", "")
            desc = " ".join((j.get("descriptionPlain") or "").split()[:200])
            jobs.append({
                "title": title, "company": company.replace("-", " ").title(),
                "location": loc, "url": j.get("hostedUrl", ""),
                "apply_url": j.get("applyUrl", ""), "source": "Lever",
                "description": desc[:500], "posted_at": "",
                "salary_range": "", "match_score": 0,
                "sponsor": _detect_sponsor(desc),
            })
        return jobs
    except Exception as e:
        log.debug("Lever %s error: %s", company, e)
        return []

def _scrape_dice_playwright(query: str, portal_id: int, max_results: int = 30) -> list:
    """Scrape Dice jobs using a logged-in Playwright session."""
    import time, urllib.parse
    from submission.apply_engine import PlaywrightEngine

    username, password = _portal_store().get_credentials(portal_id)
    engine = PlaywrightEngine(headless=True)
    engine.launch()
    jobs = []
    try:
        page = engine.page

        # Login to Dice
        page.goto("https://www.dice.com/dashboard/login", wait_until="domcontentloaded", timeout=20000)
        time.sleep(1)
        email_el = page.query_selector("input[name='email']")
        if email_el and email_el.is_visible():
            email_el.fill(username)
            time.sleep(0.3)
            page.query_selector("button[type='submit']").click()
            time.sleep(2)
        pw_el = page.query_selector("input[type='password']")
        if pw_el and pw_el.is_visible():
            pw_el.fill(password)
            time.sleep(0.3)
            page.query_selector("button[type='submit']").click()
            time.sleep(3)

        # Search
        encoded = urllib.parse.quote_plus(query)
        page.goto(
            f"https://www.dice.com/jobs?q={encoded}&countryCode=US&radius=30&radiusUnit=mi&page=1&pageSize=20&filters.isRemote=true&language=en",
            wait_until="domcontentloaded", timeout=20000
        )
        time.sleep(2)

        # Parse job cards — Dice uses data-testid="job-card"
        cards = page.query_selector_all("[data-testid='job-card']")
        seen_urls = set()
        for card in cards[:max_results]:
            try:
                title_el = card.query_selector("a[data-testid='job-search-job-detail-link']")
                comp_el  = card.query_selector("a[href*='/company-profile/']")
                loc_el   = card.query_selector("[data-testid='job-search-job-card-location'], [class*='location']")

                href = title_el.get_attribute("href") if title_el else ""
                if not href:
                    href_el = card.query_selector("a[href*='/job-detail/']")
                    href = href_el.get_attribute("href") if href_el else ""
                if not href:
                    continue
                url = href if href.startswith("http") else f"https://www.dice.com{href}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title    = title_el.get_attribute("aria-label") or (title_el.inner_text().strip() if title_el else "")
                company  = comp_el.inner_text().strip() if comp_el else ""
                # Fallback: company name from logo img alt
                if not company:
                    img = card.query_selector("img[alt]")
                    company = img.get_attribute("alt") if img else ""
                location = loc_el.inner_text().strip() if loc_el else ""

                jobs.append({
                    "title": title, "company": company, "location": location,
                    "url": url, "apply_url": url, "source": "Dice",
                    "description": "", "posted_at": "", "salary_range": "",
                    "match_score": 0, "sponsor": False, "easy_apply": True,
                })
            except Exception:
                pass
    finally:
        engine.close()
    return jobs


@app.route("/api/jobs/board", methods=["GET"])
def job_board():
    query    = request.args.get("q", "").strip() or "Data Engineer"
    sponsor  = request.args.get("sponsor") == "1"
    remote   = request.args.get("remote") == "1"
    limit    = min(int(request.args.get("limit", 60)), 200)
    settings = _read_settings()
    gh_cos   = settings.get("job_search", {}).get("greenhouse_companies", [])
    lv_cos   = settings.get("job_search", {}).get("lever_companies", [])

    results = []
    for co in gh_cos:
        results.extend(_scrape_greenhouse(co, query))
    for co in lv_cos:
        results.extend(_scrape_lever(co, query))

    # Dice — scrape via Playwright using stored portal credentials
    try:
        dice_portal = _portal_store().find_by_platform("dice")
        if dice_portal:
            dice_jobs = _scrape_dice_playwright(
                query, dice_portal["id"], max_results=30
            )
            results.extend(dice_jobs)
            log.info("Dice playwright: %d jobs", len(dice_jobs))
    except Exception as e:
        log.debug("Dice board error: %s", e)

    # Score with job matcher
    try:
        from pipeline.job_matcher import JobMatcher, CandidateProfile
        profile = CandidateProfile()
        matcher = JobMatcher(use_claude=False)
        for j in results:
            if not j.get("match_score"):
                desc = (j.get("title","") + " " + j.get("description",""))
                j["match_score"] = matcher.keyword_score(desc, profile)
    except Exception as e:
        log.debug("Board scoring error: %s", e)

    from pipeline.job_store import JobStore
    JobStore(ROOT / 'data/discovered_jobs.db').save(results)

    if sponsor:
        results = [j for j in results if j.get("sponsor")]
    if remote:
        results = [j for j in results if "remote" in (j.get("location","")).lower()
                   or not j.get("location")]

    # Deduplicate by url
    seen, deduped = set(), []
    for j in results:
        key = (j.get("url") or j.get("title","")) + (j.get("company",""))
        if key not in seen:
            seen.add(key)
            deduped.append(j)

    deduped.sort(key=lambda j: j.get("match_score", 0), reverse=True)
    return jsonify({"jobs": deduped, "total": len(deduped), 'saved': len(results)})


@app.route('/api/jobs/board/saved', methods=['GET'])
def saved_job_board():
    """Load persisted jobs without scraping, browser launch, or AI."""
    from pipeline.job_store import JobStore
    jobs = JobStore(ROOT / 'data/discovered_jobs.db').list()
    query = request.args.get('q', '').strip()
    if query:
        jobs = [job for job in jobs if _title_matches(job.get('title', ''), query)]
    if request.args.get('sponsor') == '1':
        jobs = [job for job in jobs if job.get('sponsor')]
    if request.args.get('remote') == '1':
        jobs = [job for job in jobs if 'remote' in (job.get('location') or '').lower()]
    return jsonify({'jobs': jobs, 'total': len(jobs), 'from_database': True})


# ─────────────────────────────────────────────────────────────────────────────
# Portal credential store
# ─────────────────────────────────────────────────────────────────────────────

_PORTAL_STORE = None
_PORTAL_STORE_LOCK = threading.Lock()

def _portal_store():
    global _PORTAL_STORE
    if _PORTAL_STORE is None:
        with _PORTAL_STORE_LOCK:
            if _PORTAL_STORE is None:
                from submission.portal_store import PortalStore
                _PORTAL_STORE = PortalStore(
                    db_path=ROOT / "data" / "applications.db",
                    key_path=ROOT / "data" / ".portal_key",
                )
    return _PORTAL_STORE


@app.route("/api/portals", methods=["GET"])
def list_portals():
    try:
        portals = _portal_store().list_portals()
        job_url = request.args.get('job_url')
        if job_url:
            from submission.portal_store import portal_origin
            from submission.identity import application_email
            try:
                origin = portal_origin(job_url)
            except ValueError:
                return jsonify({'error': 'A valid HTTPS job URL is required'}), 400
            email = application_email()
            eligible = []
            for portal in portals:
                try:
                    same_portal = portal_origin(portal['url']) == origin
                except ValueError:
                    continue
                if same_portal and (not email or portal['username'].casefold() == email.casefold()):
                    eligible.append(portal)
            portals = eligible
        return jsonify({"portals": portals})
    except Exception as e:
        log.error("list_portals: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/portals", methods=["POST"])
def create_portal():
    body = request.get_json(force=True) or {}
    name     = (body.get("name") or "").strip()
    url      = (body.get("url") or "").strip()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    platform = (body.get("platform") or "").strip() or None
    notes    = (body.get("notes") or "").strip() or None
    if not name or not url or not username or not password:
        return jsonify({"error": "name, url, username, password are required"}), 400
    try:
        pid = _portal_store().add_portal(name, url, username, password, platform, notes)
        return jsonify({"ok": True, "id": pid})
    except Exception as e:
        log.error("create_portal: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/portals/<int:portal_id>", methods=["PUT"])
def update_portal(portal_id):
    body = request.get_json(force=True) or {}
    kwargs = {}
    for k in ("name", "url", "username", "password", "notes"):
        if k in body and body[k] is not None:
            kwargs[k] = body[k]
    try:
        _portal_store().update_portal(portal_id, **kwargs)
        return jsonify({"ok": True})
    except Exception as e:
        log.error("update_portal: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/portals/<int:portal_id>", methods=["DELETE"])
def delete_portal(portal_id):
    try:
        _portal_store().delete_portal(portal_id)
        return jsonify({"ok": True})
    except Exception as e:
        log.error("delete_portal: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/portals/<int:portal_id>/test", methods=["POST"])
def test_portal(portal_id):
    """Test login — launches real browser, navigates to portal, tries to log in."""
    try:
        portal = _portal_store().get_portal(portal_id)
        if not portal:
            return jsonify({"ok": False, "error": "Portal not found"}), 404
        username, password = _portal_store().get_credentials(portal_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # Portal login test is never a dry_run — it only checks credentials, never submits

    try:
        from submission.apply_engine import PlaywrightEngine
        engine = PlaywrightEngine(headless=True)
        engine.launch()
        result = {"ok": False, "error": None, "platform": portal.get("platform")}
        try:
            import time as _time

            def _try_fill(sels, val):
                for s in sels:
                    try:
                        el = page.query_selector(s)
                        if el and el.is_visible():
                            el.fill(val)
                            return True
                    except Exception:
                        pass
                return False

            def _try_click(sels):
                for s in sels:
                    try:
                        el = page.query_selector(s)
                        if el and el.is_visible():
                            el.click()
                            return True
                    except Exception:
                        pass
                return False

            engine.navigate(portal["url"])
            page = engine.page
            _time.sleep(1)

            # Step 1: fill email
            _try_fill(["input[type='email']", "input[name='email']",
                       "input[name='username']", "#email"], username)
            _time.sleep(0.4)

            # Step 2: if no password visible yet, click Next/Continue first (two-step forms)
            pw_el = page.query_selector("input[type='password']")
            if not pw_el or not pw_el.is_visible():
                _try_click([
                    "button[type='submit']",
                    "button:has-text('Continue')",
                    "button:has-text('Next')",
                    "button:has-text('Sign in')",
                    "input[type='submit']",
                ])
                _time.sleep(2)

            # Step 3: fill password (now visible)
            _try_fill(["input[type='password']", "input[name='password']", "#password"], password)
            _time.sleep(0.4)

            # Step 4: submit
            _try_click(["button[type='submit']", "input[type='submit']",
                        "button:has-text('Sign in')", "button:has-text('Log in')"])
            _time.sleep(3)

            # Success heuristic: URL moved away from the original login URL
            current = engine.current_url().lower()
            original = portal["url"].lower()
            # Also check page text for error signals
            body_text = ""
            try:
                body_text = page.inner_text("body").lower()[:500]
            except Exception:
                pass
            error_signals = ["incorrect password", "invalid password", "wrong password",
                             "account not found", "no account", "try again"]
            if current != original and not any(s in body_text for s in error_signals):
                result["ok"] = True
                _portal_store().touch_last_used(portal_id)
            else:
                result["error"] = "Login failed — check credentials"
        finally:
            engine.close()
        return jsonify(result)
    except Exception as e:
        log.error("test_portal: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Background apply task system
# ─────────────────────────────────────────────────────────────────────────────

_apply_tasks: dict = {}
_apply_tasks_lock = threading.Lock()


def _new_task(job_info: dict) -> str:
    task_id = str(_uuid_mod.uuid4())[:8]
    with _apply_tasks_lock:
        _apply_tasks[task_id] = {
            "status": "queued",
            "steps":  [],
            "result": None,
            "error":  None,
            "job":    job_info,
        }
    return task_id


def _update_task(task_id: str, *, status=None, step=None,
                 result=None, error=None):
    import time as _time
    with _apply_tasks_lock:
        t = _apply_tasks.get(task_id)
        if not t:
            return
        if status is not None:
            t["status"] = status
        if step is not None:
            t["steps"].append({"step": step, "ts": _time.time()})
        if result is not None:
            t["result"] = result
        if error is not None:
            t["error"] = error


@app.route("/api/jobs/apply", methods=["POST"])
def apply_job():
    """Trigger a background Playwright apply flow."""
    body = request.get_json(force=True) or {}
    mode = body.get('mode', 'local')
    if mode not in ('local', 'ai'):
        return jsonify({'error': 'mode must be local or ai'}), 400
    job_url   = (body.get("url") or "").strip()
    portal_id = body.get("portal_id")
    headless  = False if mode == 'local' else body.get("headless", True)
    dry_run   = body.get("dry_run", None)
    job_title = body.get("title", "")
    company   = body.get("company", "")

    if not job_url:
        return jsonify({"error": "url required"}), 400

    parsed = urlsplit(job_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return jsonify({"error": "a public HTTPS job URL is required"}), 400
    if dry_run is not None and type(dry_run) is not bool:
        return jsonify({"error": "dry_run must be a boolean"}), 400
    dry_run = dry_run is not False
    if not dry_run and body.get("submission_approved") is not True:
        return jsonify({"error": "submission_approved=true is required for live submission"}), 400
    if portal_id and not dry_run:
        from submission.identity import application_email
        from submission.portal_store import portal_origin
        portal = _portal_store().get_portal(portal_id)
        if not portal or (application_email() and portal['username'].casefold() != application_email().casefold()):
            return jsonify({'error': 'Select a portal login using the current application email'}), 400
        try:
            if portal_origin(portal['url']) != portal_origin(job_url):
                return jsonify({'error': 'That login belongs to another portal. Choose No login or a matching portal account.'}), 400
        except ValueError:
            return jsonify({'error': 'Saved portal login URL is invalid'}), 400

    if dry_run:
        from apply_job import run_apply
        task_id = _new_task({"title": job_title, "company": company, "url": job_url})
        result = run_apply(url=job_url, dry_run=True)
        _update_task(task_id, status="done", result=result)
        return jsonify({"task_id": task_id, "status": "done"})

    task_id = _new_task({"title": job_title, "company": company, "url": job_url})
    login_event = threading.Event()
    with _apply_tasks_lock:
        _apply_tasks[task_id]['login_event'] = login_event

    def _wait_for_login():
        login_event.clear()
        _update_task(task_id, status='waiting_login', step='Complete login in the browser, then click Continue after login.')
        if not login_event.wait(600):
            raise TimeoutError('Portal login timed out; start again when ready')
        _update_task(task_id, status='running', step='Continuing in the same logged-in browser…')

    def _wait_for_form(missing):
        login_event.clear()
        labels = '; '.join(str(label)[:120] for label in missing[:8])
        _update_task(task_id, status='waiting_form', step='Complete required fields in the browser: ' + labels)
        if not login_event.wait(900):
            raise TimeoutError('Application form review timed out')
        _update_task(task_id, status='running', step='Checking completed form before submission…')

    def _run():
        _update_task(task_id, status="running", step="Starting apply flow…")
        try:
            extra_creds = {}
            if portal_id:
                try:
                    extra_creds = _portal_store().get_creds_as_env_dict(portal_id)
                    _update_task(task_id, step=f"Loaded credentials for portal #{portal_id}")
                except Exception as e:
                    _update_task(task_id, step=f"Warning: could not load portal creds: {e}")

            # Resolve resume + cover letter
            _update_task(task_id, step="Preparing resume…")
            try:
                from submission.apply_engine import ResumeAdapter, get_advisor
                adapter = ResumeAdapter(advisor=get_advisor() if mode == 'ai' else None)
                resume_pdf, cover_letter_pdf = adapter.prepare(
                    job_url=job_url, job_title=job_title, company=company,
                    on_step=lambda s: _update_task(task_id, step=s),
                )
            except Exception as e:
                log.warning("ResumeAdapter failed: %s", e)
                resume_pdf = str(ROOT / "data" / "master_resume" / "resume.pdf")
                cover_letter_pdf = None

            _profile_path = ROOT / "data" / "candidate_profile.json"
            if not _profile_path.exists():
                _profile_path = ROOT / "submission" / "profile.json"

            from submission.auto_apply import AutoApplier
            applier = AutoApplier(
                profile_path=str(_profile_path),
                credentials_path=str(ROOT / "submission" / ".secrets.env"),
                resume_pdf_path=resume_pdf,
                cover_letter_path=cover_letter_pdf,
                headless=False if portal_id else headless,
                dry_run=dry_run,
                submission_approved=body.get("submission_approved") is True,
                extra_creds=extra_creds,
                on_step=lambda s: _update_task(task_id, step=s),
                portal_id=portal_id,
                on_login_ready=_wait_for_login if portal_id else None,
                on_form_ready=_wait_for_form,
            )
            result = applier.apply(job_url=job_url)
            _update_task(task_id, status="done", result=result)

            if result.get("success") and portal_id:
                try:
                    _portal_store().touch_last_used(portal_id)
                except Exception:
                    pass

        except Exception as e:
            _update_task(task_id, status="failed", error=str(e))
            log.error("Apply task %s failed: %s", task_id, e, exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"task_id": task_id, "status": "queued"})


@app.route('/api/jobs/apply/<task_id>/continue', methods=['POST'])
def continue_apply_login(task_id):
    with _apply_tasks_lock:
        task = _apply_tasks.get(task_id)
        if not task or task.get('status') not in ('waiting_login', 'waiting_form'):
            return jsonify({'error': 'No browser step is waiting for this task'}), 409
        task['login_event'].set()
    return jsonify({'ok': True})


@app.route("/api/jobs/apply/<task_id>/status", methods=["GET"])
def apply_status(task_id):
    with _apply_tasks_lock:
        t = dict(_apply_tasks.get(task_id, {}))
    if not t:
        return jsonify({"error": "unknown task_id"}), 404
    return jsonify({
        "task_id": task_id,
        "status":  t.get("status"),
        "steps":   t.get("steps", []),
        "result":  t.get("result"),
        "error":   t.get("error"),
        "job":     t.get("job"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    log.info("JobFlow API  →  http://%s:%d", args.host, args.port)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("This local credential-bearing server must bind to loopback")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
