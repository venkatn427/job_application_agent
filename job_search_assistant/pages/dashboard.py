import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.db import get_stats, get_all_applications, get_weekly_applications


def render():
    st.markdown('<p class="page-title">📊 Dashboard</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Your job search at a glance</p>', unsafe_allow_html=True)

    stats = get_stats()

    # ── Metric Cards ───────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Applications", stats["total"])
    c2.metric("Interviews", stats["interviews"])
    c3.metric("Pending", stats["pending"])
    c4.metric("Offers", stats["offers"])
    c5.metric("Rejections", stats["rejections"])

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # ── Status Donut Chart ──────────────────────────────
    with col_left:
        st.markdown("### Application Status")
        by_status = stats["by_status"]
        if by_status:
            labels = list(by_status.keys())
            values = list(by_status.values())
            colors = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#3b82f6", "#8b5cf6"]
            fig = go.Figure(data=[go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors[:len(labels)]),
                textfont_color="white",
            )])
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0",
                showlegend=True,
                legend=dict(font=dict(color="#e2e8f0")),
                margin=dict(t=10, b=10, l=10, r=10),
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No applications yet. Add some in the Job Tracker!")

    # ── Weekly Bar Chart ────────────────────────────────
    with col_right:
        st.markdown("### Applications Over Time")
        weekly = get_weekly_applications()
        if weekly:
            df = pd.DataFrame(weekly)
            df["date_applied"] = pd.to_datetime(df["date_applied"], errors="coerce")
            df = df.dropna(subset=["date_applied"]).sort_values("date_applied")
            fig = px.bar(
                df, x="date_applied", y="count",
                color_discrete_sequence=["#6366f1"],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0",
                xaxis=dict(gridcolor="#2d3748", title=""),
                yaxis=dict(gridcolor="#2d3748", title="Applications"),
                margin=dict(t=10, b=10, l=10, r=10),
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No date data yet. Add applications with dates!")

    st.markdown("---")

    # ── Recent Applications Table ───────────────────────
    st.markdown("### Recent Applications")
    apps = get_all_applications()
    if apps:
        df = pd.DataFrame(apps[:10])
        display_cols = ["company", "role", "date_applied", "status", "salary"]
        existing = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[existing].rename(columns={
                "company": "Company", "role": "Role",
                "date_applied": "Applied", "status": "Status", "salary": "Salary"
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No applications yet. Start tracking in the Job Tracker page!")
