"""
Pipeline Test Runner
---------------------
Tests each component without needing API keys:
  1. Free job scrapers (Greenhouse, Lever, RemoteOK) — no Apify token required
  2. Job matching (keyword-only mode)
  3. PDF + DOCX document generation
  4. Google Sheets CSV fallback
  5. Notification dry-run

Components that need API keys are tested with a SKIP notice.
"""

import sys
import os
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PASS = "✅"
FAIL = "❌"
SKIP = "⏭️ "
INFO = "ℹ️ "

results = []

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def check(label, fn):
    try:
        val = fn()
        print(f"  {PASS}  {label}: {val}")
        results.append((label, True, None))
        return True
    except Exception as e:
        print(f"  {FAIL}  {label}: {e}")
        results.append((label, False, str(e)))
        return False

def skip(label, reason):
    print(f"  {SKIP}  {label} — {reason}")
    results.append((label, None, reason))

# ─────────────────────────────────────────────────────────────────────────────
# 1. Imports
# ─────────────────────────────────────────────────────────────────────────────
section("1. Module Imports")

def _import_pipeline():
    from pipeline.apify_scraper import JobFetcher, JobListing
    from pipeline.profile_analyzer import CandidateProfile, ProfileAnalyzer
    from pipeline.job_matcher import JobMatcher, extract_jd_keywords
    from pipeline.resume_generator import ResumeGenerator, TailoredResume
    from pipeline.document_builder import build_pdf, build_docx
    from pipeline.drive_manager import DriveManager
    from pipeline.sheets_tracker import SheetsTracker, ApplicationRow
    from pipeline.notifier import Notifier, PipelineRunSummary
    from pipeline.workflow import Pipeline, PipelineConfig, PipelineDB
    return "all pipeline modules loaded"

check("Pipeline modules", _import_pipeline)

def _import_agents():
    from agents.profile_agent import run_profile_agent, _parse_json
    from agents.matching_agent import run_matching_agent, _parse_json
    from agents.resume_agent import run_resume_agent, _compact_profile
    return "all agent modules loaded"

check("Agent modules", _import_agents)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Free scraper — Greenhouse API
# ─────────────────────────────────────────────────────────────────────────────
section("2. Job Scrapers (Free APIs — no Apify token)")

def _greenhouse():
    from pipeline.apify_scraper import GreenhouseScraper
    scraper = GreenhouseScraper()
    jobs = scraper.search(["stripe"], query="engineer", max_results=3)
    assert isinstance(jobs, list), "Expected list"
    return f"{len(jobs)} jobs from Stripe Greenhouse"

check("Greenhouse API (Stripe)", _greenhouse)

def _lever():
    from pipeline.apify_scraper import LeverScraper
    scraper = LeverScraper()
    jobs = scraper.search(["netflix"], query="engineer", max_results=3)
    assert isinstance(jobs, list), "Expected list"
    return f"{len(jobs)} jobs from Netflix Lever"

check("Lever API (Netflix)", _lever)

def _remoteok():
    # Test the existing RemoteOK scraper
    sys.path.insert(0, str(ROOT))
    try:
        from scraper import RemoteOKScraper
        scraper = RemoteOKScraper()
        jobs = scraper.search("python", max_results=3)
        return f"{len(jobs)} jobs from RemoteOK"
    except Exception as e:
        return f"RemoteOK (legacy): {e}"

check("RemoteOK API", _remoteok)

# Apify — requires token
apify_token = os.environ.get("APIFY_API_TOKEN", "")
if apify_token:
    def _apify_linkedin():
        from pipeline.apify_scraper import JobFetcher
        fetcher = JobFetcher(apify_token)
        jobs = fetcher.linkedin.search("Python Engineer", "Remote", 5)
        return f"{len(jobs)} jobs via Apify LinkedIn"
    check("Apify LinkedIn", _apify_linkedin)
else:
    skip("Apify LinkedIn/Indeed", "APIFY_API_TOKEN not set — add to .env to enable")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Profile Analyzer — mock profile (no Claude needed)
# ─────────────────────────────────────────────────────────────────────────────
section("3. Candidate Profile")

MOCK_PROFILE_DICT = {
    "name": "Alex Johnson",
    "email": "alex@example.com",
    "phone": "+1-415-555-0100",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/alexj",
    "github_url": "https://github.com/alexj",
    "years_experience": 7.0,
    "current_title": "Senior Software Engineer",
    "target_titles": ["Staff Engineer", "Senior Data Engineer", "Principal Engineer"],
    "professional_summary": "Senior engineer with 7 years building distributed systems.",
    "skills": ["Python", "SQL", "System Design", "Leadership"],
    "technologies": ["Apache Kafka", "Kubernetes", "Docker", "PostgreSQL", "Redis", "Apache Spark"],
    "programming_languages": ["Python", "Go", "TypeScript"],
    "tools": ["Git", "Terraform", "Datadog", "Airflow"],
    "cloud_platforms": ["AWS", "GCP"],
    "work_experience": [
        {
            "company": "TechCorp",
            "title": "Senior Software Engineer",
            "start_date": "2021-01",
            "end_date": "Present",
            "location": "San Francisco, CA",
            "bullets": [
                "Built real-time data pipeline processing 500M events/day using Kafka and Spark, reducing latency by 65%",
                "Led Kubernetes migration cutting infra costs by $1.2M/year and improving uptime to 99.99%",
                "Mentored 4 junior engineers; 3 promoted within 18 months",
            ],
            "technologies": ["Python", "Apache Kafka", "Apache Spark", "Kubernetes", "AWS"],
        },
        {
            "company": "DataStartup",
            "title": "Software Engineer",
            "start_date": "2018-06",
            "end_date": "2020-12",
            "location": "New York, NY",
            "bullets": [
                "Designed ETL pipelines ingesting 50TB/day from 20+ sources using Airflow and dbt",
                "Reduced analytics query time by 80% via Redshift optimization",
            ],
            "technologies": ["Python", "dbt", "AWS Redshift", "Airflow", "PostgreSQL"],
        },
    ],
    "education": [
        {"institution": "UC Berkeley", "degree": "B.S. Computer Science",
         "field_of_study": "Computer Science", "graduation_year": "2017", "gpa": "3.8"}
    ],
    "certifications": [
        {"name": "AWS Solutions Architect – Professional", "issuer": "AWS", "date": "2022"}
    ],
    "domain_expertise": ["Data Engineering", "Distributed Systems", "Platform Engineering"],
    "industry_keywords": ["microservices", "event-driven architecture", "stream processing", "MLOps"],
    "achievements": [
        "Processed 500M events/day with 99.99% reliability",
        "Saved $1.2M/year in cloud costs",
        "Reduced p99 latency from 800ms to 45ms",
    ],
    "raw_resume_text": "",
}

def _build_profile():
    from pipeline.profile_analyzer import ProfileAnalyzer
    analyzer = ProfileAnalyzer.__new__(ProfileAnalyzer)
    profile = analyzer._dict_to_profile(MOCK_PROFILE_DICT)
    assert profile.name == "Alex Johnson"
    assert len(profile.work_experience) == 2
    assert profile.years_experience == 7.0
    return f"Profile: {profile.name}, {profile.years_experience:.0f}y, {len(profile.skills_flat())} skills"

check("Build profile from dict", _build_profile)

resume_file = ROOT / "data" / "master_resume" / "resume.pdf"
if resume_file.exists():
    def _extract_resume():
        from pipeline.profile_analyzer import extract_text
        text = extract_text(resume_file)
        return f"Extracted {len(text)} chars from resume"
    check("Extract PDF resume text", _extract_resume)
else:
    skip("Extract PDF resume", f"No file at {resume_file} — add your resume to test this")

anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
if anthropic_key:
    def _parse_with_claude():
        from pipeline.profile_analyzer import ProfileAnalyzer
        analyzer = ProfileAnalyzer(api_key=anthropic_key, cache_dir=ROOT / "data")
        text = MOCK_PROFILE_DICT.get("raw_resume_text") or json.dumps(MOCK_PROFILE_DICT)
        profile = analyzer._parse_with_claude(text[:2000])
        return f"Claude parse: got name={profile.name!r}"
    check("Claude profile parsing", _parse_with_claude)
else:
    skip("Claude profile parsing", "ANTHROPIC_API_KEY not set")

# ─────────────────────────────────────────────────────────────────────────────
# 4. Job Matching
# ─────────────────────────────────────────────────────────────────────────────
section("4. Job Matching (keyword-only, no Claude)")

MOCK_JOBS = [
    {
        "title": "Senior Data Engineer",
        "company": "Stripe",
        "location": "Remote",
        "job_type": "full-time",
        "description": (
            "We need a Senior Data Engineer with 5+ years experience in Python, Apache Kafka, "
            "Spark, and AWS. You'll build real-time streaming pipelines. Kubernetes is a plus. "
            "Domain knowledge in fintech/payments preferred. Must have strong SQL skills."
        ),
        "requirements": ["Python", "Kafka", "Apache Spark", "AWS", "SQL", "Kubernetes"],
        "apply_url": "https://stripe.com/jobs/1",
        "source": "greenhouse",
        "job_id": "mock_001",
    },
    {
        "title": "Junior Frontend Developer",
        "company": "SomeStartup",
        "location": "Austin, TX",
        "job_type": "full-time",
        "description": "1 year experience with React and HTML. Entry-level position.",
        "requirements": ["React", "HTML", "CSS", "JavaScript"],
        "apply_url": "https://example.com/jobs/2",
        "source": "lever",
        "job_id": "mock_002",
    },
    {
        "title": "Staff Platform Engineer",
        "company": "Databricks",
        "location": "Remote",
        "job_type": "remote",
        "description": (
            "Staff Engineer to lead platform infrastructure. 8+ years with Python, Kubernetes, "
            "Terraform, Kafka, and AWS. Spark and data engineering background a strong plus. "
            "Design distributed systems at scale."
        ),
        "requirements": ["Python", "Kubernetes", "Terraform", "AWS", "Kafka", "Apache Spark"],
        "apply_url": "https://databricks.com/jobs/3",
        "source": "greenhouse",
        "job_id": "mock_003",
    },
]

def _score_jobs():
    from pipeline.apify_scraper import JobListing
    from pipeline.profile_analyzer import ProfileAnalyzer
    from pipeline.job_matcher import JobMatcher

    jobs = [JobListing(**{k: v for k, v in j.items() if k in JobListing.__dataclass_fields__}) for j in MOCK_JOBS]
    profile = ProfileAnalyzer._dict_to_profile(MOCK_PROFILE_DICT)
    matcher = JobMatcher(threshold=70, use_claude=False)
    results = matcher.score_batch(jobs, profile)

    lines = []
    for r in results:
        tag = "✓ MATCH" if r.recommended else "✗ SKIP "
        lines.append(f"    [{r.score:3d}/100] {tag} — {r.job.title} @ {r.job.company}")
    return "\n" + "\n".join(lines)

check("Keyword scoring (3 jobs)", _score_jobs)

def _keyword_extract():
    from pipeline.job_matcher import extract_jd_keywords
    jd = "We need Python, Kafka, Kubernetes (k8s), and AWS experience. PostgreSQL is required."
    kws = extract_jd_keywords(jd)
    return f"Extracted: {sorted(kws)}"

check("JD keyword extraction", _keyword_extract)

if anthropic_key:
    def _claude_scoring():
        from pipeline.apify_scraper import JobListing
        from pipeline.profile_analyzer import ProfileAnalyzer
        from pipeline.job_matcher import JobMatcher
        job = JobListing(**{k: v for k, v in MOCK_JOBS[0].items() if k in JobListing.__dataclass_fields__})
        profile = ProfileAnalyzer._dict_to_profile(MOCK_PROFILE_DICT)
        matcher = JobMatcher(threshold=70, use_claude=True, api_key=anthropic_key)
        bonus, topics, gaps = matcher._claude_analysis(job, profile)
        return f"Claude bonus={bonus}/10, {len(topics)} interview topics, {len(gaps)} gaps"
    check("Claude scoring bonus", _claude_scoring)
else:
    skip("Claude scoring bonus", "ANTHROPIC_API_KEY not set")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Resume Generation
# ─────────────────────────────────────────────────────────────────────────────
section("5. Resume Generation")

if anthropic_key:
    def _generate_resume():
        from pipeline.apify_scraper import JobListing
        from pipeline.profile_analyzer import ProfileAnalyzer
        from pipeline.resume_generator import ResumeGenerator
        job = JobListing(**{k: v for k, v in MOCK_JOBS[0].items() if k in JobListing.__dataclass_fields__})
        profile = ProfileAnalyzer._dict_to_profile(MOCK_PROFILE_DICT)
        gen = ResumeGenerator(api_key=anthropic_key)
        resume = gen.generate_resume(job, profile, match_score=88)
        assert resume.professional_summary, "Empty summary"
        return (f"Summary: {resume.professional_summary[:80]}... | "
                f"{len(resume.experience_bullets)} exp sections | "
                f"{len(resume.ats_keywords_used)} ATS keywords")
    check("Claude resume generation", _generate_resume)

    def _generate_cover_letter():
        from pipeline.apify_scraper import JobListing
        from pipeline.profile_analyzer import ProfileAnalyzer
        from pipeline.resume_generator import ResumeGenerator
        job = JobListing(**{k: v for k, v in MOCK_JOBS[0].items() if k in JobListing.__dataclass_fields__})
        profile = ProfileAnalyzer._dict_to_profile(MOCK_PROFILE_DICT)
        gen = ResumeGenerator(api_key=anthropic_key)
        cl = gen.generate_cover_letter(job, profile)
        return f"{len(cl)} chars, first 80: {cl[:80].strip()}..."
    check("Claude cover letter", _generate_cover_letter)
else:
    skip("Claude resume generation", "ANTHROPIC_API_KEY not set")
    skip("Claude cover letter", "ANTHROPIC_API_KEY not set")

# ─────────────────────────────────────────────────────────────────────────────
# 6. Document Building (PDF + DOCX) — no API key needed
# ─────────────────────────────────────────────────────────────────────────────
section("6. Document Building (PDF + DOCX)")

from pipeline.apify_scraper import JobListing
from pipeline.resume_generator import TailoredResume

MOCK_JOB = JobListing(
    title="Senior Data Engineer",
    company="Stripe",
    location="Remote",
    apply_url="https://stripe.com/jobs/test",
    source="greenhouse",
)

MOCK_RESUME = TailoredResume(
    job=MOCK_JOB,
    candidate_name="Alex Johnson",
    candidate_email="alex@example.com",
    candidate_phone="+1-415-555-0100",
    candidate_location="San Francisco, CA",
    linkedin_url="https://linkedin.com/in/alexj",
    github_url="https://github.com/alexj",
    professional_summary=(
        "Senior Data Engineer with 7 years building real-time data platforms at scale. "
        "Expert in Python, Apache Kafka, Spark, and AWS. Proven track record of processing "
        "500M+ events/day with 99.99% uptime."
    ),
    skills_section=(
        "Languages: Python, Go, TypeScript, SQL | "
        "Data: Apache Kafka, Apache Spark, dbt, Airflow, PostgreSQL, Redis | "
        "Cloud: AWS (EMR, Redshift, S3, Lambda), GCP | "
        "Infra: Kubernetes, Terraform, Docker | "
        "Tools: Git, Datadog, Grafana"
    ),
    experience_bullets={
        "TechCorp | Senior Software Engineer | Jan 2021 – Present": [
            "Built real-time streaming pipeline processing 500M events/day using Kafka and Spark, reducing ingestion latency by 65%",
            "Led Kubernetes migration cutting infrastructure costs by $1.2M/year; achieved 99.99% uptime SLA",
            "Architected event-driven microservices on AWS serving 10M daily active users",
            "Mentored 4 junior engineers, 3 of whom were promoted within 18 months",
        ],
        "DataStartup | Software Engineer | Jun 2018 – Dec 2020": [
            "Designed ETL pipelines ingesting 50TB/day from 20+ data sources using Airflow and dbt",
            "Reduced analytics query time by 80% through Redshift optimization and query refactoring",
            "Built self-serve data catalog adopted by 150+ internal stakeholders",
        ],
    },
    education_section="B.S. Computer Science — UC Berkeley, 2017 | GPA: 3.8/4.0",
    certifications_section="AWS Solutions Architect – Professional (2022)",
    ats_keywords_used=["Python", "Apache Kafka", "Kubernetes", "AWS", "Spark", "SQL", "dbt"],
    tailoring_notes="Emphasized streaming/Kafka experience and fintech-relevant scale metrics.",
    match_score=88,
)

out_dir = ROOT / "output" / "test_run"
out_dir.mkdir(parents=True, exist_ok=True)

def _build_pdf():
    from pipeline.document_builder import build_pdf
    pdf_path = out_dir / "test_resume.pdf"
    build_pdf(MOCK_RESUME, pdf_path)
    size_kb = pdf_path.stat().st_size // 1024
    return f"PDF: {pdf_path} ({size_kb} KB)"

check("Build PDF resume", _build_pdf)

def _build_docx():
    from pipeline.document_builder import build_docx
    docx_path = out_dir / "test_resume.docx"
    build_docx(MOCK_RESUME, docx_path)
    size_kb = docx_path.stat().st_size // 1024
    return f"DOCX: {docx_path} ({size_kb} KB)"

check("Build DOCX resume", _build_docx)

def _build_cover_letter_docx():
    from pipeline.document_builder import build_cover_letter_docx
    cover_text = (
        "Dear Hiring Manager,\n\n"
        "I am excited to apply for the Senior Data Engineer position at Stripe. "
        "With 7 years of experience building high-throughput data pipelines processing "
        "500M+ events/day using Kafka and Apache Spark, I am confident I can contribute "
        "meaningfully to Stripe's data infrastructure.\n\n"
        "My most recent work at TechCorp involved architecting an event-driven platform "
        "that reduced processing latency by 65% while cutting infrastructure costs by $1.2M/year. "
        "This aligns directly with Stripe's mission of building reliable financial infrastructure at scale.\n\n"
        "I would love to discuss how my background fits Stripe's data engineering goals.\n\n"
        "Sincerely,\nAlex Johnson"
    )
    cl_path = out_dir / "test_cover_letter.docx"
    build_cover_letter_docx(cover_text, "Stripe", "Senior Data Engineer", "Alex Johnson", cl_path)
    return f"Cover letter: {cl_path}"

check("Build cover letter DOCX", _build_cover_letter_docx)

# ─────────────────────────────────────────────────────────────────────────────
# 7. Tracking + Logging
# ─────────────────────────────────────────────────────────────────────────────
section("7. Application Tracker (CSV fallback)")

def _tracker_csv():
    from pipeline.sheets_tracker import SheetsTracker, ApplicationRow
    tracker = SheetsTracker(local_csv_path=ROOT / "logs" / "test_applications.csv")
    row = ApplicationRow(
        company="Stripe",
        job_title="Senior Data Engineer",
        job_url="https://stripe.com/jobs/test",
        match_score=88,
        resume_link="output/test_run/test_resume.pdf",
        status="Pending",
        source="greenhouse",
        missing_skills="Go, Terraform",
        interview_topics="System design, Stream processing, Fintech domain",
    )
    tracker.upsert(row)
    csv_path = ROOT / "logs" / "test_applications.csv"
    assert csv_path.exists()
    return f"CSV written: {csv_path}"

check("Sheets tracker (CSV fallback)", _tracker_csv)

def _pipeline_db():
    from pipeline.workflow import PipelineDB
    from pipeline.apify_scraper import JobListing
    db_path = ROOT / "data" / "test_pipeline.db"
    db = PipelineDB(db_path)
    job = JobListing(title="Test Job", company="TestCo", job_id="test_abc123")
    assert not db.is_processed("test_abc123"), "Should not be processed yet"
    db.mark_processed(job, score=88, status="matched")
    assert db.is_processed("test_abc123"), "Should be processed now"
    assert not db.is_processed("nonexistent"), "Should not exist"
    db_path.unlink()
    return "Insert → dedup → cleanup OK"

check("Pipeline deduplication DB", _pipeline_db)

# ─────────────────────────────────────────────────────────────────────────────
# 8. Config loading
# ─────────────────────────────────────────────────────────────────────────────
section("8. Configuration")

def _load_config():
    from pipeline.workflow import PipelineConfig
    config = PipelineConfig.from_yaml(ROOT / "config" / "settings.yaml")
    return (f"threshold={config.match_threshold}, "
            f"queries={config.queries[:2]}, "
            f"sources={config.sources}")

check("Load settings.yaml", _load_config)

def _notifier_dry_run():
    from pipeline.notifier import Notifier, PipelineRunSummary
    summary = PipelineRunSummary(
        run_date="2026-05-20",
        jobs_scraped=47,
        jobs_matched=8,
        jobs_skipped=39,
        resumes_generated=8,
        top_matches=[
            {"title": "Senior Data Engineer", "company": "Stripe", "score": 88, "url": ""},
            {"title": "Staff Platform Engineer", "company": "Databricks", "score": 84, "url": ""},
        ],
    )
    text = summary.to_text()
    assert "Stripe" in text
    return f"Summary text: {len(text)} chars, Slack blocks: {len(summary.to_slack_blocks())}"

check("Notification summary builder", _notifier_dry_run)

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────
section("TEST RESULTS")

passed = [r for r in results if r[1] is True]
failed = [r for r in results if r[1] is False]
skipped = [r for r in results if r[1] is None]

print(f"\n  {PASS} Passed : {len(passed)}")
print(f"  {FAIL} Failed : {len(failed)}")
print(f"  {SKIP} Skipped: {len(skipped)} (need API keys)")

if failed:
    print("\n  FAILURES:")
    for name, _, err in failed:
        print(f"    • {name}: {err}")

if skipped:
    print("\n  TO ENABLE SKIPPED TESTS:")
    print("    1. Copy .env.template → .env")
    print("    2. Add ANTHROPIC_API_KEY=sk-ant-...")
    print("    3. Add APIFY_API_TOKEN=apify_api_...")
    print("    4. Run this test again")

if not failed:
    print(f"\n  {PASS} All tests passed! The pipeline is working correctly.")
    if out_dir.exists():
        print(f"\n  📄 Generated documents are in: {out_dir}/")
        for f in sorted(out_dir.iterdir()):
            print(f"     • {f.name} ({f.stat().st_size // 1024} KB)")
else:
    print(f"\n  {FAIL} {len(failed)} test(s) failed — see errors above.")
    sys.exit(1)
