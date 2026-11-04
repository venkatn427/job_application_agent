"""
Resume Generator
-----------------
Uses Claude (with prompt caching) to produce an ATS-optimized, tailored
resume for each job. Never invents experience — only reframes real facts.

Outputs:
  - TailoredResume dataclass with all sections as structured text
  - Ready to pass to document_builder.py for PDF/DOCX rendering
"""

import re
import os
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

import anthropic

from .profile_analyzer import CandidateProfile
from .apify_scraper import JobListing

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TailoredResume:
    """Structured content for a single tailored resume."""
    job: JobListing

    # Header
    candidate_name: str = ""
    candidate_email: str = ""
    candidate_phone: str = ""
    candidate_location: str = ""
    linkedin_url: str = ""
    github_url: str = ""

    # Content sections
    professional_summary: str = ""
    skills_section: str = ""          # formatted skills list
    experience_bullets: dict[str, list[str]] = field(default_factory=dict)
    # key = "Company | Title | Dates", value = list of bullet strings

    education_section: str = ""
    certifications_section: str = ""
    additional_sections: dict[str, str] = field(default_factory=dict)

    # Metadata
    ats_keywords_used: list[str] = field(default_factory=list)
    tailoring_notes: str = ""
    match_score: int = 0

    def full_text(self) -> str:
        """Render as plain text (for review / fallback export)."""
        lines = [
            self.candidate_name,
            " | ".join(filter(None, [
                self.candidate_email, self.candidate_phone,
                self.candidate_location, self.linkedin_url, self.github_url,
            ])),
            "",
            "PROFESSIONAL SUMMARY",
            self.professional_summary,
            "",
            "SKILLS",
            self.skills_section,
            "",
            "EXPERIENCE",
        ]
        for header, bullets in self.experience_bullets.items():
            lines.append(header)
            for b in bullets:
                lines.append(f"  • {b}")
            lines.append("")
        if self.education_section:
            lines += ["EDUCATION", self.education_section, ""]
        if self.certifications_section:
            lines += ["CERTIFICATIONS", self.certifications_section, ""]
        for section, content in self.additional_sections.items():
            lines += [section.upper(), content, ""]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# System prompts (cached)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_RESUME = """You are a senior technical resume writer with 15 years of experience in ATS optimization.
Your job is to tailor a candidate's existing resume to a specific job description.

CRITICAL RULES — never break these:
1. NEVER invent, fabricate, or add experience the candidate does not have
2. NEVER add skills the candidate did not list
3. ONLY reframe, reorder, and keyword-optimize existing content
4. Keep all dates, companies, and titles exactly as provided
5. Use strong action verbs and quantified achievements where they already exist
6. Embed ATS keywords naturally — never stuff them awkwardly
7. Professional tone throughout

Resume length guidelines:
- Under 5 years experience: target 1 page (≤50 bullet points across all sections)
- 5+ years experience: target 2 pages (≤90 bullet points)

Return ONLY a valid JSON object with these exact keys:
{
  "professional_summary": "3-4 sentence summary tailored to this role",
  "skills_section": "Comma-separated or categorized skills list",
  "experience_bullets": {
    "Company | Title | Start–End": ["bullet 1", "bullet 2", ...]
  },
  "education_section": "Formatted education string",
  "certifications_section": "Formatted certifications string or empty string",
  "ats_keywords_used": ["keyword1", "keyword2"],
  "tailoring_notes": "Brief internal note on what was changed and why"
}"""

_SYSTEM_COVER_LETTER = """You are an expert cover letter writer who creates compelling, personalized letters
that get candidates noticed. You write in a professional yet authentic voice.

RULES:
1. 3-4 paragraphs, 250-350 words
2. Address specific details from the job description
3. Connect candidate's actual experience to the role requirements
4. End with a clear call to action
5. Never be generic — every sentence should be specific to this company/role
6. Return ONLY the cover letter text, no preamble"""


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

class ResumeGenerator:
    """
    Generates tailored resumes and cover letters using Claude.
    Prompt caching is used on the system prompts and candidate profile
    to reduce costs significantly when processing many jobs.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-sonnet-4-6"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_resume(
        self,
        job: JobListing,
        profile: CandidateProfile,
        match_score: int = 0,
    ) -> TailoredResume:
        """Generate a tailored resume for the given job."""
        logger.info("Generating resume for %r @ %r", job.title, job.company)

        profile_json = self._profile_to_prompt(profile)
        jd_text = self._jd_to_prompt(job)

        raw = self._call_claude_resume(profile_json, jd_text, profile.years_experience)
        resume_data = self._parse_resume_json(raw)

        # Build TailoredResume
        result = TailoredResume(
            job=job,
            candidate_name=profile.name,
            candidate_email=profile.email,
            candidate_phone=profile.phone,
            candidate_location=profile.location,
            linkedin_url=profile.linkedin_url,
            github_url=profile.github_url,
            professional_summary=resume_data.get("professional_summary", ""),
            skills_section=resume_data.get("skills_section", ""),
            experience_bullets=resume_data.get("experience_bullets", {}),
            education_section=resume_data.get("education_section", ""),
            certifications_section=resume_data.get("certifications_section", ""),
            ats_keywords_used=resume_data.get("ats_keywords_used", []),
            tailoring_notes=resume_data.get("tailoring_notes", ""),
            match_score=match_score,
        )

        # If Claude returned empty experience_bullets, fall back to original
        if not result.experience_bullets:
            result.experience_bullets = self._fallback_experience(profile)

        logger.info(
            "Resume generated: %d experience sections, %d ATS keywords",
            len(result.experience_bullets),
            len(result.ats_keywords_used),
        )
        return result

    def generate_cover_letter(
        self,
        job: JobListing,
        profile: CandidateProfile,
        tone: str = "professional",
    ) -> str:
        """Generate a tailored cover letter. Returns plain text."""
        logger.info("Generating cover letter for %r @ %r", job.title, job.company)
        profile_summary = (
            f"Name: {profile.name}\n"
            f"Current role: {profile.current_title}\n"
            f"Years experience: {profile.years_experience:.0f}\n"
            f"Key skills: {', '.join(profile.skills_flat()[:15])}\n"
            f"Recent experience:\n{profile.experience_summary()}"
        )
        jd_text = self._jd_to_prompt(job)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_COVER_LETTER,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Write a {tone} cover letter.\n\n"
                            f"CANDIDATE:\n{profile_summary}\n\n"
                            f"JOB:\n{jd_text}"
                        ),
                    }
                ],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Cover letter generation failed: %s", e)
            return self._fallback_cover_letter(job, profile)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _call_claude_resume(
        self,
        profile_json: str,
        jd_text: str,
        years_exp: float,
    ) -> str:
        """Call Claude with prompt caching on system + profile."""
        page_target = "1 page (under 5 years experience)" if years_exp < 5 else "2 pages"
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_RESUME,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"CANDIDATE PROFILE:\n{profile_json}",
                            # Cache the profile — reused across all jobs
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": (
                                f"TARGET RESUME LENGTH: {page_target}\n\n"
                                f"JOB TO TAILOR FOR:\n{jd_text}\n\n"
                                "Produce the tailored resume JSON now."
                            ),
                        },
                    ],
                }
            ],
        )
        # Detect truncated output (ran out of tokens mid-JSON)
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                "Claude response was truncated (max_tokens reached). "
                "Increase max_tokens or shorten the profile/JD."
            )
        return response.content[0].text.strip()

    def _profile_to_prompt(self, profile: CandidateProfile) -> str:
        """Serialize profile for the LLM prompt."""
        exp_list = []
        for exp in profile.work_experience:
            exp_list.append({
                "company": exp.company,
                "title": exp.title,
                "dates": f"{exp.start_date or '?'} – {exp.end_date or 'Present'}",
                "bullets": exp.bullets,
                "technologies": exp.technologies,
            })
        edu_list = [
            {"institution": e.institution, "degree": e.degree,
             "field": e.field_of_study, "year": e.graduation_year}
            for e in profile.education
        ]
        cert_list = [
            {"name": c.name, "issuer": c.issuer, "date": c.date}
            for c in profile.certifications
        ]
        return json.dumps({
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone,
            "location": profile.location,
            "linkedin_url": profile.linkedin_url,
            "github_url": profile.github_url,
            "years_experience": profile.years_experience,
            "current_title": profile.current_title,
            "professional_summary": profile.professional_summary,
            "skills": profile.skills_flat(),
            "work_experience": exp_list,
            "education": edu_list,
            "certifications": cert_list,
            "domain_expertise": profile.domain_expertise,
        }, indent=2)

    def _jd_to_prompt(self, job: JobListing) -> str:
        return (
            f"Company: {job.company}\n"
            f"Title: {job.title}\n"
            f"Location: {job.location or 'Not specified'}\n"
            f"Type: {job.job_type or 'Full-time'}\n"
            f"Description:\n{(job.description or '')[:4000]}\n"
            f"Requirements: {', '.join(job.requirements[:20])}"
        )

    def _parse_resume_json(self, raw: str) -> dict:
        raw = re.sub(r"^```json\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON object from messy output
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return json.loads(raw[start:end])
                except Exception:
                    pass
            raise ValueError(
                f"Claude returned output that is not valid JSON (first 200 chars): {raw[:200]!r}"
            )

    def _fallback_experience(self, profile: CandidateProfile) -> dict[str, list[str]]:
        """Original bullets if Claude fails."""
        result = {}
        for exp in profile.work_experience:
            key = f"{exp.company} | {exp.title} | {exp.start_date or '?'} – {exp.end_date or 'Present'}"
            result[key] = exp.bullets
        return result

    @staticmethod
    def _fallback_cover_letter(job: JobListing, profile: CandidateProfile) -> str:
        return (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to express my strong interest in the {job.title} position at {job.company}. "
            f"With {profile.years_experience:.0f} years of experience as {profile.current_title}, "
            f"I am confident I can make a meaningful contribution to your team.\n\n"
            f"My background includes expertise in {', '.join(profile.skills_flat()[:6])}. "
            f"I am particularly excited about this opportunity because it aligns closely with "
            f"my professional goals and technical background.\n\n"
            f"I would welcome the chance to discuss how my experience can benefit {job.company}. "
            f"Thank you for your consideration.\n\n"
            f"Sincerely,\n{profile.name}"
        )
