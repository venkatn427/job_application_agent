import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            date_applied TEXT,
            status TEXT DEFAULT 'Applied',
            recruiter TEXT,
            salary TEXT,
            follow_up_date TEXT,
            notes TEXT,
            job_link TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def add_application(company, role, date_applied, status, recruiter, salary,
                    follow_up_date, notes, job_link):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO applications
        (company, role, date_applied, status, recruiter, salary, follow_up_date, notes, job_link)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (company, role, date_applied, status, recruiter, salary,
          follow_up_date, notes, job_link))
    conn.commit()
    conn.close()


def get_all_applications():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM applications ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_application(app_id, company, role, date_applied, status, recruiter,
                       salary, follow_up_date, notes, job_link):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE applications SET
            company=?, role=?, date_applied=?, status=?, recruiter=?,
            salary=?, follow_up_date=?, notes=?, job_link=?
        WHERE id=?
    """, (company, role, date_applied, status, recruiter, salary,
          follow_up_date, notes, job_link, app_id))
    conn.commit()
    conn.close()


def delete_application(app_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM applications WHERE id=?", (app_id,))
    conn.commit()
    conn.close()


def get_stats():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) as count FROM applications GROUP BY status")
    rows = c.fetchall()
    conn.close()
    stats = {r["status"]: r["count"] for r in rows}
    total = sum(stats.values())
    return {
        "total": total,
        "interviews": stats.get("Interview", 0),
        "pending": stats.get("Applied", 0) + stats.get("Screening", 0),
        "offers": stats.get("Offer", 0),
        "rejections": stats.get("Rejected", 0),
        "by_status": stats,
    }


def get_weekly_applications():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT date_applied, COUNT(*) as count
        FROM applications
        WHERE date_applied IS NOT NULL
        GROUP BY date_applied
        ORDER BY date_applied DESC
        LIMIT 30
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


init_db()
