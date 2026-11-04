import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.file_export import parse_resume, text_to_txt
from utils.openai_helper import generate_recruiter_email


def render():
    st.markdown('<p class="page-title">📧 Recruiter Email Generator</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Craft compelling outreach emails to get noticed</p>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        recruiter_name = st.text_input("Recruiter Name (optional)", placeholder="e.g. John Smith")
        company = st.text_input("Company", placeholder="e.g. Meta")
        role = st.text_input("Role", placeholder="e.g. Product Manager")
        linkedin_url = st.text_input("LinkedIn URL (optional)", placeholder="https://linkedin.com/in/yourprofile")

    with col2:
        tone = st.selectbox("Tone", ["Professional", "Friendly", "Confident", "Concise"])
        email_type = st.selectbox("Email Type", [
            "Cold Outreach",
            "Referral Request",
            "Follow-Up After Application",
            "Thank You After Interview",
            "Salary Negotiation",
        ])
        resume_file = st.file_uploader("Upload Resume (optional)", type=["pdf", "docx", "txt"])

    resume_text = ""
    if resume_file:
        try:
            resume_text = parse_resume(resume_file)
        except Exception:
            pass

    if not resume_text and st.session_state.get("resume_text"):
        resume_text = st.session_state.resume_text
        st.info("Using resume from Resume Optimizer page.")

    st.markdown("---")
    if st.button("📨 Generate Email"):
        if not company or not role:
            st.warning("Please enter Company and Role.")
        else:
            with st.spinner("Writing your email..."):
                result = generate_recruiter_email(
                    resume_text, recruiter_name, company,
                    role, linkedin_url, tone, email_type
                )
                st.session_state.email_result = result

    if "email_result" in st.session_state:
        result = st.session_state.email_result
        st.markdown("---")
        st.markdown("### Generated Email")

        subject = st.text_input("Subject Line", value=result.get("subject", ""), key="email_subject")
        body = st.text_area("Email Body", value=result.get("body", ""), height=380, key="email_body")

        st.session_state.email_result = {"subject": subject, "body": body}

        full_email = f"Subject: {subject}\n\n{body}"

        d1, d2 = st.columns(2)
        with d1:
            if st.button("📋 Copy to Clipboard"):
                st.code(full_email, language="")
                st.success("Email shown above — select all and copy!")
        with d2:
            st.download_button(
                "⬇ Download TXT",
                data=text_to_txt(full_email),
                file_name=f"email_{company.lower().replace(' ','_')}.txt",
                mime="text/plain",
            )
