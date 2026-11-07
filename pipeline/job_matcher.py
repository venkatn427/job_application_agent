"""
Job Matcher & Scorer
---------------------
Produces a 1–100 compatibility score for each job listing against the
candidate's profile. Combines:

  1. Keyword overlap (skills/tech)           — 40 pts
  2. Title alignment                         — 20 pts
  3. Experience level match                  — 20 pts
  4. Domain / industry alignment             — 10 pts
  5. Claude deep-analysis bonus              — 10 pts (optional, API call)

Score ≥ threshold (default 70) → job is included in the pipeline.
"""

import re
import os
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

from pipeline.llm import ModelClient, resolve

from .apify_scraper import JobListing
from .profile_analyzer import CandidateProfile

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Match result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    job: JobListing
    score: int                              # 1–100
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    match_reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    interview_prep_topics: list[str] = field(default_factory=list)
    recommended: bool = False               # score >= threshold

    def summary(self) -> str:
        status = "✅ MATCH" if self.recommended else "❌ SKIP"
        return (
            f"{status} [{self.score}/100] {self.job.title} @ {self.job.company}\n"
            f"  Matched: {', '.join(self.matched_keywords[:8])}\n"
            f"  Missing: {', '.join(self.missing_keywords[:5])}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Keyword extraction from JD
# ─────────────────────────────────────────────────────────────────────────────

# Common tech terms to look for in JDs.
# Note: C++ and C# are handled separately below because \b fails after + and #.
_TECH_PATTERNS = re.compile(
    r"\b("
    r"python|java(?:script)?|typescript|go(?:lang)?|rust|ruby|scala|kotlin|swift|"
    r"sql|nosql|postgresql|mysql|mongodb|redis|elasticsearch|dynamodb|cassandra|"
    r"aws|gcp|azure|kubernetes|k8s|docker|terraform|ansible|helm|"
    r"spark|kafka|airflow|dbt|flink|hadoop|hive|"
    r"react|vue|angular|next\.?js|node\.?js|fastapi|django|flask|spring|"
    r"machine\s*learning|deep\s*learning|nlp|llm|bert|pytorch|tensorflow|"
    r"ci/cd|github\s*actions|jenkins|gitlab|argocd|"
    r"microservices|rest(?:ful)?|graphql|grpc|"
    r"data\s*engineering|data\s*science|mlops|devops|platform\s*engineering|"
    r"linux|bash|shell\s*scripting"
    r")\b",
    re.IGNORECASE,
)

# C++ and C# need their own pattern since \b doesn't apply after symbol characters
_CPP_CS_RE = re.compile(r"(?<![A-Za-z])(c\+\+|c#)(?![A-Za-z])", re.IGNORECASE)

_SENIORITY_RE = re.compile(
    r"\b(entry[- ]?level|junior|mid[- ]?level|senior|staff|principal|"
    r"lead|manager|director|vp|head\s+of)\b",
    re.IGNORECASE,
)

# Match YOE only when accompanied by explicit experience context (not "401k vests after 1 year")
_YOE_REQUIRED_RE = re.compile(
    r"(\d+)\+?\s*(?:[-–to]+\s*\d+\s*)?years?(?:\s+of)?\s+(?:experience|exp\.?|professional|working)",
    re.IGNORECASE,
)
_YOE_CONTEXT_RE = re.compile(
    r"(?:require[sd]?|minimum|at\s+least|need[sd]?|minimum\s+of)\s+(\d+)\+?\s*years?",
    re.IGNORECASE,
)
_YOE_STATED_RE = re.compile(
    r"(\d+)\+?\s*years?\s+(?:required|minimum|relevant|combined)",
    re.IGNORECASE,
)

# Canonical alias map: maps any alias/abbreviation to a canonical set.
# Used to cross-match k8s↔kubernetes, js↔javascript, etc.
_SKILL_ALIASES: dict[str, frozenset[str]] = {
    "kubernetes": frozenset({"kubernetes", "k8s"}),
    "javascript": frozenset({"javascript", "js", "node.js", "nodejs", "node"}),
    "typescript": frozenset({"typescript", "ts"}),
    "machine learning": frozenset({"machine learning", "ml", "machine-learning"}),
    "natural language processing": frozenset({"nlp", "natural language processing"}),
    "large language model": frozenset({"llm", "large language model"}),
    "ci/cd": frozenset({"ci/cd", "ci", "cd", "continuous integration", "continuous delivery"}),
    "postgresql": frozenset({"postgresql", "postgres"}),
    "elasticsearch": frozenset({"elasticsearch", "elastic", "opensearch"}),
    "c++": frozenset({"c++", "cpp"}),
    "c#": frozenset({"c#", "csharp", "c sharp"}),
    "golang": frozenset({"go", "golang"}),
    "terraform": frozenset({"terraform", "iac", "infrastructure as code"}),
}
# Build reverse map: alias → canonical key
_ALIAS_LOOKUP: dict[str, str] = {}
for _canon, _aliases in _SKILL_ALIASES.items():
    for _a in _aliases:
        _ALIAS_LOOKUP[_a] = _canon


def _canonicalize(term: str) -> str:
    """Return the canonical form of a skill term."""
    return _ALIAS_LOOKUP.get(term.lower(), term.lower())


def _has_skill(jd_kw: str, skill_words: set[str]) -> bool:
    """True when the JD keyword meaningfully matches a candidate's skill set.

    Uses whole-word matching to prevent false positives:
    - "java" does NOT match "javascript"
    - "go" does NOT match "mongodb"
    - "kafka" DOES match "apache kafka" (candidate has multi-word skill containing kw)
    - "kubernetes" DOES match "k8s" via alias
    """
    jd_canon = _canonicalize(jd_kw)
    # Expand the check set with canonical equivalents
    alias_group = _SKILL_ALIASES.get(jd_canon, frozenset())

    # Exact or alias match in skill_words
    if jd_kw in skill_words or jd_canon in skill_words:
        return True
    if any(a in skill_words for a in alias_group):
        return True

    # Whole-word substring: jd_kw must appear as a complete token within a skill phrase
    # e.g. "kafka" in "apache kafka" is fine; "java" in "javascript" is not
    pattern = r"(?:^|\s)" + re.escape(jd_kw) + r"(?:\s|$)"
    if any(re.search(pattern, s, re.IGNORECASE) for s in skill_words):
        return True

    # Also check alias group
    for alias in alias_group:
        alias_pattern = r"(?:^|\s)" + re.escape(alias) + r"(?:\s|$)"
        if any(re.search(alias_pattern, s, re.IGNORECASE) for s in skill_words):
            return True

    return False


def extract_jd_keywords(text: str) -> set[str]:
    """Extract skill/tech keywords from job description text."""
    if not text:
        return set()
    matches = _TECH_PATTERNS.findall(text)
    result: set[str] = {re.sub(r"\s+", " ", m.lower().strip()) for m in matches}
    # C++ and C# with dedicated symbol-aware pattern
    for m in _CPP_CS_RE.finditer(text):
        result.add(m.group(1).lower())
    return result


def infer_required_yoe(text: str) -> Optional[int]:
    """Return the minimum years of experience requirement from a JD.

    Only matches phrases that clearly refer to experience requirements, not
    unrelated numbers (e.g. "401k vests after 1 year" is ignored).
    """
    nums: list[int] = []
    for m in _YOE_REQUIRED_RE.finditer(text):
        nums.append(int(m.group(1)))
    for m in _YOE_CONTEXT_RE.finditer(text):
        nums.append(int(m.group(1)))
    for m in _YOE_STATED_RE.finditer(text):
        nums.append(int(m.group(1)))
    return min(nums) if nums else None


def infer_seniority(text: str) -> Optional[str]:
    m = _SENIORITY_RE.search(text)
    return m.group(0).lower().replace("-", " ").strip() if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Scoring engine
# ─────────────────────────────────────────────────────────────────────────────

class JobMatcher:
    """
    Scores each JobListing against a CandidateProfile.

    use_claude=True enables a Claude-powered deep analysis that adds up to
    10 bonus points and produces interview prep topics. Set use_claude=False
    to run entirely offline.
    """

    def __init__(
        self,
        threshold: int = 70,
        use_claude: bool = True,
        api_key: Optional[str] = None,
        ai_config=None,
    ):
        self.threshold = threshold
        self.use_claude = use_claude
        self.ai_config = ai_config
        self.api_key = resolve("matching", config=ai_config, api_key=api_key).api_key if use_claude else None
        self._client: Optional[ModelClient] = None

    @property
    def client(self) -> ModelClient:
        if self._client is None:
            self._client = ModelClient("matching", config=self.ai_config, api_key=self.api_key)
        return self._client

    # ── Public API ────────────────────────────────────────────────────────────

    def score_job(self, job: JobListing, profile: CandidateProfile) -> MatchResult:
        result = MatchResult(job=job, score=0)
        jd_text = f"{job.title} {job.description or ''} {' '.join(job.requirements)}"

        # Component 1: keyword overlap (0–40)
        keyword_score, matched, missing = self._keyword_score(jd_text, profile)

        # Component 2: title alignment (0–20)
        title_score, title_reason = self._title_score(job.title, profile)

        # Component 3: experience level (0–20)
        exp_score, exp_reason = self._experience_score(jd_text, profile)

        # Component 4: domain alignment (0–10)
        domain_score, domain_reason = self._domain_score(jd_text, profile)

        base_score = keyword_score + title_score + exp_score + domain_score

        # Component 5: Claude deep analysis bonus (0–10)
        claude_bonus = 0
        interview_topics: list[str] = []
        gaps: list[str] = []
        if self.use_claude and self.api_key and base_score >= (self.threshold - 15):
            claude_bonus, interview_topics, gaps = self._claude_analysis(job, profile)

        final_score = min(100, base_score + claude_bonus)

        result.score = final_score
        result.matched_keywords = matched
        result.missing_keywords = missing
        result.match_reasons = [r for r in [title_reason, exp_reason, domain_reason] if r]
        result.gaps = gaps
        result.interview_prep_topics = interview_topics
        result.recommended = final_score >= self.threshold

        logger.debug(
            "Score %d/100 for %r @ %r (kw=%d title=%d exp=%d domain=%d claude=%d)",
            final_score, job.title, job.company,
            keyword_score, title_score, exp_score, domain_score, claude_bonus,
        )
        return result

    def score_batch(
        self,
        jobs: list[JobListing],
        profile: CandidateProfile,
    ) -> list[MatchResult]:
        """Score all jobs and return sorted by score descending."""
        results = [self.score_job(j, p) for j, p in ((j, profile) for j in jobs)]
        results.sort(key=lambda r: r.score, reverse=True)
        recommended = [r for r in results if r.recommended]
        skipped = [r for r in results if not r.recommended]
        logger.info(
            "Batch scoring: %d total, %d recommended (≥%d), %d skipped",
            len(results), len(recommended), self.threshold, len(skipped),
        )
        return results

    # ── Scoring components ────────────────────────────────────────────────────

    def _keyword_score(
        self,
        jd_text: str,
        profile: CandidateProfile,
    ) -> tuple[int, list[str], list[str]]:
        """Keyword overlap — max 40 points.

        Uses whole-word matching to prevent false positives:
        "java" ≠ "javascript", "go" ≠ "mongodb", "sql" ≠ "nosql".
        Alias expansion: "k8s" ↔ "kubernetes", "ts" ↔ "typescript", etc.
        """
        jd_keywords = extract_jd_keywords(jd_text)
        # Build flat candidate skill set (lowercased, including multi-word phrases)
        skill_words: set[str] = {s.lower() for s in profile.skills_flat()}

        matched = sorted(kw for kw in jd_keywords if _has_skill(kw, skill_words))
        missing = sorted(jd_keywords - set(matched))

        if not jd_keywords:
            return 20, [], []  # neutral if no keywords extracted

        ratio = len(matched) / max(len(jd_keywords), 1)
        score = min(40, int(ratio * 40))
        return score, matched, missing

    def _title_score(
        self,
        job_title: str,
        profile: CandidateProfile,
    ) -> tuple[int, str]:
        """Title alignment — max 20 points."""
        jt_lower = job_title.lower()

        # Current title match
        if profile.current_title and profile.current_title.lower() in jt_lower:
            return 20, f"Title '{job_title}' matches current role"

        # Target titles
        for target in profile.target_titles:
            if target.lower() in jt_lower or jt_lower in target.lower():
                return 18, f"Title '{job_title}' matches target '{target}'"

        # Partial word overlap
        jt_words = set(re.findall(r"\w+", jt_lower))
        profile_words = set(re.findall(
            r"\w+",
            (profile.current_title + " " + " ".join(profile.target_titles)).lower()
        ))
        overlap = jt_words & profile_words - {"senior", "junior", "lead", "staff", "the", "a"}
        if len(overlap) >= 2:
            return 12, f"Partial title overlap: {', '.join(overlap)}"
        if len(overlap) == 1:
            return 6, f"Minor title overlap: {', '.join(overlap)}"
        return 0, ""

    def _experience_score(
        self,
        jd_text: str,
        profile: CandidateProfile,
    ) -> tuple[int, str]:
        """Experience level match — max 20 points."""
        required_yoe = infer_required_yoe(jd_text)
        seniority = infer_seniority(jd_text)
        candidate_yoe = profile.years_experience

        if required_yoe is None:
            return 10, "No experience requirement stated"

        if candidate_yoe >= required_yoe:
            surplus = candidate_yoe - required_yoe
            if surplus <= 3:
                return 20, f"Experience matches ({candidate_yoe:.0f}y vs {required_yoe}+ required)"
            else:
                # Overqualified — slight penalty
                return 14, f"Overqualified ({candidate_yoe:.0f}y vs {required_yoe}+ required)"
        else:
            gap = required_yoe - candidate_yoe
            if gap <= 1:
                return 12, f"Slightly under required ({candidate_yoe:.0f}y vs {required_yoe}+ required)"
            elif gap <= 2:
                return 6, f"Under required by {gap:.0f}y"
            else:
                return 0, f"Significantly under required ({gap:.0f}y gap)"

    def _domain_score(
        self,
        jd_text: str,
        profile: CandidateProfile,
    ) -> tuple[int, str]:
        """Domain alignment — max 10 points."""
        jd_lower = jd_text.lower()
        matched_domains = [
            d for d in profile.domain_expertise
            if d.lower() in jd_lower
        ]
        matched_industry = [
            k for k in profile.industry_keywords
            if k.lower() in jd_lower
        ]
        total_matches = len(matched_domains) + len(matched_industry)
        score = min(10, total_matches * 2)
        reason = f"Domain match: {', '.join(matched_domains[:3])}" if matched_domains else ""
        return score, reason

    # ── Claude deep analysis ──────────────────────────────────────────────────

    def _claude_analysis(
        self,
        job: JobListing,
        profile: CandidateProfile,
    ) -> tuple[int, list[str], list[str]]:
        """
        Ask Claude to evaluate the fit holistically.
        Returns (bonus_points_0_10, interview_topics, gaps).
        Uses prompt caching on the profile context.
        """
        profile_context = json.dumps({
            "name": profile.name,
            "current_title": profile.current_title,
            "years_experience": profile.years_experience,
            "skills": profile.skills_flat()[:30],
            "domain_expertise": profile.domain_expertise,
            "recent_titles": [e.title for e in profile.work_experience[:3]],
        })

        jd_snippet = (job.description or "")[:3000]

        try:
            response = self.client.messages.create(
                max_tokens=512,
                system=[
                    {
                        "type": "text",
                        "text": (
                            "You are a technical recruiter evaluating candidate fit. "
                            "Return ONLY valid JSON with keys: "
                            "bonus_points (0-10 int), "
                            "interview_topics (list of 3-5 strings), "
                            "gaps (list of 0-3 strings). "
                            "bonus_points reflects how well the candidate's background "
                            "aligns with this specific role beyond keyword matching."
                        ),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Candidate profile:\n{profile_context}\n\n"
                            f"Job: {job.title} at {job.company}\n"
                            f"Description:\n{jd_snippet}"
                        ),
                    }
                ],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            bonus = int(data.get("bonus_points") or 0)
            topics = data.get("interview_topics") or []
            gaps = data.get("gaps") or []
            return min(10, max(0, bonus)), topics, gaps
        except Exception as e:
            logger.debug("Claude analysis failed for %r: %s", job.title, e)
            return 0, [], []
