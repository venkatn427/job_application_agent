import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.file_export import parse_resume, text_to_pdf, text_to_docx, text_to_txt
from utils.openai_helper import analyze_ats_match, rewrite_resume, improve_bullets


def render():
    st.markdown('<p class="page-title">📄 Resume Optimizer</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Tailor your resume for any role and beat the ATS</p>', unsafe_allow_html=True)

    # ── Inputs ──────────────────────────────────────────
    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded = st.file_uploader("Upload Resume (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
        job_title = st.text_input("Job Title", placeholder="e.g. Senior Software Engineer")
        experience_level = st.selectbox("Experience Level",
                                        ["Entry Level", "Mid Level", "Senior Level", "Lead / Principal", "Executive"])
    with col2:
        job_desc = st.text_area("Job Description", height=220,
                                placeholder="Paste the full job description here...")

    # Parse resume
    resume_text = ""
    if uploaded:
        try:
            resume_text = parse_resume(uploaded)
            st.success(f"Resume loaded: {len(resume_text)} characters")
        except Exception as e:
            st.error(f"Could not parse resume: {e}")

    if "resume_text" not in st.session_state:
        st.session_state.resume_text = ""
    if resume_text:
        st.session_state.resume_text = resume_text

    # ── Action Buttons ───────────────────────────────────
    st.markdown("---")
    b1, b2, b3 = st.columns(3)

    with b1:
        if st.button("🎯 Analyze ATS Match"):
            if not st.session_state.resume_text:
                st.warning("Please upload a resume first.")
            elif not job_desc:
                st.warning("Please paste a job description.")
            else:
                with st.spinner("Analyzing ATS compatibility..."):
                    result = analyze_ats_match(st.session_state.resume_text, job_desc)
                    st.session_state.ats_result = result

    with b2:
        if st.button("✨ Rewrite Resume"):
            if not st.session_state.resume_text:
                st.warning("Please upload a resume first.")
            elif not job_desc:
                st.warning("Please paste a job description.")
            else:
                with st.spinner("Rewriting resume for this role..."):
                    rewritten = rewrite_resume(
                        st.session_state.resume_text, job_desc,
                        experience_level, job_title or "this role"
                    )
                    st.session_state.rewritten_resume = rewritten

    with b3:
        if st.button("💡 Improve Bullet Points"):
            if not st.session_state.resume_text:
                st.warning("Please upload a resume first.")
            else:
                with st.spinner("Improving bullet points..."):
                    improved = improve_bullets(st.session_state.resume_text)
                    st.session_state.rewritten_resume = improved

    st.markdown("---")

    # ── ATS Results ──────────────────────────────────────
    if "ats_result" in st.session_state:
        result = st.session_state.ats_result
        score = result.get("score", 0)

        st.markdown("### ATS Analysis Results")
        col_score, col_missing, col_present = st.columns([1, 2, 2])

        with col_score:
            css_class = "score-high" if score >= 70 else "score-mid" if score >= 45 else "score-low"
            st.markdown(
                f'<div class="card" style="text-align:center;">'
                f'<p style="color:#94a3b8;font-size:0.85rem;margin-bottom:0.5rem;">ATS MATCH SCORE</p>'
                f'<span class="score-badge {css_class}">{score}/100</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col_missing:
            st.markdown("**Missing Keywords**")
            missing = result.get("missing_keywords", [])
            if missing:
                tags = " ".join(
                    f'<span class="keyword-tag keyword-missing">{k}</span>'
                    for k in missing
                )
                st.markdown(tags, unsafe_allow_html=True)
            else:
                st.success("No critical missing keywords!")

        with col_present:
            st.markdown("**Present Keywords**")
            present = result.get("present_keywords", [])
            if present:
                tags = " ".join(
                    f'<span class="keyword-tag">{k}</span>'
                    for k in present[:20]
                )
                st.markdown(tags, unsafe_allow_html=True)

        suggestions = result.get("suggestions", [])
        if suggestions:
            st.markdown("**Suggestions**")
            for s in suggestions:
                st.markdown(f"- {s}")

    # ── Rewritten Resume ────────────────────────────────
    if "rewritten_resume" in st.session_state:
        st.markdown("### Optimized Resume")
        edited = st.text_area(
            "Edit your tailored resume below:",
            value=st.session_state.rewritten_resume,
            height=450,
            key="resume_editor",
        )
        st.session_state.rewritten_resume = edited

        st.markdown("**Download**")
        dl1, dl2, dl3 = st.columns(3)
        with dl1:
            st.download_button(
                "⬇ Download PDF",
                data=text_to_pdf(edited, "Resume"),
                file_name="resume_optimized.pdf",
                mime="application/pdf",
            )
        with dl2:
            st.download_button(
                "⬇ Download DOCX",
                data=text_to_docx(edited, "Resume"),
                file_name="resume_optimized.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        with dl3:
            st.download_button(
                "⬇ Download TXT",
                data=text_to_txt(edited),
                file_name="resume_optimized.txt",
                mime="text/plain",
            )
