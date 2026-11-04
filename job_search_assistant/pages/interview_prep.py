import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.file_export import parse_resume, text_to_txt
from utils.openai_helper import generate_interview_questions


def render():
    st.markdown('<p class="page-title">🎤 Interview Prep</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Practice with AI-generated role-specific questions & answers</p>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        role = st.text_input("Role / Job Title", placeholder="e.g. Data Scientist")
        resume_file = st.file_uploader("Upload Resume (optional)", type=["pdf", "docx", "txt"])
    with col2:
        job_desc = st.text_area("Job Description", height=180,
                                placeholder="Paste the job description for tailored questions...")

    resume_text = ""
    if resume_file:
        try:
            resume_text = parse_resume(resume_file)
        except Exception:
            pass
    if not resume_text and st.session_state.get("resume_text"):
        resume_text = st.session_state.resume_text

    st.markdown("---")
    if st.button("🎯 Generate Interview Questions"):
        if not role:
            st.warning("Please enter a role.")
        else:
            with st.spinner("Generating interview questions..."):
                questions = generate_interview_questions(role, job_desc, resume_text)
                st.session_state.interview_questions = questions

    if "interview_questions" in st.session_state:
        questions = st.session_state.interview_questions
        st.markdown(f"### Interview Questions for **{role}**")

        full_text_parts = []
        for i, q in enumerate(questions, 1):
            question = q.get("question", "")
            answer = q.get("answer_framework", "")

            with st.expander(f"Q{i}: {question}"):
                st.markdown("**Suggested Answer Framework:**")
                st.markdown(answer)

            full_text_parts.append(f"Q{i}: {question}\n\nAnswer Framework:\n{answer}\n")

        full_text = "\n" + "─" * 60 + "\n\n".join(full_text_parts)
        st.download_button(
            "⬇ Download All Questions (TXT)",
            data=text_to_txt(full_text),
            file_name=f"interview_prep_{role.lower().replace(' ','_')}.txt",
            mime="text/plain",
        )
