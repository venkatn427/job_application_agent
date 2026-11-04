import streamlit as st
import pandas as pd
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.db import (
    get_all_applications, add_application,
    update_application, delete_application
)
from utils.file_export import applications_to_csv

STATUSES = ["Applied", "Screening", "Interview", "Take-Home", "Final Round", "Offer", "Rejected", "Withdrawn"]


def render():
    st.markdown('<p class="page-title">📋 Job Tracker</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Track every application from submission to offer</p>', unsafe_allow_html=True)

    tab_view, tab_add, tab_edit = st.tabs(["📂 All Applications", "➕ Add Application", "✏️ Edit / Delete"])

    # ── Tab: All Applications ───────────────────────────
    with tab_view:
        apps = get_all_applications()

        col_search, col_status, col_export = st.columns([3, 2, 1])
        with col_search:
            search = st.text_input("Search", placeholder="Company, role, recruiter...")
        with col_status:
            filter_status = st.selectbox("Filter by Status", ["All"] + STATUSES)
        with col_export:
            st.markdown("<br>", unsafe_allow_html=True)
            if apps:
                st.download_button(
                    "⬇ CSV",
                    data=applications_to_csv(apps),
                    file_name="applications.csv",
                    mime="text/csv",
                )

        if apps:
            df = pd.DataFrame(apps)
            if search:
                mask = df.apply(
                    lambda r: any(
                        search.lower() in str(v).lower()
                        for v in r.values
                    ), axis=1
                )
                df = df[mask]
            if filter_status != "All":
                df = df[df["status"] == filter_status]

            display_cols = ["id", "company", "role", "date_applied", "status",
                            "recruiter", "salary", "follow_up_date", "notes", "job_link"]
            existing = [c for c in display_cols if c in df.columns]
            st.dataframe(
                df[existing].rename(columns={
                    "id": "ID", "company": "Company", "role": "Role",
                    "date_applied": "Date Applied", "status": "Status",
                    "recruiter": "Recruiter", "salary": "Salary",
                    "follow_up_date": "Follow-Up", "notes": "Notes",
                    "job_link": "Link"
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"Showing {len(df)} of {len(apps)} applications")
        else:
            st.info("No applications yet. Use the Add tab to get started!")

    # ── Tab: Add Application ─────────────────────────────
    with tab_add:
        with st.form("add_form", clear_on_submit=True):
            a1, a2 = st.columns(2)
            with a1:
                company = st.text_input("Company *", placeholder="e.g. Stripe")
                role = st.text_input("Role *", placeholder="e.g. Backend Engineer")
                date_applied = st.date_input("Date Applied", value=date.today())
                status = st.selectbox("Status", STATUSES)
                recruiter = st.text_input("Recruiter Name")
            with a2:
                salary = st.text_input("Salary / Range", placeholder="e.g. $120k - $150k")
                follow_up = st.date_input("Follow-Up Date", value=None)
                job_link = st.text_input("Job Posting URL")
                notes = st.text_area("Notes", height=100, placeholder="Any additional notes...")

            submitted = st.form_submit_button("➕ Add Application")
            if submitted:
                if not company or not role:
                    st.error("Company and Role are required.")
                else:
                    add_application(
                        company=company,
                        role=role,
                        date_applied=str(date_applied),
                        status=status,
                        recruiter=recruiter,
                        salary=salary,
                        follow_up_date=str(follow_up) if follow_up else "",
                        notes=notes,
                        job_link=job_link,
                    )
                    st.success(f"Added: {company} — {role}")
                    st.rerun()

    # ── Tab: Edit / Delete ───────────────────────────────
    with tab_edit:
        apps = get_all_applications()
        if not apps:
            st.info("No applications to edit yet.")
        else:
            options = {f"#{a['id']} — {a['company']} ({a['role']})": a for a in apps}
            selected_label = st.selectbox("Select Application", list(options.keys()))
            app = options[selected_label]

            with st.form("edit_form"):
                e1, e2 = st.columns(2)
                with e1:
                    company = st.text_input("Company", value=app["company"])
                    role = st.text_input("Role", value=app["role"])
                    date_applied = st.text_input("Date Applied", value=app.get("date_applied", ""))
                    status = st.selectbox("Status", STATUSES,
                                         index=STATUSES.index(app["status"]) if app["status"] in STATUSES else 0)
                    recruiter = st.text_input("Recruiter", value=app.get("recruiter", "") or "")
                with e2:
                    salary = st.text_input("Salary", value=app.get("salary", "") or "")
                    follow_up = st.text_input("Follow-Up Date", value=app.get("follow_up_date", "") or "")
                    job_link = st.text_input("Job Link", value=app.get("job_link", "") or "")
                    notes = st.text_area("Notes", value=app.get("notes", "") or "", height=100)

                col_save, col_del = st.columns(2)
                with col_save:
                    if st.form_submit_button("💾 Save Changes"):
                        update_application(
                            app["id"], company, role, date_applied, status,
                            recruiter, salary, follow_up, notes, job_link
                        )
                        st.success("Updated successfully!")
                        st.rerun()
                with col_del:
                    if st.form_submit_button("🗑️ Delete Application", type="secondary"):
                        delete_application(app["id"])
                        st.success("Deleted.")
                        st.rerun()
