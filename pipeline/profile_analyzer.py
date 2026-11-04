"""
Candidate Profile Analyzer
---------------------------
Parses a master resume (PDF/DOCX/TXT) and an optional LinkedIn profile export
into a structured CandidateProfile that drives job matching and resume tailoring.

Uses Claude API with prompt caching for efficient re-processing.
"""

import os
import re
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

import anthropic

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Candidate data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkExperience:
    company: str
    title: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = None
    bullets: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)


@dataclass
class Education:
    institution: str
    degree: str
    field_of_study: Optional[str] = None
    graduation_year: Optional[str] = None
    gpa: Optional[str] = None


@dataclass
class Certification:
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None


@dataclass
class CandidateProfile:
    # Basics
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""

    # Professional
    professional_summary: str = ""
    years_experience: float = 0.0
    current_title: str = ""
    target_titles: list[str] = field(default_factory=list)

    # Skills & tech
    skills: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    programming_languages: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    cloud_platforms: list[str] = field(default_factory=list)

    # Experience, education, certs
    work_experience: list[WorkExperience] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    certifications: list[Certification] = field(default_factory=list)

    # Keywords and domains
    domain_expertise: list[str] = field(default_factory=list)
    industry_keywords: list[str] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)

    # Raw text (for context in LLM calls)
    raw_resume_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_resume_text", None)
        return d

    def skills_flat(self) -> list[str]:
        """All skills/tech in one deduplicated list."""
        all_skills = set(
            self.skills + self.technologies + self.programming_languages +
            self.tools + self.cloud_platforms
        )
        return sorted(all_skills, key=str.lower)

    def experience_summary(self) -> str:
        """Human-readable experience string for LLM prompts."""
        lines = []
        for exp in self.work_experience:
            period = f"{exp.start_date or '?'} – {exp.end_date or 'Present'}"
            lines.append(f"• {exp.title} at {exp.company} ({period})")
            for b in exp.bullets[:3]:
                lines.append(f"  - {b}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text_pdf(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except ImportError:
        pass
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception as e:
        logger.warning("PDF extraction failed for %s: %s", path, e)
        return ""


def _extract_text_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        logger.warning("DOCX extraction failed for %s: %s", path, e)
        return ""


def extract_text(path: Path) -> str:
    """Extract plain text from PDF, DOCX, or TXT."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_pdf(path)
    elif suffix in (".docx", ".doc"):
        return _extract_text_docx(path)
    elif suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    else:
        logger.warning("Unsupported file type %s — reading as text", suffix)
        return path.read_text(encoding="utf-8", errors="ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Profile Analyzer
# ─────────────────────────────────────────────────────────────────────────────

_PARSE_SYSTEM = """You are an expert resume parser. Given resume text, extract all information
into a structured JSON object. Return ONLY valid JSON with these keys:

{
  "name": "string",
  "email": "string",
  "phone": "string",
  "location": "string",
  "linkedin_url": "string",
  "github_url": "string",
  "portfolio_url": "string",
  "professional_summary": "string",
  "years_experience": number,
  "current_title": "string",
  "target_titles": ["string"],
  "skills": ["string"],
  "technologies": ["string"],
  "programming_languages": ["string"],
  "tools": ["string"],
  "cloud_platforms": ["string"],
  "work_experience": [
    {
      "company": "string",
      "title": "string",
      "start_date": "string",
      "end_date": "string",
      "location": "string",
      "bullets": ["string"],
      "technologies": ["string"]
    }
  ],
  "education": [
    {
      "institution": "string",
      "degree": "string",
      "field_of_study": "string",
      "graduation_year": "string",
      "gpa": "string"
    }
  ],
  "certifications": [
    {
      "name": "string",
      "issuer": "string",
      "date": "string"
    }
  ],
  "domain_expertise": ["string"],
  "industry_keywords": ["string"],
  "achievements": ["string"]
}

Rules:
- years_experience: calculate from work history dates; float
- target_titles: infer 2-3 roles the candidate would naturally apply for
- domain_expertise: high-level domains (e.g. "Machine Learning", "Cloud Infrastructure")
- industry_keywords: key ATS terms from the resume
- achievements: bullet points with measurable impact (keep verbatim)
- If a field has no data, use null or empty list
- NEVER invent data not present in the resume"""


class ProfileAnalyzer:
    """
    Parses a candidate's resume and LinkedIn export into a CandidateProfile.
    Caches the profile to disk so it is only re-parsed when the source changes.
    """

    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.cache_dir = cache_dir or Path("data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ── Cache helpers ────────────────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self.cache_dir / "candidate_profile.json"

    @staticmethod
    def _file_hash(path: Path) -> str:
        """MD5 of a file's contents, used to detect resume changes."""
        h = hashlib.md5()
        h.update(path.read_bytes())
        return h.hexdigest()

    def load_cached(self, resume_path: Optional[Path] = None) -> Optional[CandidateProfile]:
        """Load cached profile only if it has real content and the resume hasn't changed."""
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            logger.warning("Could not read cached profile: %s", e)
            return None

        # Invalidate if resume file has changed since the cache was written
        if resume_path and resume_path.exists():
            current_hash = self._file_hash(resume_path)
            if data.get("_resume_hash") != current_hash:
                logger.info("Resume file changed — invalidating profile cache")
                return None

        profile = self._dict_to_profile(data)
        # Don't return a cached profile with no meaningful content — it was likely
        # written from a failed parse and would poison every job scored against it.
        if not profile.name and not profile.skills and not profile.work_experience:
            logger.warning("Cached profile is empty — ignoring and re-parsing")
            return None
        return profile

    def save_cache(self, profile: CandidateProfile, resume_path: Optional[Path] = None) -> None:
        """Cache is only written when the profile has real content."""
        if not profile.name and not profile.skills and not profile.work_experience:
            logger.warning("Refusing to cache empty profile — parse likely failed")
            return
        path = self._cache_path()
        full = profile.to_dict()
        full["raw_resume_text"] = profile.raw_resume_text
        if resume_path and resume_path.exists():
            full["_resume_hash"] = self._file_hash(resume_path)
        path.write_text(json.dumps(full, indent=2))
        logger.info("Profile cached to %s", path)

    # ── Main entry point ─────────────────────────────────────────────────────

    def analyze(
        self,
        resume_path: Optional[Path] = None,
        linkedin_export_path: Optional[Path] = None,
        force_refresh: bool = False,
    ) -> CandidateProfile:
        """
        Parse resume and/or LinkedIn export. Uses cache unless force_refresh=True
        or the resume file has changed since the last cache write.
        At least one source is required.
        """
        if not force_refresh:
            cached = self.load_cached(resume_path)
            if cached:
                logger.info("Using cached candidate profile for %s", cached.name)
                return cached

        resume_text = ""
        if resume_path and resume_path.exists():
            resume_text = extract_text(resume_path)
            logger.info("Extracted %d chars from resume %s", len(resume_text), resume_path)
        elif resume_path:
            logger.error("Resume not found at %s", resume_path)

        linkedin_text = ""
        if linkedin_export_path and linkedin_export_path.exists():
            linkedin_text = extract_text(linkedin_export_path)
            logger.info("Extracted %d chars from LinkedIn export", len(linkedin_text))

        combined = "\n\n".join(filter(None, [resume_text, linkedin_text]))
        if not combined.strip():
            logger.error("No text could be extracted from provided sources")
            # Return a minimal profile — do NOT cache this empty result
            return CandidateProfile()

        profile = self._parse_with_claude(combined)
        profile.raw_resume_text = resume_text or linkedin_text
        self.save_cache(profile, resume_path)
        return profile

    # ── Claude parsing ────────────────────────────────────────────────────────

    def _parse_with_claude(self, text: str) -> CandidateProfile:
        """Send resume text to Claude and parse the JSON response.

        Uses prompt caching on the system prompt to reduce token costs
        on repeated calls (e.g. force-refresh runs).
        """
        logger.info("Parsing profile with Claude...")
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": _PARSE_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Parse this resume/profile:\n\n{text[:12000]}",
                    }
                ],
            )
            raw = response.content[0].text.strip()

            # Strip markdown fences if present
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            data = json.loads(raw)
            return self._dict_to_profile(data)
        except json.JSONDecodeError as e:
            logger.error("JSON decode error parsing profile: %s", e)
            # Return profile with raw text so the pipeline has context, but save_cache
            # will refuse to persist it because name/skills/experience are all empty.
            return CandidateProfile(raw_resume_text=text)
        except Exception as e:
            logger.error("Claude profile parse failed: %s", e)
            return CandidateProfile(raw_resume_text=text)

    # ── Conversion ────────────────────────────────────────────────────────────

    @staticmethod
    def _dict_to_profile(data: dict) -> CandidateProfile:
        def _list(key: str) -> list:
            val = data.get(key) or []
            return val if isinstance(val, list) else []

        def _str(key: str) -> str:
            return str(data.get(key) or "").strip()

        work_exp = []
        for e in _list("work_experience"):
            work_exp.append(WorkExperience(
                company=str(e.get("company") or ""),
                title=str(e.get("title") or ""),
                start_date=e.get("start_date"),
                end_date=e.get("end_date"),
                location=e.get("location"),
                bullets=e.get("bullets") or [],
                technologies=e.get("technologies") or [],
            ))

        education = []
        for e in _list("education"):
            education.append(Education(
                institution=str(e.get("institution") or ""),
                degree=str(e.get("degree") or ""),
                field_of_study=e.get("field_of_study"),
                graduation_year=e.get("graduation_year"),
                gpa=e.get("gpa"),
            ))

        certs = []
        for c in _list("certifications"):
            certs.append(Certification(
                name=str(c.get("name") or ""),
                issuer=c.get("issuer"),
                date=c.get("date"),
            ))

        years = data.get("years_experience") or 0
        try:
            years = float(years)
        except (TypeError, ValueError):
            years = 0.0

        return CandidateProfile(
            name=_str("name"),
            email=_str("email"),
            phone=_str("phone"),
            location=_str("location"),
            linkedin_url=_str("linkedin_url"),
            github_url=_str("github_url"),
            portfolio_url=_str("portfolio_url"),
            professional_summary=_str("professional_summary"),
            years_experience=years,
            current_title=_str("current_title"),
            target_titles=_list("target_titles"),
            skills=_list("skills"),
            technologies=_list("technologies"),
            programming_languages=_list("programming_languages"),
            tools=_list("tools"),
            cloud_platforms=_list("cloud_platforms"),
            work_experience=work_exp,
            education=education,
            certifications=certs,
            domain_expertise=_list("domain_expertise"),
            industry_keywords=_list("industry_keywords"),
            achievements=_list("achievements"),
            raw_resume_text=data.get("raw_resume_text", ""),
        )
