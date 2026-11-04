"""
Job Scraper Agent
-----------------
Uses WebSearch and WebFetch to discover and extract job postings from online sources.
Returns structured job data for downstream filtering.
"""

import anyio
import json
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock


SCRAPER_SYSTEM_PROMPT = """You are a job scraper agent. Your task is to find and extract job postings from the web.

For each job you find, extract the following fields in JSON format:
- title: Job title
- company: Company name
- location: Job location (city, state, or "Remote")
- job_type: full-time / part-time / contract / remote
- salary_range: Salary range if listed (e.g. "$150k-$200k"), or null
- description: Full job description text
- requirements: List of required skills/qualifications
- nice_to_have: List of preferred/nice-to-have skills
- apply_url: URL to apply or the source URL
- posted_date: When the job was posted (if available)
- company_about: Brief description of the company (if available)
- contact_email: Hiring manager or HR email (if listed)

Return a valid JSON array of job objects. If you cannot find structured data,
extract whatever is available and set missing fields to null.

Be thorough - read each job posting fully before extracting data."""


async def scrape_jobs(sources: list[str], max_jobs: int = 10) -> list[dict]:
    """
    Scrape job postings from a list of URLs/sources.

    Args:
        sources: List of URLs or search queries to scrape jobs from
        max_jobs: Maximum number of jobs to return

    Returns:
        List of job dicts with structured data
    """
    sources_text = "\n".join(f"- {s}" for s in sources)

    prompt = f"""Search and fetch job postings from these sources:
{sources_text}

For each source:
1. Use WebFetch to load the page
2. Extract all visible job listings
3. For each job listing found, fetch the individual job page for full details (if links are available)
4. Extract structured data for each job

Aim to collect up to {max_jobs} jobs total across all sources.

Return ONLY a valid JSON array of job objects. No other text before or after the JSON.
If a source fails to load, skip it and try the next one."""

    result_text = ""

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["WebSearch", "WebFetch"],
            system_prompt=SCRAPER_SYSTEM_PROMPT,
            max_turns=30,
            permission_mode="bypassPermissions",
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result_text = block.text

    # Parse the JSON from the result
    jobs = _parse_jobs_json(result_text)
    return jobs[:max_jobs]


def _parse_jobs_json(text: str) -> list[dict]:
    """Extract and parse JSON array from agent output."""
    if not text:
        return []

    # Try direct parse first
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "jobs" in data:
            return data["jobs"]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    print(f"Warning: Could not parse jobs JSON from scraper output.")
    print(f"Raw output (first 500 chars): {text[:500]}")
    return []


if __name__ == "__main__":
    from config import JOB_CRITERIA

    async def main():
        print("Starting job scraper...")
        jobs = await scrape_jobs(
            sources=JOB_CRITERIA["job_sources"],
            max_jobs=JOB_CRITERIA["max_jobs_to_scrape"],
        )
        print(f"\nScraped {len(jobs)} jobs:")
        for i, job in enumerate(jobs, 1):
            print(f"{i}. {job.get('title')} at {job.get('company')} - {job.get('location')}")

        # Save raw scraped data
        with open("scraped_jobs.json", "w") as f:
            json.dump(jobs, f, indent=2)
        print("\nSaved to scraped_jobs.json")

    anyio.run(main)
