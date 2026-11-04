import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.file_export import parse_resume, text_to_pdf, text_to_docx, text_to_txt
from utils.openai_helper import generate_cover_letter


def render():
    st.markdown('<p class="page-title">✉️ Cover Letter Generator</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Generate personalized cover letters in seconds</p>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        company = st.text_input("Company Name", placeholder="e.g. Google")
        role = st.text_input("Role / Position", placeholder="e.g. Software Engineer")
        hiring_manager = st.text_input("Hiring Manager Name (optional)", placeholder="e.g. Jane Smith")
        tone = st.selectbox("Tone", ["Professional", "Enthusiastic", "Concise", "Creative", "Formal"])

    with col2:
        job_desc = st.text_area("Job Description", height=200,
                                placeholder="Paste the job description here...")
        resume_file = st.file_uploader("Upload Resume (optional, for personalization)",
                                       type=["pdf", "docx", "txt"])

    resume_text = ""
    if resume_file:
        try:
            resume_text = parse_resume(resume_file)
        except Exception as e:
            st.warning(f"Could not read resume: {e}")

    # Fallback to session resume
    if not resume_text and st.session_state.get("resume_text"):
        resume_text = st.session_state.resume_text
        st.info("Using resume from Resume Optimizer page.")

    st.markdown("---")
    if st.button("🚀 Generate Cover Letter"):
        if not company or not role:
            st.warning("Please fill in Company and Role.")
        elif not job_desc and not resume_text:
            st.warning("Please paste a job description or upload your resume.")
        else:
            with st.spinner("Crafting your cover letter..."):
                letter = generate_cover_letter(
                    resume_text, company, role,
                    hiring_manager, job_desc, tone
                )
                st.session_state.cover_letter = letter

    if "cover_letter" in st.session_state:
        st.markdown("### Your Cover Letter")
        edited = st.text_area(
            "Edit your cover letter:",
            value=st.session_state.cover_letter,
            height=500,
            key="cover_letter_editor",
        )
        st.session_state.cover_letter = edited

        st.markdown("**Download**")
        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button(
                "⬇ Download PDF",
                data=text_to_pdf(edited, f"Cover Letter - {role} at {company}"),
                file_name=f"cover_letter_{company.lower().replace(' ','_')}.pdf",
                mime="application/pdf",
            )
        with d2:
            st.download_button(
                "⬇ Download DOCX",
                data=text_to_docx(edited, f"Cover Letter - {role} at {company}"),
                file_name=f"cover_letter_{company.lower().replace(' ','_')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        with d3:
            st.download_button(
                "⬇ Download TXT",
                data=text_to_txt(edited),
                file_name=f"cover_letter_{company.lower().replace(' ','_')}.txt",
                mime="text/plain",
            )
