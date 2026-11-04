# Job Application Agent - User Configuration
# Edit this file to customize your job search and profile

USER_PROFILE = {
    "name": "Your Name",
    "email": "your.email@example.com",
    "phone": "+1-XXX-XXX-XXXX",
    "location": "San Francisco, CA",
    "linkedin": "https://linkedin.com/in/yourprofile",
    "github": "https://github.com/yourusername",

    # Professional summary (used in email drafts)
    "summary": (
        "Experienced software engineer with 5+ years building scalable systems. "
        "Passionate about Python, cloud infrastructure, and data engineering."
    ),

    # Skills (used for job matching)
    "skills": [
        "Python", "SQL", "AWS", "Kubernetes", "Docker",
        "Apache Spark", "Kafka", "PostgreSQL", "FastAPI", "React"
    ],

    # Years of experience
    "years_experience": 5,
}

JOB_CRITERIA = {
    # What roles to search for
    "job_titles": [
        "Senior Software Engineer",
        "Data Engineer",
        "Backend Engineer",
        "Python Developer",
    ],

    # Target companies or job boards to scrape (can be URLs or company names)
    "job_sources": [
        "https://www.linkedin.com/jobs/search/?keywords=senior+python+engineer&location=San+Francisco",
        "https://news.ycombinator.com/jobs",
        "https://remoteok.com/remote-python-jobs",
    ],

    # Filter criteria
    "filters": {
        "min_salary": 150000,          # USD per year (0 = no minimum)
        "max_salary": 300000,          # USD per year (0 = no maximum)
        "locations": ["San Francisco", "Remote", "New York"],
        "job_types": ["full-time", "remote", "hybrid"],
        "exclude_keywords": ["junior", "intern", "entry-level"],
        "required_keywords": ["Python"],
        "min_match_score": 60,         # Minimum % match to include job (0-100)
    },

    # Max jobs to process end-to-end
    "max_jobs_to_scrape": 10,
    "max_emails_to_draft": 5,
}

EMAIL_CONFIG = {
    # Tone of application emails
    "tone": "professional",           # professional | casual | enthusiastic

    # Include these sections in emails
    "include_sections": [
        "greeting",
        "intro",
        "why_this_company",
        "relevant_experience",
        "call_to_action",
        "signature",
    ],

    # Output directory for drafted emails
    "output_dir": "./drafted_emails",
}
