import streamlit as st
import os
import sys

# ── Path setup ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Streamlit Page Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Job Search Assistant",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load Custom CSS ────────────────────────────────────────────────────────
css_path = os.path.join(os.path.dirname(__file__), "assets", "styles.css")
with open(css_path) as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── Load .env ──────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ── Page Imports ───────────────────────────────────────────────────────────
from pages import dashboard, resume_optimizer, cover_letter, recruiter_email
from pages import job_tracker, templates_library, interview_prep, job_search

# ── Sidebar Navigation ─────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div class="sidebar-logo"><h1>💼 JobAssist AI</h1>'
        '<p>Your AI-powered job search copilot</p></div>',
        unsafe_allow_html=True,
    )

    page = st.radio(
        "Navigate",
        options=[
            "📊 Dashboard",
            "🔍 Job Search",
            "📄 Resume Optimizer",
            "✉️ Cover Letter",
            "📧 Recruiter Email",
            "📋 Job Tracker",
            "🎤 Interview Prep",
            "📚 Templates Library",
        ],
        label_visibility="collapsed",
    )

    st.markdown("---")

    # Keys are supplied through the environment or the main settings dashboard.
    from pipeline.llm import resolve
    ai_settings = resolve("assistant")
    if ai_settings.api_key:
        st.success(f"✓ {ai_settings.provider} connected ({ai_settings.model})")
    else:
        st.warning("AI key not configured")
        st.caption("Set the provider key in .env and choose models in config/settings.yaml.")

    st.markdown("---")
    st.caption("Built with Streamlit + configurable AI")

# ── Route to Page ──────────────────────────────────────────────────────────
if page == "📊 Dashboard":
    dashboard.render()
elif page == "🔍 Job Search":
    job_search.render()
elif page == "📄 Resume Optimizer":
    resume_optimizer.render()
elif page == "✉️ Cover Letter":
    cover_letter.render()
elif page == "📧 Recruiter Email":
    recruiter_email.render()
elif page == "📋 Job Tracker":
    job_tracker.render()
elif page == "🎤 Interview Prep":
    interview_prep.render()
elif page == "📚 Templates Library":
    templates_library.render()
