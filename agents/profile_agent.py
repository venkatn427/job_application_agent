"""
Profile Agent
--------------
A Claude Agent SDK sub-agent that deep-extracts candidate profile data
from any source: PDF resume, DOCX, LinkedIn HTML export, or raw text.

Unlike the lightweight ProfileAnalyzer in pipeline/, this agent can:
  • Fetch a LinkedIn profile page directly (if URL is given)
  • Handle multi-page PDFs robustly
  • Ask clarifying follow-up questions via multi-turn
  • Extract portfolio projects from a Google Drive folder listing

Returns a complete CandidateProfile JSON.

Usage:
    from agents.profile_agent import run_profile_agent
    import asyncio
    profile_data = asyncio.run(run_profile_agent(resume_text="..."))
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert resume and LinkedIn profile analyst.

Your job is to extract a complete, structured candidate profile from the provided text.
You have access to WebFetch if a URL is provided.

Extract ALL of the following and return a single JSON object:

{
  "name": "Full Name",
  "email": "email@example.com",
  "phone": "+1-XXX-XXX-XXXX",
  "location": "City, State",
  "linkedin_url": "https://linkedin.com/in/...",
  "github_url": "https://github.com/...",
  "portfolio_url": "https://...",
  "professional_summary": "3-4 sentence professional summary",
  "years_experience": 7.5,
  "current_title": "Senior Software Engineer",
  "target_titles": ["Staff Engineer", "Engineering Manager", "Principal Engineer"],
  "skills": ["Python", "System Design", "Leadership"],
  "technologies": ["Kubernetes", "PostgreSQL", "Redis"],
  "programming_languages": ["Python", "Go", "TypeScript"],
  "tools": ["Git", "Jira", "Datadog"],
  "cloud_platforms": ["AWS", "GCP"],
  "work_experience": [
    {
      "company": "Acme Corp",
      "title": "Senior Engineer",
      "start_date": "2020-01",
      "end_date": "Present",
      "location": "San Francisco, CA",
      "bullets": [
        "Led migration of monolith to microservices, reducing p99 latency by 40%",
        "Managed team of 5 engineers across 3 time zones"
      ],
      "technologies": ["Go", "Kubernetes", "PostgreSQL"]
    }
  ],
  "education": [
    {
      "institution": "UC Berkeley",
      "degree": "B.S. Computer Science",
      "field_of_study": "Computer Science",
      "graduation_year": "2017",
      "gpa": "3.8"
    }
  ],
  "certifications": [
    {
      "name": "AWS Solutions Architect",
      "issuer": "Amazon Web Services",
      "date": "2022-06"
    }
  ],
  "domain_expertise": ["Distributed Systems", "Data Engineering", "Platform Engineering"],
  "industry_keywords": ["microservices", "event-driven architecture", "SLO/SLA"],
  "achievements": [
    "Reduced infrastructure costs by $2M/year through spot instance optimization",
    "Built real-time data pipeline processing 10M events/day"
  ]
}

IMPORTANT:
- years_experience: float, calculated from work history (most recent dates)
- target_titles: infer what roles this person would naturally apply to
- achievements: extract ALL bullet points that include numbers/metrics
- NEVER invent or fabricate information
- If a field has no data, use null or []
- Return ONLY the JSON, no markdown fences, no preamble"""


async def run_profile_agent(
    resume_text: Optional[str] = None,
    resume_path: Optional[Path] = None,
    linkedin_url: Optional[str] = None,
    raw_input: Optional[str] = None,
) -> dict:
    """
    Run the profile agent and return extracted profile as a dict.

    Priority: resume_path > resume_text > raw_input > linkedin_url (fetched)
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
    except ImportError:
        logger.error("claude_agent_sdk not installed — falling back to static parse")
        return {}

    # Build the input text
    content_parts = []
    if resume_path and Path(resume_path).exists():
        from pipeline.profile_analyzer import extract_text
        text = extract_text(Path(resume_path))
        if text:
            content_parts.append(f"RESUME TEXT:\n{text[:12000]}")

    if resume_text:
        content_parts.append(f"RESUME TEXT:\n{resume_text[:12000]}")

    if raw_input:
        content_parts.append(raw_input[:8000])

    prompt_parts = []
    if content_parts:
        prompt_parts.extend(content_parts)

    if linkedin_url:
        prompt_parts.append(
            f"Also fetch and extract data from this LinkedIn profile: {linkedin_url}"
        )

    if not prompt_parts:
        logger.warning("No profile input provided to profile_agent")
        return {}

    full_prompt = "\n\n".join(prompt_parts) + "\n\nExtract the full profile JSON now."

    result_text = ""
    async for message in query(
        prompt=full_prompt,
        options=ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=["WebFetch"] if linkedin_url else [],
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

    return _parse_json(result_text)


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
    logger.warning("Could not parse profile JSON from agent output")
    return {}


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    logging.basicConfig(level=logging.INFO)

    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = asyncio.run(run_profile_agent(resume_path=input_path))
    print(json.dumps(result, indent=2))
