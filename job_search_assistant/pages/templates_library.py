import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.file_export import text_to_txt, text_to_docx

TEMPLATES = {
    "Thank You Email (Post-Interview)": {
        "icon": "🙏",
        "description": "Send within 24 hours after an interview",
        "content": """Subject: Thank You – [Role] Interview

Dear [Hiring Manager Name],

Thank you for taking the time to speak with me about the [Role] position at [Company] yesterday. I truly enjoyed learning more about the team and the exciting work you're doing in [specific area discussed].

Our conversation reinforced my enthusiasm for this opportunity. I'm particularly excited about [specific project or aspect mentioned], and I believe my experience in [relevant skill] would allow me to contribute meaningfully from day one.

Please don't hesitate to reach out if you need any additional information. I look forward to the next steps.

Best regards,
[Your Name]
[Phone] | [Email] | [LinkedIn]
""",
    },
    "Follow-Up After Application": {
        "icon": "📬",
        "description": "Follow up 1–2 weeks after submitting an application",
        "content": """Subject: Following Up – [Role] Application

Dear [Recruiter/Hiring Manager Name],

I hope this message finds you well. I recently applied for the [Role] position at [Company] on [Date] and wanted to follow up to express my continued interest in the opportunity.

I'm very excited about the possibility of contributing to [Company]'s mission and believe my background in [key skill/experience] makes me a strong fit for this role.

I'd love the chance to discuss my application further at your convenience. Thank you for your time and consideration.

Best regards,
[Your Name]
""",
    },
    "Networking Message": {
        "icon": "🤝",
        "description": "Reach out to contacts at a target company",
        "content": """Subject: Connecting – [Mutual Connection / Shared Interest]

Hi [Name],

I hope you're doing well! My name is [Your Name] and I came across your profile while researching [Company]. I'm a [Your Title] with [X years] of experience in [field], and I'm deeply interested in [Company]'s work on [specific initiative].

I'd love to learn more about your experience at [Company] and any advice you might have for someone looking to join the team. Would you be open to a brief 15-minute chat or an exchange via email?

I really appreciate your time and look forward to connecting!

Best,
[Your Name]
[LinkedIn URL]
""",
    },
    "Salary Negotiation Email": {
        "icon": "💰",
        "description": "Negotiate a job offer professionally",
        "content": """Subject: Re: [Role] Offer – Discussion on Compensation

Dear [Hiring Manager / Recruiter Name],

Thank you so much for the offer to join [Company] as [Role]. I'm genuinely excited about this opportunity and the team.

After careful consideration, I'd like to discuss the base salary. Based on my research of market rates for this role in [Location/Remote], as well as my [X years] of experience in [key area], I was hoping we could explore a base salary closer to [$X].

I'm very enthusiastic about joining [Company] and confident I'll bring immediate value. I'm happy to discuss this further at your convenience.

Thank you again for the offer — I look forward to working together.

Best,
[Your Name]
""",
    },
    "Resignation Letter": {
        "icon": "📤",
        "description": "Professionally resign from your current position",
        "content": """[Your Name]
[Your Address]
[Date]

[Manager's Name]
[Company Name]
[Company Address]

Dear [Manager's Name],

I am writing to formally notify you of my resignation from my position as [Your Role] at [Company], effective [Last Day — typically 2 weeks from today].

This was not an easy decision. I have genuinely valued my time at [Company] and am grateful for the opportunities, support, and experiences I've gained here.

I am committed to ensuring a smooth transition and will do everything I can to complete outstanding projects and hand over my responsibilities before my departure.

Thank you for the opportunity to be part of the team.

Sincerely,
[Your Name]
""",
    },
}


def render():
    st.markdown('<p class="page-title">📚 Templates Library</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">One-click professional email & letter templates</p>', unsafe_allow_html=True)

    for title, data in TEMPLATES.items():
        with st.expander(f"{data['icon']} {title}  —  {data['description']}"):
            content = st.text_area(
                "Template (editable):",
                value=data["content"],
                height=300,
                key=f"tmpl_{title}",
            )
            col_copy, col_pdf, col_docx, col_txt = st.columns(4)
            with col_copy:
                if st.button("📋 Show to Copy", key=f"copy_{title}"):
                    st.code(content, language="")
            with col_pdf:
                from utils.file_export import text_to_pdf
                st.download_button(
                    "⬇ PDF",
                    data=text_to_pdf(content, title),
                    file_name=f"{title.lower().replace(' ','_')[:30]}.pdf",
                    mime="application/pdf",
                    key=f"pdf_{title}",
                )
            with col_docx:
                st.download_button(
                    "⬇ DOCX",
                    data=text_to_docx(content, title),
                    file_name=f"{title.lower().replace(' ','_')[:30]}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"docx_{title}",
                )
            with col_txt:
                st.download_button(
                    "⬇ TXT",
                    data=text_to_txt(content),
                    file_name=f"{title.lower().replace(' ','_')[:30]}.txt",
                    mime="text/plain",
                    key=f"txt_{title}",
                )
