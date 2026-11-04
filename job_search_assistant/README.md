# 💼 Job Search Assistant

An AI-powered web app to manage your entire job search — built with Streamlit and OpenAI.

## Features

| Page | What it does |
|------|-------------|
| **Dashboard** | Metrics cards + application status & timeline charts |
| **Resume Optimizer** | ATS analysis, resume rewriting, bullet improvement |
| **Cover Letter** | Personalized cover letters with tone selection |
| **Recruiter Email** | Cold outreach, follow-ups, referral requests |
| **Job Tracker** | Full CRUD application tracker with CSV export |
| **Interview Prep** | AI-generated role-specific Q&A |
| **Templates Library** | Thank-you, follow-up, networking, salary negotiation, resignation |

## Setup

### 1. Install dependencies

```bash
cd job_search_assistant
pip install -r requirements.txt
```

### 2. Add your OpenAI API key

Edit `.env`:

```
OPENAI_API_KEY=sk-your-key-here
```

Or enter it directly in the sidebar when the app is running.

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at **http://localhost:8501**

## Project Structure

```
job_search_assistant/
├── app.py                    # Main entry point + navigation
├── pages/
│   ├── dashboard.py
│   ├── resume_optimizer.py
│   ├── cover_letter.py
│   ├── recruiter_email.py
│   ├── job_tracker.py
│   ├── interview_prep.py
│   └── templates_library.py
├── utils/
│   ├── openai_helper.py      # All AI logic
│   └── file_export.py        # PDF, DOCX, TXT, CSV export + resume parsing
├── database/
│   └── db.py                 # SQLite CRUD operations
├── assets/
│   └── styles.css            # Premium dark UI styles
├── requirements.txt
└── .env
```

## Export Formats

- **PDF** — via fpdf2
- **DOCX** — via python-docx
- **TXT** — plain text
- **CSV** — job tracker export via pandas
