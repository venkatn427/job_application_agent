"""
Matching Agent
---------------
A Claude Agent SDK sub-agent that performs deep semantic job matching.

Unlike the heuristic JobMatcher in pipeline/, this agent:
  • Understands context and nuance in job descriptions
  • Identifies hidden requirements (culture fit, soft skills)
  • Computes a holistic compatibility score with detailed reasoning
  • Suggests specific preparation materials for the interview
  • Flags red flags (overqualified, travel required, etc.)

Returns a MatchAnalysis JSON.

Usage:
    from agents.matching_agent import run_matching_agent
    import asyncio
    result = asyncio.run(run_matching_agent(job_dict, profile_dict))
"""

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior technical recruiter with 20 years of experience
evaluating candidate fit for engineering roles. You have deep knowledge of:
- What ATS systems look for
- How hiring managers evaluate candidates
- Common gaps between resume and job requirements
- Interview preparation strategies

Given a job description and candidate profile, produce a comprehensive match analysis.

Return ONLY a valid JSON object with these exact keys:

{
  "score": 82,
  "matched_keywords": ["Python", "AWS", "Kafka", "microservices"],
  "missing_keywords": ["Go", "Terraform"],
  "match_reasons": [
    "Strong Python backend experience aligns with core stack",
    "7 years experience exceeds 5+ requirement",
    "Previous fintech domain experience matches company focus"
  ],
  "gaps": [
    "No Go experience mentioned — but Python background makes this learnable",
    "Terraform not listed but AWS CDK experience is equivalent"
  ],
  "red_flags": [],
  "interview_prep_topics": [
    "System design: distributed payment processing at scale",
    "Behavioral: leading cross-functional teams on compliance projects",
    "Technical: Python async patterns and event-driven architecture",
    "Company-specific: their recent migration from monolith to microservices"
  ],
  "recommended": true,
  "recommended_reason": "Strong technical match with relevant domain experience. Minor gaps are learnable.",
  "tailoring_suggestions": [
    "Emphasize Kafka experience in professional summary",
    "Lead with payment processing achievements in work history",
    "Add 'event-driven architecture' keyword to skills section"
  ]
}

Scoring rubric (1-100):
  90-100: Perfect match, apply immediately
  80-89: Strong match, apply with minor tailoring
  70-79: Good match, worth applying with focused tailoring
  60-69: Moderate match, significant gaps exist
  < 60:  Poor match, recommend skipping

IMPORTANT:
- Be honest about gaps — do not inflate scores
- Red flags: overqualified (>6yr surplus), requires relocation when candidate wants remote, etc.
- interview_prep_topics: highly specific to this exact role and company
- NEVER hallucinate skills the candidate doesn't have"""


async def run_matching_agent(
    job: dict,
    profile: dict,
    threshold: int = 70,
) -> dict:
    """
    Run the matching agent and return a detailed match analysis dict.

    Args:
        job: JobListing.to_dict() or equivalent
        profile: CandidateProfile.to_dict() or equivalent
        threshold: minimum score to recommend

    Returns:
        dict with score, keywords, gaps, interview_topics, etc.
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
    except ImportError:
        logger.error("claude_agent_sdk not installed")
        return {"score": 0, "recommended": False}

    # Compact profile (avoid token overflow)
    compact_profile = {
        "name": profile.get("name"),
        "current_title": profile.get("current_title"),
        "years_experience": profile.get("years_experience"),
        "skills": profile.get("skills", [])[:30],
        "technologies": profile.get("technologies", [])[:20],
        "programming_languages": profile.get("programming_languages", []),
        "cloud_platforms": profile.get("cloud_platforms", []),
        "domain_expertise": profile.get("domain_expertise", []),
        "industry_keywords": profile.get("industry_keywords", [])[:20],
        "work_experience": [
            {
                "company": e.get("company"),
                "title": e.get("title"),
                "start_date": e.get("start_date"),
                "end_date": e.get("end_date"),
                "bullets": e.get("bullets", [])[:5],
            }
            for e in profile.get("work_experience", [])[:4]
        ],
        "certifications": [c.get("name") for c in profile.get("certifications", [])],
        "achievements": profile.get("achievements", [])[:6],
    }

    compact_job = {
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "job_type": job.get("job_type"),
        "description": (job.get("description") or "")[:3000],
        "requirements": job.get("requirements", [])[:20],
        "salary_range": job.get("salary_range"),
    }

    prompt = (
        f"CANDIDATE PROFILE:\n{json.dumps(compact_profile, indent=2)}\n\n"
        f"JOB POSTING:\n{json.dumps(compact_job, indent=2)}\n\n"
        f"Match threshold is {threshold}/100. Produce the full match analysis JSON."
    )

    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=[],
            max_turns=3,
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
        return {
            "score": 0,
            "matched_keywords": [],
            "missing_keywords": [],
            "match_reasons": ["Analysis failed"],
            "gaps": [],
            "interview_prep_topics": [],
            "recommended": False,
        }

    # Enforce threshold
    score = int(parsed.get("score") or 0)
    parsed["recommended"] = score >= threshold
    return parsed


async def batch_match(
    jobs: list[dict],
    profile: dict,
    threshold: int = 70,
    concurrency: int = 3,
) -> list[dict]:
    """
    Score multiple jobs concurrently.
    Returns list of (job, analysis) dicts sorted by score.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _match_one(job: dict) -> dict:
        async with semaphore:
            analysis = await run_matching_agent(job, profile, threshold)
            return {"job": job, "analysis": analysis}

    tasks = [_match_one(j) for j in jobs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid = [r for r in results if isinstance(r, dict)]
    valid.sort(key=lambda r: r["analysis"].get("score", 0), reverse=True)
    return valid


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
    return {}


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    # Quick smoke test with dummy data
    test_job = {
        "title": "Senior Python Engineer",
        "company": "Acme Corp",
        "description": (
            "We're looking for a Senior Python Engineer with 5+ years experience. "
            "Must have: Python, AWS, PostgreSQL, microservices. "
            "Nice to have: Kafka, Kubernetes, Go."
        ),
        "requirements": ["Python", "AWS", "PostgreSQL", "microservices"],
    }
    test_profile = {
        "name": "Jane Doe",
        "current_title": "Senior Software Engineer",
        "years_experience": 7,
        "skills": ["Python", "AWS", "PostgreSQL", "Docker"],
        "technologies": ["Kubernetes", "Redis", "RabbitMQ"],
        "programming_languages": ["Python", "TypeScript"],
        "cloud_platforms": ["AWS"],
        "domain_expertise": ["Backend Engineering", "Data Pipelines"],
        "work_experience": [
            {
                "company": "TechCo",
                "title": "Senior Engineer",
                "start_date": "2020",
                "end_date": "Present",
                "bullets": ["Built microservices on AWS", "Led team of 4 engineers"],
            }
        ],
        "achievements": ["Reduced API latency by 60%", "Processed 1B events/day"],
    }

    result = asyncio.run(run_matching_agent(test_job, test_profile))
    print(json.dumps(result, indent=2))
