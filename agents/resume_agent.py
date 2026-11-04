"""
Resume Agent
-------------
A Claude Agent SDK sub-agent that produces a fully tailored, ATS-optimized
resume JSON. It acts as an experienced resume writer who:

  • Reframes existing bullets to match JD language
  • Naturally embeds ATS keywords
  • Writes a role-specific professional summary
  • Curates which experience to emphasize
  • Stays factually accurate — NEVER invents data

Returns a structured TailoredResume-compatible JSON.

Usage:
    from agents.resume_agent import run_resume_agent
    import asyncio
    result = asyncio.run(run_resume_agent(job_dict, profile_dict))
"""

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a world-class technical resume writer with 15 years of experience.
You specialize in ATS optimization and helping engineers land jobs at top companies.

Your task: Take the candidate's existing profile and tailor it for a specific job.

CRITICAL RULES — violating any of these is unacceptable:
1. NEVER add experience, skills, or achievements the candidate does not have
2. NEVER change dates, company names, or job titles
3. ONLY reframe, emphasize, and keyword-optimize existing content
4. All claims must be verifiable from the original profile
5. Keep bullet points concise (1-2 lines max)
6. Start each bullet with a strong action verb
7. Quantify achievements where the data already exists in the profile

ATS optimization rules:
- Match job description keywords exactly (not paraphrased)
- Use both acronyms and full terms for the first occurrence (e.g., "Kubernetes (K8s)")
- Avoid tables, columns, headers, or special characters that break ATS parsers
- Skills section: comma-separated or simple category lists

Resume length:
- Under 5 years experience: target 1 page
- 5+ years experience: target 2 pages

Return ONLY valid JSON with this exact structure:
{
  "professional_summary": "3-4 sentence summary tailored to this specific role. Include the job title, key relevant technologies, and a value proposition.",
  "skills_section": "Technical Skills: Python, AWS, Kubernetes, PostgreSQL | Leadership: Team Lead, Mentoring | Methodologies: Agile, CI/CD",
  "experience_bullets": {
    "Company Name | Job Title | Start Date – End Date": [
      "Led development of real-time data pipeline processing 50M events/day using Python and Kafka, reducing ingestion latency by 65%",
      "Architected microservices migration from monolith, improving deployment frequency from monthly to daily releases"
    ]
  },
  "education_section": "B.S. Computer Science, UC Berkeley, 2017 | GPA: 3.8/4.0",
  "certifications_section": "AWS Solutions Architect – Professional (2023) | Google Cloud Professional Data Engineer (2022)",
  "ats_keywords_used": ["Python", "AWS", "Kafka", "microservices", "Kubernetes"],
  "tailoring_notes": "Emphasized Kafka and streaming data experience to match the real-time processing requirements. Reordered bullets to lead with scale metrics."
}

Experience bullet selection rules:
- Include ALL work experience positions from the profile
- For each position, select the 3-5 most relevant bullets
- If a company had <3 good bullets, use what exists — do not fabricate
- Prioritize bullets that include numbers/metrics"""


async def run_resume_agent(
    job: dict,
    profile: dict,
    match_analysis: Optional[dict] = None,
) -> dict:
    """
    Run the resume tailoring agent.

    Args:
        job: JobListing as dict
        profile: CandidateProfile as dict
        match_analysis: Optional output from matching_agent for better tailoring

    Returns:
        TailoredResume-compatible dict
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
    except ImportError:
        logger.error("claude_agent_sdk not installed")
        return {}

    # Build the tailoring context
    years_exp = float(profile.get("years_experience") or 0)
    page_target = "1 page" if years_exp < 5 else "2 pages"

    context_parts = [
        f"TARGET PAGE LENGTH: {page_target}",
        f"\nCANDIDATE PROFILE:\n{json.dumps(_compact_profile(profile), indent=2)}",
        f"\nJOB TO TAILOR FOR:\n{json.dumps(_compact_job(job), indent=2)}",
    ]

    if match_analysis:
        context_parts.append(
            f"\nMATCH ANALYSIS (use this to guide tailoring):\n"
            f"Keywords to emphasize: {', '.join(match_analysis.get('matched_keywords', [])[:10])}\n"
            f"Tailoring suggestions:\n"
            + "\n".join(f"- {s}" for s in match_analysis.get("tailoring_suggestions", []))
        )

    prompt = "\n".join(context_parts) + "\n\nProduce the tailored resume JSON now."

    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=[],
            max_turns=5,
            permission_mode="bypassPermissions",
        ),
    ):
        if hasattr(message, "result"):
            result_text = message.result
        elif hasattr(message, "content"):
            for block in message.content:
                if hasattr(block, "text"):
                    result_text = block.text

    parsed = _parse_json(result_text)
    if not parsed:
        logger.warning("Resume agent returned empty/invalid JSON")
    return parsed


def _compact_profile(profile: dict) -> dict:
    """Trim profile for the prompt — keep all experience but limit depth."""
    return {
        "name": profile.get("name"),
        "email": profile.get("email"),
        "phone": profile.get("phone"),
        "location": profile.get("location"),
        "linkedin_url": profile.get("linkedin_url"),
        "github_url": profile.get("github_url"),
        "years_experience": profile.get("years_experience"),
        "current_title": profile.get("current_title"),
        "professional_summary": profile.get("professional_summary"),
        "skills": profile.get("skills", [])[:30],
        "technologies": profile.get("technologies", [])[:20],
        "programming_languages": profile.get("programming_languages", []),
        "tools": profile.get("tools", [])[:15],
        "cloud_platforms": profile.get("cloud_platforms", []),
        "work_experience": [
            {
                "company": e.get("company"),
                "title": e.get("title"),
                "start_date": e.get("start_date"),
                "end_date": e.get("end_date"),
                "location": e.get("location"),
                "bullets": e.get("bullets", []),
                "technologies": e.get("technologies", []),
            }
            for e in profile.get("work_experience", [])
        ],
        "education": profile.get("education", []),
        "certifications": profile.get("certifications", []),
        "domain_expertise": profile.get("domain_expertise", []),
        "achievements": profile.get("achievements", []),
    }


def _compact_job(job: dict) -> dict:
    return {
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "job_type": job.get("job_type"),
        "description": (job.get("description") or "")[:3500],
        "requirements": job.get("requirements", [])[:20],
        "salary_range": job.get("salary_range"),
    }


def _parse_json(text: str) -> dict:
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
    logger.warning("Could not parse resume agent JSON")
    return {}


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    test_job = {
        "title": "Senior Data Engineer",
        "company": "DataCo",
        "description": (
            "We need a Senior Data Engineer with 5+ years experience in "
            "Python, Apache Spark, Kafka, and AWS. You'll build real-time "
            "data pipelines processing billions of events. Kubernetes experience preferred."
        ),
        "requirements": ["Python", "Apache Spark", "Kafka", "AWS", "Kubernetes"],
    }
    test_profile = {
        "name": "Alex Johnson",
        "email": "alex@example.com",
        "phone": "+1-415-555-0100",
        "location": "San Francisco, CA",
        "linkedin_url": "https://linkedin.com/in/alexj",
        "years_experience": 7,
        "current_title": "Senior Software Engineer",
        "skills": ["Python", "SQL", "Apache Spark", "AWS"],
        "technologies": ["Kafka", "Kubernetes", "Docker", "Airflow", "dbt"],
        "cloud_platforms": ["AWS", "GCP"],
        "work_experience": [
            {
                "company": "TechCo",
                "title": "Senior Software Engineer",
                "start_date": "2021-01",
                "end_date": "Present",
                "bullets": [
                    "Built real-time data pipeline processing 500M events/day using Kafka and Spark",
                    "Reduced data processing costs by $1.2M/year through Spark optimization",
                    "Led migration to Kubernetes, achieving 99.99% uptime SLA",
                ],
                "technologies": ["Python", "Kafka", "Apache Spark", "Kubernetes", "AWS"],
            },
            {
                "company": "StartupInc",
                "title": "Data Engineer",
                "start_date": "2018-06",
                "end_date": "2020-12",
                "bullets": [
                    "Designed ETL pipelines ingesting 50TB/day from 20+ data sources",
                    "Built dbt models reducing analytics query time by 80%",
                ],
                "technologies": ["Python", "dbt", "AWS Redshift", "Airflow"],
            },
        ],
        "education": [{"institution": "UC Davis", "degree": "B.S. Computer Science", "graduation_year": "2017"}],
        "certifications": [{"name": "AWS Data Analytics Specialty", "issuer": "AWS", "date": "2022"}],
        "achievements": [
            "Processed 500M events/day with 99.9% reliability",
            "Saved $1.2M/year in cloud costs",
        ],
    }

    result = asyncio.run(run_resume_agent(test_job, test_profile))
    print(json.dumps(result, indent=2))
