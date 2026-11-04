import streamlit as st
import requests
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.db import add_application


def _fetch_remotive(query: str, category: str) -> list:
    params = {}
    if query:
        params["search"] = query
    cat_map = {
        "Software Engineering": "software-dev",
        "Data Science": "data",
        "DevOps / SRE": "devops",
        "Product": "product",
        "Design": "design",
        "Marketing": "marketing",
        "Sales": "sales",
        "Customer Support": "customer-support",
        "Finance": "finance",
        "HR": "human-resources",
    }
    if category and category != "All" and category in cat_map:
        params["category"] = cat_map[category]
    try:
        r = requests.get("https://remotive.com/api/remote-jobs", params=params, timeout=10)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])[:50]
        return [
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "Remote"),
                "type": j.get("job_type", ""),
                "salary": j.get("salary", "Not listed"),
                "posted": (j.get("publication_date") or "")[:10],
                "url": j.get("url", ""),
                "tags": j.get("tags", []),
                "source": "Remotive",
            }
            for j in jobs
        ]
    except Exception:
        return []


def _fetch_arbeitnow(query: str, category: str) -> list:
    params = {"page": 1}
    if query:
        params["q"] = query
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api", params=params, timeout=10)
        r.raise_for_status()
        jobs = r.json().get("data", [])[:50]
        return [
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "type": "Remote" if j.get("remote") else (j.get("job_types") or [""])[0],
                "salary": "Not listed",
                "posted": (j.get("created_at") or "")[:10],
                "url": j.get("url", ""),
                "tags": j.get("tags", []),
                "source": "Arbeitnow",
            }
            for j in jobs
        ]
    except Exception:
        return []


def _fetch_themuse(query: str, category: str) -> list:
    params = {"page": 1, "descending": "true"}
    if query:
        params["name"] = query
    try:
        r = requests.get("https://www.themuse.com/api/public/jobs", params=params, timeout=10)
        r.raise_for_status()
        jobs = r.json().get("results", [])[:50]
        return [
            {
                "title": j.get("name", ""),
                "company": j.get("company", {}).get("name", ""),
                "location": ", ".join(
                    loc.get("name", "") for loc in j.get("locations", [])
                ) or "Remote",
                "type": j.get("type", ""),
                "salary": "Not listed",
                "posted": (j.get("publication_date") or "")[:10],
                "url": j.get("refs", {}).get("landing_page", ""),
                "tags": [c.get("name", "") for c in j.get("categories", [])],
                "source": "The Muse",
            }
            for j in jobs
        ]
    except Exception:
        return []


SOURCES = {
    "Remotive (Remote Tech)": _fetch_remotive,
    "Arbeitnow (Global)": _fetch_arbeitnow,
    "The Muse": _fetch_themuse,
}


def render():
    st.markdown('<p class="page-title">🔍 Job Search</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Live job listings from multiple sources — no API key needed</p>',
        unsafe_allow_html=True,
    )

    # ── Search Controls ──────────────────────────────────
    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        query = st.text_input(
            "Search keywords",
            placeholder="e.g. Python engineer, product manager, data scientist...",
        )
    with col2:
        category = st.selectbox("Category", [
            "All", "Software Engineering", "Data Science", "DevOps / SRE",
            "Product", "Design", "Marketing", "Sales", "Customer Support",
            "Finance", "HR",
        ])
    with col3:
        source_name = st.selectbox("Source", list(SOURCES.keys()))

    search_col, _ = st.columns([1, 5])
    with search_col:
        search_clicked = st.button("🔍 Search Jobs")

    if search_clicked:
        with st.spinner(f"Fetching live jobs from {source_name}..."):
            jobs = SOURCES[source_name](query, category)
            st.session_state.job_results = jobs
            st.session_state.job_source = source_name

    results = st.session_state.get("job_results")

    if results is None:
        st.info("Enter keywords above and click **Search Jobs** to fetch live listings.")
        _show_quick_searches(source_name)
        return

    if not results:
        st.warning("No results found. Try different keywords or switch the source.")
        _show_quick_searches(source_name)
        return

    used_source = st.session_state.get("job_source", source_name)
    st.markdown(f"**{len(results)} positions found** from {used_source}")
    st.markdown("---")

    for i, job in enumerate(results):
        _render_job_card(job, i)


def _render_job_card(job: dict, idx: int):
    col_info, col_btn = st.columns([6, 1])

    tags_html = " ".join(
        f'<span class="keyword-tag">{t}</span>'
        for t in (job.get("tags") or [])[:5]
    )

    with col_info:
        st.markdown(
            f"""
            <div class="card" style="padding:0.9rem;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                  <span style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">{job['title']}</span>
                  <span style="color:#94a3b8;margin-left:0.5rem;font-size:0.9rem;">@ {job['company']}</span>
                </div>
                <span style="background:rgba(99,102,241,0.15);color:#a78bfa;border-radius:20px;
                             padding:2px 10px;font-size:0.78rem;white-space:nowrap;">
                  {job.get('source','')}
                </span>
              </div>
              <div style="color:#64748b;font-size:0.83rem;margin-top:0.3rem;">
                📍 {job.get('location','Remote')} &nbsp;|&nbsp;
                💼 {job.get('type','')} &nbsp;|&nbsp;
                💰 {job.get('salary','Not listed')} &nbsp;|&nbsp;
                📅 {job.get('posted','')}
              </div>
              <div style="margin-top:0.4rem;">{tags_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if job.get("url"):
            st.markdown(
                f'<a href="{job["url"]}" target="_blank" style="text-decoration:none;">'
                f'<div style="background:rgba(99,102,241,0.2);color:#a78bfa;'
                f'border:1px solid rgba(99,102,241,0.4);border-radius:8px;'
                f'padding:0.35rem 0.5rem;text-align:center;font-size:0.82rem;cursor:pointer;">'
                f'View →</div></a>',
                unsafe_allow_html=True,
            )
        if st.button("➕ Track", key=f"track_{idx}"):
            add_application(
                company=job["company"],
                role=job["title"],
                date_applied=str(date.today()),
                status="Applied",
                recruiter="",
                salary=job.get("salary", ""),
                follow_up_date="",
                notes=f"Source: {job.get('source','')}",
                job_link=job.get("url", ""),
            )
            st.success("Added to tracker!")


def _show_quick_searches(source_name: str):
    st.markdown("### Quick Searches")
    quick = [
        "Python engineer", "React developer", "Data scientist",
        "Product manager", "DevOps engineer", "ML engineer",
        "Full stack developer", "UX designer", "Backend engineer",
    ]
    cols = st.columns(3)
    for i, term in enumerate(quick):
        with cols[i % 3]:
            if st.button(term, key=f"quick_{i}"):
                with st.spinner(f"Searching for {term}..."):
                    jobs = SOURCES[source_name](term, "All")
                    st.session_state.job_results = jobs
                    st.session_state.job_source = source_name
                st.rerun()
