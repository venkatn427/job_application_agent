"""
Job Scraper — BeautifulSoup / requests
----------------------------------------
Scrapes job postings from multiple sources and returns structured dicts
that are ready to pass directly to the MCP store_job_application tool.

Supported scrapers:
  • IndeedScraper   — indeed.com search results
  • RemoteOKScraper — remoteok.com public JSON API
  • HNJobsScraper   — news.ycombinator.com/jobs (Who is Hiring threads)

Usage (standalone):
    python scraper.py

Usage (from agent / MCP workflow):
    from scraper import scrape_all
    jobs = scrape_all(["indeed:senior data engineer:Remote", "remoteok:python"])

Install deps:
    pip install requests beautifulsoup4 lxml
"""

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        # Mimic a real browser to reduce bot-detection blocks
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)

REQUEST_TIMEOUT = 15   # seconds
RATE_LIMIT_DELAY = 1.5 # seconds between requests — be polite


def _get(url: str, **kwargs) -> Optional[requests.Response]:
    """GET with error handling; returns None on failure."""
    try:
        resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.HTTPError as e:
        logger.warning("HTTP %s for %s", e.response.status_code, url)
    except requests.RequestException as e:
        logger.warning("Request failed for %s: %s", url, e)
    return None


# ---------------------------------------------------------------------------
# Base scraper interface
# ---------------------------------------------------------------------------

@dataclass
class JobPost:
    """Canonical job record — matches the MCP JobApplication schema."""
    title:          str
    company:        str
    location:       Optional[str]       = None
    job_type:       Optional[str]       = None
    salary_range:   Optional[str]       = None
    description:    Optional[str]       = None
    requirements:   list[str]           = field(default_factory=list)
    nice_to_have:   list[str]           = field(default_factory=list)
    apply_url:      Optional[str]       = None
    posted_date:    Optional[str]       = None
    company_about:  Optional[str]       = None
    contact_email:  Optional[str]       = None
    source:         Optional[str]       = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class BaseScraper(ABC):
    """All scrapers implement this interface."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        ...

    def _soup(self, url: str, **kwargs) -> Optional[BeautifulSoup]:
        resp = _get(url, **kwargs)
        if resp is None:
            return None
        return BeautifulSoup(resp.text, "lxml")

    @staticmethod
    def _clean(text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return re.sub(r"\s+", " ", text).strip() or None


# ---------------------------------------------------------------------------
# Indeed scraper
# ---------------------------------------------------------------------------

class IndeedScraper(BaseScraper):
    """
    Scrapes Indeed job search results.

    NOTE: Indeed aggressively detects bots.  When the scraper is blocked you
    will receive an empty list and a warning.  For production use consider
    pairing this with a rotating proxy or Playwright.
    """

    name = "indeed"
    BASE_URL = "https://www.indeed.com/jobs"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        jobs: list[JobPost] = []
        start = 0
        page_size = 15  # Indeed shows ~15 per page

        while len(jobs) < max_results:
            params = {"q": query, "l": location, "start": start, "remotejob": "032b3046-06a3-4876-8dfd-474eb5e7ed11"}
            url = f"{self.BASE_URL}?{urlencode(params)}"
            logger.info("[indeed] Fetching: %s", url)

            soup = self._soup(url)
            if soup is None:
                break

            cards = soup.select("div.job_seen_beacon, div[data-testid='jobListing']")
            if not cards:
                logger.warning("[indeed] No job cards found — page structure may have changed or bot-blocked")
                break

            for card in cards:
                if len(jobs) >= max_results:
                    break
                post = self._parse_card(card)
                if post:
                    jobs.append(post)
                time.sleep(RATE_LIMIT_DELAY)

            if len(cards) < page_size:
                break  # last page
            start += page_size

        return jobs

    def _parse_card(self, card: BeautifulSoup) -> Optional[JobPost]:
        try:
            title_el  = card.select_one("h2.jobTitle span[title], h2.jobTitle a span")
            company_el = card.select_one("span[data-testid='company-name'], span.companyName")
            location_el = card.select_one("div[data-testid='text-location'], div.companyLocation")
            salary_el  = card.select_one("div[data-testid='attribute_snippet_testid'], div.salary-snippet-container")
            link_el    = card.select_one("h2.jobTitle a, a[id^='job_']")

            title   = self._clean(title_el.get_text()   if title_el   else None)
            company = self._clean(company_el.get_text() if company_el else None)

            if not title or not company:
                return None

            location  = self._clean(location_el.get_text() if location_el else None)
            salary    = self._clean(salary_el.get_text()   if salary_el   else None)

            apply_url = None
            if link_el:
                href = link_el.get("href", "")
                apply_url = urljoin("https://www.indeed.com", href)

            description = self._fetch_description(apply_url) if apply_url else None
            job_type    = self._infer_job_type(location, description)

            return JobPost(
                title=title,
                company=company,
                location=location,
                job_type=job_type,
                salary_range=salary,
                description=description,
                apply_url=apply_url,
                source="indeed",
            )
        except Exception as e:
            logger.debug("[indeed] Card parse error: %s", e)
            return None

    def _fetch_description(self, url: str) -> Optional[str]:
        """Follow the job detail link and extract the description text."""
        time.sleep(RATE_LIMIT_DELAY)
        soup = self._soup(url)
        if soup is None:
            return None
        desc_el = soup.select_one("div#jobDescriptionText, div.jobsearch-jobDescriptionText")
        return self._clean(desc_el.get_text(separator=" ") if desc_el else None)

    @staticmethod
    def _infer_job_type(location: Optional[str], description: Optional[str]) -> Optional[str]:
        text = f"{location or ''} {description or ''}".lower()
        if "remote" in text:
            return "remote"
        if "contract" in text or "contractor" in text:
            return "contract"
        if "part-time" in text or "part time" in text:
            return "part-time"
        if "full-time" in text or "full time" in text:
            return "full-time"
        return None


# ---------------------------------------------------------------------------
# RemoteOK scraper (public JSON API — no HTML parsing needed)
# ---------------------------------------------------------------------------

class RemoteOKScraper(BaseScraper):
    """
    Uses the RemoteOK public JSON API.  No auth required, very reliable.
    Query is matched against title + tags.
    """

    name = "remoteok"
    API_URL = "https://remoteok.com/api"

    def search(self, query: str, _location: str = "", max_results: int = 10) -> list[JobPost]:
        logger.info("[remoteok] Fetching public API…")
        resp = _get(self.API_URL)
        if resp is None:
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning("[remoteok] JSON parse failed: %s", e)
            return []

        # First element is a metadata dict — skip it
        postings = [p for p in data if isinstance(p, dict) and p.get("position")]

        keywords = query.lower().split()
        matched: list[JobPost] = []

        for p in postings:
            if len(matched) >= max_results:
                break

            title = p.get("position", "")
            tags  = " ".join(p.get("tags", []))
            text  = f"{title} {tags}".lower()

            if all(kw in text for kw in keywords):
                salary_raw = None
                if p.get("salary_min") and p.get("salary_max"):
                    salary_raw = f"${p['salary_min']:,}–${p['salary_max']:,}"

                matched.append(
                    JobPost(
                        title=self._clean(title) or "Unknown",
                        company=self._clean(p.get("company", "Unknown")) or "Unknown",
                        location="Remote",
                        job_type="remote",
                        salary_range=salary_raw,
                        description=self._clean(
                            BeautifulSoup(p.get("description", ""), "lxml").get_text(separator=" ")
                        ),
                        apply_url=p.get("url") or p.get("apply_url"),
                        posted_date=p.get("date"),
                        source="remoteok",
                    )
                )

        return matched


# ---------------------------------------------------------------------------
# Hacker News "Who is Hiring" scraper
# ---------------------------------------------------------------------------

class HNJobsScraper(BaseScraper):
    """
    Scrapes the latest HN 'Ask HN: Who is Hiring?' thread via the
    Algolia HN Search API (free, no auth).
    """

    name = "hn"
    ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"

    def search(self, query: str, _location: str = "", max_results: int = 10) -> list[JobPost]:
        # Find the most recent "Who is Hiring" thread
        thread_id = self._find_latest_hiring_thread()
        if not thread_id:
            logger.warning("[hn] Could not find latest 'Who is Hiring' thread")
            return []

        params = {
            "tags": f"comment,story_{thread_id}",
            "query": query,
            "hitsPerPage": max_results,
        }
        url = f"{self.ALGOLIA_URL}?{urlencode(params)}"
        logger.info("[hn] Searching HN thread %s for '%s'", thread_id, query)

        resp = _get(url)
        if resp is None:
            return []

        try:
            hits = resp.json().get("hits", [])
        except Exception as e:
            logger.warning("[hn] JSON parse failed: %s", e)
            return []

        jobs: list[JobPost] = []
        for hit in hits:
            post = self._parse_hit(hit)
            if post:
                jobs.append(post)

        return jobs

    def _find_latest_hiring_thread(self) -> Optional[str]:
        params = {
            "query": "Ask HN: Who is hiring?",
            "tags": "story,ask_hn",
            "hitsPerPage": 1,
        }
        url = f"https://hn.algolia.com/api/v1/search_by_date?{urlencode(params)}"
        resp = _get(url)
        if resp is None:
            return None
        try:
            hits = resp.json().get("hits", [])
            return str(hits[0]["objectID"]) if hits else None
        except (KeyError, IndexError, Exception):
            return None

    def _parse_hit(self, hit: dict) -> Optional[JobPost]:
        text = hit.get("comment_text", "") or ""
        soup = BeautifulSoup(text, "lxml")
        clean_text = soup.get_text(separator="\n")

        lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
        if not lines:
            return None

        # Heuristic: first line often contains "Company | Role | Location"
        header = lines[0]
        parts  = re.split(r"\s*[|–-]\s*", header)

        company = self._clean(parts[0]) if parts else "Unknown"
        title   = self._clean(parts[1]) if len(parts) > 1 else "Software Engineer"
        location = self._clean(parts[2]) if len(parts) > 2 else None

        # Detect remote
        full_text_lower = clean_text.lower()
        if "remote" in full_text_lower:
            location = location or "Remote"
            job_type = "remote"
        else:
            job_type = None

        # Extract salary mentions
        salary_match = re.search(r"\$[\d,]+k?\s*[-–]\s*\$[\d,]+k?", clean_text, re.IGNORECASE)
        salary_range = salary_match.group(0) if salary_match else None

        # Extract email
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", clean_text)
        contact_email = email_match.group(0) if email_match else None

        return JobPost(
            title=title or "Software Engineer",
            company=company or "Unknown",
            location=location,
            job_type=job_type,
            salary_range=salary_range,
            description=clean_text[:3000],  # cap length
            apply_url=f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
            posted_date=hit.get("created_at"),
            contact_email=contact_email,
            source="hn",
        )


# ---------------------------------------------------------------------------
# Remotive scraper (public JSON API)
# ---------------------------------------------------------------------------

class RemotiveScraper(BaseScraper):
    """
    Remotive.com public API — no auth, reliable.
    https://remotive.com/api/remote-jobs
    """

    name = "remotive"
    API_URL = "https://remotive.com/api/remote-jobs"

    def search(self, query: str, _location: str = "", max_results: int = 10) -> list[JobPost]:
        params = {"search": query, "limit": max_results * 2}
        logger.info("[remotive] Fetching API for query=%r", query)
        resp = _get(self.API_URL, params=params)
        if resp is None:
            return []
        try:
            jobs_raw = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning("[remotive] JSON parse failed: %s", e)
            return []

        results: list[JobPost] = []
        for j in jobs_raw:
            if len(results) >= max_results:
                break
            salary_raw = j.get("salary") or None
            results.append(
                JobPost(
                    title=self._clean(j.get("title", "")) or "Unknown",
                    company=self._clean(j.get("company_name", "")) or "Unknown",
                    location=self._clean(j.get("candidate_required_location")) or "Remote",
                    job_type=self._clean(j.get("job_type")) or "remote",
                    salary_range=self._clean(salary_raw),
                    description=self._clean(
                        BeautifulSoup(j.get("description", ""), "lxml").get_text(separator=" ")
                    ),
                    apply_url=j.get("url"),
                    posted_date=j.get("publication_date"),
                    company_about=self._clean(j.get("company_logo_url")),
                    source="remotive",
                )
            )
        return results


# ---------------------------------------------------------------------------
# Jobicy scraper (public JSON API)
# ---------------------------------------------------------------------------

class JobicyScraper(BaseScraper):
    """
    Jobicy.com public API — no auth, reliable.
    https://jobicy.com/api/v2/remote-jobs
    """

    name = "jobicy"
    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        params = {"count": min(max_results * 2, 50), "tag": query}
        if location:
            params["geo"] = location
        logger.info("[jobicy] Fetching API for query=%r", query)
        resp = _get(self.API_URL, params=params)
        if resp is None:
            return []
        try:
            jobs_raw = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning("[jobicy] JSON parse failed: %s", e)
            return []

        results: list[JobPost] = []
        for j in jobs_raw:
            if len(results) >= max_results:
                break
            results.append(
                JobPost(
                    title=self._clean(j.get("jobTitle", "")) or "Unknown",
                    company=self._clean(j.get("companyName", "")) or "Unknown",
                    location=self._clean(j.get("jobGeo")) or "Remote",
                    job_type=self._clean(
                        j["jobType"][0] if isinstance(j.get("jobType"), list) and j["jobType"]
                        else j.get("jobType")
                    ) or "remote",
                    salary_range=(
                        f"${j['salaryMin']:,}–${j.get('salaryMax', j['salaryMin']):,}"
                        if j.get("salaryMin") else None
                    ),
                    description=self._clean(
                        BeautifulSoup(j.get("jobDescription", ""), "lxml").get_text(separator=" ")
                    ),
                    requirements=j.get("jobIndustry", []) if isinstance(j.get("jobIndustry"), list) else [],
                    apply_url=j.get("url"),
                    posted_date=j.get("pubDate"),
                    company_about=self._clean(j.get("companyTagline")),
                    source="jobicy",
                )
            )
        return results


# ---------------------------------------------------------------------------
# ArbeitNow scraper (public JSON API — European + remote roles)
# ---------------------------------------------------------------------------

class ArbeitNowScraper(BaseScraper):
    """
    Arbeitnow.com public job board API — free, no auth.
    https://www.arbeitnow.com/api/job-board-api
    """

    name = "arbeitnow"
    API_URL = "https://www.arbeitnow.com/api/job-board-api"

    def search(self, query: str, _location: str = "", max_results: int = 10) -> list[JobPost]:
        logger.info("[arbeitnow] Fetching API for query=%r", query)
        resp = _get(self.API_URL)
        if resp is None:
            return []
        try:
            jobs_raw = resp.json().get("data", [])
        except Exception as e:
            logger.warning("[arbeitnow] JSON parse failed: %s", e)
            return []

        keywords = query.lower().split()
        results: list[JobPost] = []
        for j in jobs_raw:
            if len(results) >= max_results:
                break
            text = f"{j.get('title', '')} {' '.join(j.get('tags', []))}".lower()
            if not all(kw in text for kw in keywords):
                continue
            loc = "Remote" if j.get("remote") else self._clean(j.get("location")) or "Unknown"
            results.append(
                JobPost(
                    title=self._clean(j.get("title", "")) or "Unknown",
                    company=self._clean(j.get("company_name", "")) or "Unknown",
                    location=loc,
                    job_type="remote" if j.get("remote") else "full-time",
                    description=self._clean(
                        BeautifulSoup(j.get("description", ""), "lxml").get_text(separator=" ")
                    ),
                    requirements=j.get("tags", []),
                    apply_url=j.get("url"),
                    posted_date=j.get("created_at"),
                    source="arbeitnow",
                )
            )
        return results


# ---------------------------------------------------------------------------
# WeWorkRemotely scraper (HTML — structured category pages)
# ---------------------------------------------------------------------------

class WeWorkRemotelyScraper(BaseScraper):
    """
    Scrapes WeWorkRemotely search results.  Their HTML is clean and stable.
    https://weworkremotely.com/remote-jobs/search?term=<query>
    """

    name = "weworkremotely"
    SEARCH_URL = "https://weworkremotely.com/remote-jobs/search"

    def search(self, query: str, _location: str = "", max_results: int = 10) -> list[JobPost]:
        params = {"term": query}
        logger.info("[wwr] Searching for query=%r", query)
        soup = self._soup(self.SEARCH_URL, params=params)
        if soup is None:
            return []

        results: list[JobPost] = []
        # Listings are <li class="new-listing-container ..."> inside <section class="jobs">
        for li in soup.select("section.jobs li.new-listing-container"):
            if len(results) >= max_results:
                break
            post = self._parse_li(li)
            if post:
                results.append(post)
                time.sleep(RATE_LIMIT_DELAY)

        return results

    def _parse_li(self, li: BeautifulSoup) -> Optional[JobPost]:
        try:
            # Job detail link — skip "view-all" entries
            job_link = li.select_one("a[href*='/remote-jobs/']:not([href*='categories'])")
            if not job_link:
                return None

            href = job_link.get("href", "")
            apply_url = f"https://weworkremotely.com{href}" if href.startswith("/") else href

            # Title and company from visible text spans
            spans = li.find_all("span")
            text_spans = [self._clean(s.get_text()) for s in spans if self._clean(s.get_text())]

            # Company link is usually separate from job link
            company_link = li.select_one("a[href*='/company/']")
            company = self._clean(company_link.get_text()) if company_link else "Unknown"

            # Full text of the li for title heuristic
            full_text = self._clean(li.get_text(separator=" ")) or ""
            # Title is usually the longest meaningful span that isn't the company
            title_candidates = [t for t in text_spans if len(t) > 5 and t != company]
            title = title_candidates[0] if title_candidates else full_text[:80]

            description = self._fetch_detail(apply_url)

            return JobPost(
                title=title or "Unknown",
                company=company,
                location="Remote",
                job_type="remote",
                description=description,
                apply_url=apply_url,
                source="weworkremotely",
            )
        except Exception as e:
            logger.debug("[wwr] Parse error: %s", e)
            return None

    def _fetch_detail(self, url: str) -> Optional[str]:
        time.sleep(RATE_LIMIT_DELAY)
        soup = self._soup(url)
        if soup is None:
            return None
        desc_el = soup.select_one("div.job-listing-container, div#job-listing, div.listing-container")
        return self._clean(desc_el.get_text(separator=" ") if desc_el else None)


# ---------------------------------------------------------------------------
# LinkedIn scraper (unofficial search — public job listings only)
# ---------------------------------------------------------------------------

class LinkedInScraper(BaseScraper):
    """
    Scrapes LinkedIn public job search (no login required for listings page).
    Uses the guest search endpoint. Rate-limited; works best with RATE_LIMIT_DELAY >= 2s.
    """

    name = "linkedin"
    SEARCH_URL = "https://www.linkedin.com/jobs/search/"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        params = {
            "keywords":      query,
            "location":      location or "Worldwide",
            "f_WT":          "2",   # remote flag
            "position":      1,
            "pageNum":       0,
            "count":         min(max_results, 25),
        }
        logger.info("[linkedin] Searching for %r in %r", query, location)
        soup = self._soup(self.SEARCH_URL, params=params)
        if soup is None:
            return []

        results: list[JobPost] = []
        cards = soup.select("div.base-card, li.jobs-search__results-list > div")
        if not cards:
            # Fallback selector for newer LinkedIn markup
            cards = soup.find_all("div", class_=lambda c: c and "job-search-card" in c)

        for card in cards:
            if len(results) >= max_results:
                break
            post = self._parse_card(card)
            if post:
                results.append(post)

        return results

    def _parse_card(self, card: BeautifulSoup) -> Optional[JobPost]:
        try:
            title_el   = card.select_one("h3.base-search-card__title, span.sr-only")
            company_el = card.select_one("h4.base-search-card__subtitle, a.hidden-nested-link")
            location_el = card.select_one("span.job-search-card__location")
            link_el    = card.select_one("a.base-card__full-link, a[data-tracking-control-name]")

            title   = self._clean(title_el.get_text()) if title_el else None
            company = self._clean(company_el.get_text()) if company_el else "Unknown"
            location = self._clean(location_el.get_text()) if location_el else None
            apply_url = link_el.get("href") if link_el else None

            if not title:
                return None

            return JobPost(
                title=title,
                company=company,
                location=location,
                apply_url=apply_url,
                job_type=self._infer_type(location),
                source="linkedin",
            )
        except Exception as e:
            logger.debug("[linkedin] Card parse error: %s", e)
            return None

    @staticmethod
    def _infer_type(location: Optional[str]) -> Optional[str]:
        if location and "remote" in location.lower():
            return "remote"
        return "full-time"


# ---------------------------------------------------------------------------
# The Muse scraper (public JSON API — company job board aggregator)
# ---------------------------------------------------------------------------

class TheMuseScraper(BaseScraper):
    """
    The Muse public API — no auth, 100% reliable.
    https://www.themuse.com/api/public/jobs
    Covers thousands of company career pages with curated content.
    """

    name = "themuse"
    API_URL = "https://www.themuse.com/api/public/jobs"

    # Map common query terms to Muse category slugs
    CATEGORY_MAP = {
        "data engineer":      "Data Science",
        "data science":       "Data Science",
        "software engineer":  "Software Engineer",
        "backend":            "Software Engineer",
        "frontend":           "Software Engineer",
        "python":             "Software Engineer",
        "devops":             "IT",
        "machine learning":   "Data Science",
        "product manager":    "Project Management",
        "designer":           "Design & UX",
        "marketing":          "Marketing & PR",
        "sales":              "Sales",
        "finance":            "Finance",
    }

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        # Match query to a Muse category slug
        category = None
        for kw, cat in self.CATEGORY_MAP.items():
            if kw in query.lower():
                category = cat
                break

        query_keywords = query.lower().split()
        all_results: list[JobPost] = []
        page = 0  # Muse pages are 0-indexed

        while len(all_results) < max_results:
            params: dict = {"page": page}
            if category:
                params["category"] = category
            logger.info("[themuse] page=%d category=%r query=%r", page, category, query)
            resp = _get(self.API_URL, params=params)
            if resp is None:
                break
            try:
                data = resp.json()
            except Exception as e:
                logger.warning("[themuse] JSON parse failed: %s", e)
                break

            jobs_raw = data.get("results", [])
            if not jobs_raw:
                break

            for j in jobs_raw:
                if len(all_results) >= max_results:
                    break
                title = j.get("name", "")
                # Require at least one query keyword in title or category matched
                title_lower = title.lower()
                if not category and not any(kw in title_lower for kw in query_keywords):
                    continue

                locations = [loc["name"] for loc in j.get("locations", [])]
                loc_str = ", ".join(locations) if locations else "Remote"

                if location and location.lower() not in ("remote", ""):
                    if not any(location.lower() in l.lower() for l in locations):
                        continue

                levels = [lv["name"] for lv in j.get("levels", [])]
                all_results.append(JobPost(
                    title=self._clean(title) or "Unknown",
                    company=self._clean(j.get("company", {}).get("name", "")) or "Unknown",
                    location=loc_str,
                    job_type="remote" if any("remote" in l.lower() for l in locations) else "full-time",
                    description=self._clean(
                        BeautifulSoup(j.get("contents", ""), "lxml").get_text(separator=" ")
                    ),
                    requirements=levels,
                    apply_url=j.get("refs", {}).get("landing_page"),
                    posted_date=(j.get("publication_date") or "")[:10] or None,
                    source="themuse",
                ))

            if len(jobs_raw) < 20:
                break  # last page
            page += 1

        return all_results[:max_results]


# ---------------------------------------------------------------------------
# GitHub Jobs via GitHub API (real company postings)
# ---------------------------------------------------------------------------

class GitHubJobsScraper(BaseScraper):
    """
    Searches GitHub Discussions and public repositories that list job openings.
    Uses GitHub REST API — no auth needed for public content (60 req/hr unauthenticated).
    Also fetches from github.com/tramcar/awesome-job-boards style repos.
    """

    name = "github"
    API_URL = "https://api.github.com/search/repositories"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        # Search for job board repos and parse README job listings
        gh_headers = {"Accept": "application/vnd.github.v3+json"}
        params     = {"q": f"{query} jobs hiring in:readme", "sort": "updated", "per_page": 5}
        logger.info("[github] Searching repos for %r", query)
        resp = _get(self.API_URL, params=params, headers=gh_headers)
        if resp is None:
            return []

        try:
            repos = resp.json().get("items", [])
        except Exception:
            return []

        results: list[JobPost] = []
        for repo in repos:
            if len(results) >= max_results:
                break
            readme_url  = f"https://api.github.com/repos/{repo['full_name']}/readme"
            readme_resp = _get(readme_url, headers={**gh_headers, "Accept": "application/vnd.github.raw"})
            if readme_resp is None:
                continue
            for line in readme_resp.text.splitlines():
                if len(results) >= max_results:
                    break
                line = line.strip()
                if not line or len(line) < 20:
                    continue
                lower = line.lower()
                if any(kw in lower for kw in ["hiring", "job", "engineer", "developer", query.lower()]):
                    results.append(JobPost(
                        title=line[:120],
                        company=repo.get("owner", {}).get("login", "Unknown"),
                        location=location or "Remote",
                        description=f"From GitHub repo: {repo['html_url']}\n\n{line}",
                        apply_url=repo["html_url"],
                        source="github",
                    ))

        return results


# ---------------------------------------------------------------------------
# Y Combinator companies job board (via workatastartup.com)
# ---------------------------------------------------------------------------

class YCombinatorScraper(BaseScraper):
    """
    Scrapes jobs from workatastartup.com — Y Combinator's official job board.
    No auth required for search.
    """

    name = "ycombinator"
    SEARCH_URL = "https://www.workatastartup.com/jobs"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        import json as _json
        params = {"q": query, "remote": "true" if "remote" in location.lower() else ""}
        logger.info("[ycombinator] Searching workatastartup for %r", query)
        soup = self._soup(self.SEARCH_URL, params=params)
        if soup is None:
            return []

        results: list[JobPost] = []

        # Jobs are rendered via a JSON data attribute on the page
        script = soup.find("script", {"data-component-name": True})
        if not script:
            # Fallback: parse visible job cards
            for div in soup.select("div.job-name, div[class*='job']")[:max_results]:
                title = self._clean(div.get_text())
                if title and len(title) > 5:
                    results.append(JobPost(
                        title=title,
                        company="YC Company",
                        location=location or "Remote",
                        apply_url=self.SEARCH_URL,
                        source="ycombinator",
                    ))
            return results

        try:
            data = _json.loads(script.string or "{}")
            jobs_raw = data.get("jobs", [])
            for j in jobs_raw:
                if len(results) >= max_results:
                    break
                results.append(JobPost(
                    title=self._clean(j.get("title", "")) or "Unknown",
                    company=self._clean(j.get("company", {}).get("name", "")) or "Unknown",
                    location=self._clean(j.get("location")) or "Remote",
                    job_type="remote" if j.get("remote") else "full-time",
                    description=self._clean(j.get("description", "")),
                    apply_url=j.get("url"),
                    source="ycombinator",
                ))
        except Exception as e:
            logger.debug("[ycombinator] JSON parse failed: %s", e)

        return results


# ---------------------------------------------------------------------------
# Dice scraper (HTML — major US tech job board)
# ---------------------------------------------------------------------------

class DiceScraper(BaseScraper):
    """
    Scrapes Dice.com using their internal search API (same endpoint the site uses).
    Returns JSON — no auth required for public listings.
    """

    name = "dice"
    SEARCH_URL = "https://www.dice.com/jobs"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        logger.info("[dice] Scraping HTML for %r in %r", query, location)
        params = {"q": query, "location": location or "Remote", "countryCode": "US"}
        soup = self._soup(self.SEARCH_URL, params=params)
        if soup is None:
            return []
        results = []
        for card in soup.select("dhi-search-card, div[data-cy='search-result']")[:max_results]:
            title_el   = card.select_one("a.card-title-link, h5 a, a[data-cy='card-title-link']")
            company_el = card.select_one("a.company-name-link, span.company-name, a[data-cy='search-result-company-name']")
            loc_el     = card.select_one("span.search-result-location, span[data-cy='search-result-location']")
            link_el    = card.select_one("a.card-title-link, a[href*='/job-detail/']")
            title   = self._clean(title_el.get_text()) if title_el else None
            company = self._clean(company_el.get_text()) if company_el else "Unknown"
            loc     = self._clean(loc_el.get_text()) if loc_el else location or "US"
            url     = link_el.get("href") if link_el else None
            if url and not url.startswith("http"):
                url = f"https://www.dice.com{url}"
            if title:
                results.append(JobPost(
                    title=title, company=company, location=loc,
                    apply_url=url, source="dice",
                ))
        return results


# ---------------------------------------------------------------------------
# Monster scraper (HTML — global job board)
# ---------------------------------------------------------------------------

class CareerjetScraper(BaseScraper):
    """
    Scrapes Careerjet.com — large global aggregator with clean HTML.
    No auth, no bot-blocking, works reliably.
    """

    name = "careerjet"
    SEARCH_URL = "https://www.careerjet.com/search/jobs"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        params = {"s": query, "l": location or "remote", "p": 1}
        logger.info("[careerjet] Searching for %r in %r", query, location)
        soup = self._soup(self.SEARCH_URL, params=params)
        if soup is None:
            return []

        results: list[JobPost] = []
        for article in soup.select("article.job")[:max_results]:
            try:
                title_el   = article.select_one("h2 a, header a")
                company_el = article.select_one("p.company")
                loc_el     = article.select_one("ul.location li, p.location")
                desc_el    = article.select_one("div.desc, p.desc")
                link_el    = article.select_one("h2 a[href]")

                title   = self._clean(title_el.get_text()) if title_el else None
                company = self._clean(company_el.get_text()) if company_el else "Unknown"
                loc     = self._clean(loc_el.get_text()) if loc_el else location or "Unknown"
                desc    = self._clean(desc_el.get_text()) if desc_el else None

                href = link_el.get("href", "") if link_el else ""
                url  = f"https://www.careerjet.com{href}" if href.startswith("/") else href

                if not title:
                    continue

                results.append(JobPost(
                    title=title,
                    company=company,
                    location=loc,
                    job_type="remote" if "remote" in loc.lower() else None,
                    description=desc,
                    apply_url=url or None,
                    source="careerjet",
                ))
            except Exception as e:
                logger.debug("[careerjet] Parse error: %s", e)

        return results


# ---------------------------------------------------------------------------
# Monster — graceful note (blocks without a browser)
# ---------------------------------------------------------------------------

class MonsterScraper(BaseScraper):
    """
    Monster.com returns 403 to plain requests. This stub returns an empty
    list and logs a warning rather than silently failing.
    """

    name = "monster"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        logger.warning(
            "[monster] Monster.com requires JavaScript rendering — skipping. "
            "Use 'careerjet', 'remoteok', or 'themuse' instead."
        )
        return []


# ---------------------------------------------------------------------------
# USAJobs scraper (US federal government jobs — public REST API)
# ---------------------------------------------------------------------------

class USAJobsScraper(BaseScraper):
    """
    US Federal Government job board via the official USAJobs REST API.
    Requires a free API key from https://developer.usajobs.gov/
    Falls back gracefully if no key is set.
    Set env var USAJOBS_API_KEY and USAJOBS_USER_AGENT to enable.
    """

    name = "usajobs"
    API_URL = "https://data.usajobs.gov/api/search"

    def search(self, query: str, location: str = "", max_results: int = 10) -> list[JobPost]:
        api_key    = os.environ.get("USAJOBS_API_KEY", "")
        user_agent = os.environ.get("USAJOBS_USER_AGENT", "jobagent@example.com")
        if not api_key:
            logger.warning("[usajobs] No USAJOBS_API_KEY set — skipping")
            return []

        params = {
            "Keyword":        query,
            "LocationName":   location or "Remote",
            "ResultsPerPage": min(max_results, 25),
        }
        headers = {
            "Authorization-Key": api_key,
            "User-Agent":        user_agent,
            "Host":              "data.usajobs.gov",
        }
        logger.info("[usajobs] Searching for %r in %r", query, location)
        resp = _get(self.API_URL, params=params, headers=headers)
        if resp is None:
            return []

        try:
            items = resp.json()["SearchResult"]["SearchResultItems"]
        except Exception as e:
            logger.warning("[usajobs] Response parse failed: %s", e)
            return []

        results = []
        for item in items[:max_results]:
            j = item.get("MatchedObjectDescriptor", {})
            locs = [pos.get("LocationName", "") for pos in j.get("PositionLocation", [])]
            sal = j.get("PositionRemuneration", [{}])[0]
            salary = None
            if sal.get("MinimumRange") and sal.get("MaximumRange"):
                salary = f"${float(sal['MinimumRange']):,.0f}–${float(sal['MaximumRange']):,.0f}"
            results.append(JobPost(
                title=self._clean(j.get("PositionTitle", "")) or "Unknown",
                company=self._clean(j.get("OrganizationName", "")) or "US Government",
                location=", ".join(locs[:2]) or location or "US",
                job_type="full-time",
                salary_range=salary,
                description=self._clean(j.get("QualificationSummary", "")),
                apply_url=j.get("PositionURI"),
                posted_date=(j.get("PublicationStartDate") or "")[:10] or None,
                source="usajobs",
            ))
        return results


# ---------------------------------------------------------------------------
# Dispatcher — parse "source:query:location" strings
# ---------------------------------------------------------------------------

_SCRAPERS: dict[str, type[BaseScraper]] = {
    "indeed":         IndeedScraper,
    "remoteok":       RemoteOKScraper,
    "hn":             HNJobsScraper,
    "remotive":       RemotiveScraper,
    "jobicy":         JobicyScraper,
    "arbeitnow":      ArbeitNowScraper,
    "weworkremotely": WeWorkRemotelyScraper,
    "linkedin":       LinkedInScraper,
    "glassdoor":      LinkedInScraper,   # JS-rendered; falls back to LinkedIn HTML
    "themuse":        TheMuseScraper,
    "github":         GitHubJobsScraper,
    "ycombinator":    YCombinatorScraper,
    "dice":           DiceScraper,
    "careerjet":      CareerjetScraper,
    "monster":        MonsterScraper,
    "usajobs":        USAJobsScraper,
}


def scrape_all(
    sources: list[str],
    max_per_source: int = 10,
) -> list[dict]:
    """
    Scrape multiple sources and return a flat list of job dicts.

    Source string format:  "scraper_name:search query:optional location"
    Examples:
        "indeed:senior data engineer:Remote"
        "remoteok:python"
        "hn:data engineer"

    Falls back to RemoteOK for bare URLs or unrecognised prefixes.
    """
    all_jobs: list[dict] = []

    for source in sources:
        parts    = source.split(":", 2)
        key      = parts[0].lower().strip()
        query    = parts[1].strip() if len(parts) > 1 else "software engineer"
        location = parts[2].strip() if len(parts) > 2 else ""

        scraper_cls = _SCRAPERS.get(key)
        if scraper_cls is None:
            logger.warning("Unknown scraper %r — skipping", key)
            continue

        scraper = scraper_cls()
        logger.info("Running %s scraper for query=%r location=%r", scraper.name, query, location)
        try:
            posts = scraper.search(query=query, location=location, max_results=max_per_source)
            logger.info("%s returned %d jobs", scraper.name, len(posts))
            all_jobs.extend(p.to_dict() for p in posts)
        except Exception as e:
            logger.error("Scraper %s crashed: %s", scraper.name, e, exc_info=True)

    return all_jobs


# ---------------------------------------------------------------------------
# Direct DB write (used when running standalone / from UI subprocess)
# ---------------------------------------------------------------------------

def _save_jobs_to_db(jobs: list[dict], db_path: str) -> tuple[int, int]:
    """
    Upsert a list of job dicts into the SQLite DB.
    Returns (inserted_count, updated_count).
    """
    import sqlite3
    import json as _json
    from datetime import datetime, timezone

    ddl = """
    CREATE TABLE IF NOT EXISTS job_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, company TEXT NOT NULL, location TEXT,
        job_type TEXT, salary_min INTEGER, salary_max INTEGER, salary_raw TEXT,
        description TEXT, requirements TEXT, nice_to_have TEXT,
        apply_url TEXT, posted_date TEXT, company_about TEXT,
        contact_email TEXT, source TEXT, scraped_at TEXT NOT NULL,
        UNIQUE(title, company, apply_url)
    );
    """

    salary_re = re.compile(r"\$?([\d,]+)k?", re.IGNORECASE)

    def parse_salary(raw):
        if not raw:
            return None, None
        matches = salary_re.findall(str(raw).replace(",", ""))
        vals = [int(m) * 1000 if int(m) < 1000 else int(m) for m in matches]
        if not vals:
            return None, None
        return min(vals), max(vals)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(ddl)

    inserted = updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for j in jobs:
        sal_min, sal_max = parse_salary(j.get("salary_range"))
        try:
            cur = conn.execute(
                """
                INSERT INTO job_applications
                    (title, company, location, job_type, salary_min, salary_max,
                     salary_raw, description, requirements, nice_to_have,
                     apply_url, posted_date, company_about, contact_email,
                     source, scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(title, company, apply_url) DO UPDATE SET
                    location=excluded.location, job_type=excluded.job_type,
                    salary_min=excluded.salary_min, salary_max=excluded.salary_max,
                    salary_raw=excluded.salary_raw, description=excluded.description,
                    requirements=excluded.requirements, nice_to_have=excluded.nice_to_have,
                    posted_date=excluded.posted_date, company_about=excluded.company_about,
                    contact_email=excluded.contact_email, source=excluded.source,
                    scraped_at=excluded.scraped_at
                """,
                (
                    str(j.get("title", "")).strip(),
                    str(j.get("company", "")).strip(),
                    j.get("location"),
                    j.get("job_type"),
                    sal_min, sal_max,
                    j.get("salary_range"),
                    j.get("description"),
                    _json.dumps(j["requirements"]) if j.get("requirements") else None,
                    _json.dumps(j["nice_to_have"]) if j.get("nice_to_have") else None,
                    j.get("apply_url"),
                    j.get("posted_date"),
                    j.get("company_about"),
                    j.get("contact_email"),
                    j.get("source"),
                    now,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            logger.warning("DB insert failed for %r: %s", j.get("title"), e)

    conn.commit()
    conn.close()
    return inserted, updated


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    from pathlib import Path as _Path

    # When launched by the Streamlit UI, these env vars are set
    ui_source   = os.environ.get("_UI_SOURCE", "")
    ui_max_jobs = int(os.environ.get("_UI_MAX_JOBS", "0"))
    db_path     = os.environ.get("JOB_DB_PATH", str(_Path(__file__).parent / "jobs.db"))

    if ui_source:
        # Called from the UI subprocess
        scrape_sources = [ui_source]
        max_jobs = ui_max_jobs or 10
        print(f"[scraper] UI mode: source={ui_source!r} max_jobs={max_jobs}", flush=True)
    else:
        # Standalone: read from config.py
        from config import JOB_CRITERIA
        raw_sources = JOB_CRITERIA.get("job_sources", [])
        max_jobs    = JOB_CRITERIA.get("max_jobs_to_scrape", 10)

        scrape_sources = []
        for src in raw_sources:
            if "remoteok" in src:
                scrape_sources.append("remoteok:python")
            elif "ycombinator" in src or "hacker" in src.lower():
                scrape_sources.append("hn:data engineer")
            elif "indeed" in src:
                scrape_sources.append("indeed:senior data engineer:Remote")
            else:
                scrape_sources.append("remoteok:python")

        if not scrape_sources:
            scrape_sources = ["remoteok:data engineer", "hn:data engineer"]

    print(f"[scraper] Scraping {len(scrape_sources)} source(s)…", flush=True)
    jobs = scrape_all(scrape_sources, max_per_source=max_jobs)

    print(f"[scraper] Found {len(jobs)} job(s).", flush=True)
    for i, j in enumerate(jobs, 1):
        print(f"  {i:>3}. {j['title']} @ {j['company']}  [{j.get('location', '?')}]  {j.get('salary_range') or ''}", flush=True)

    # Always write to DB
    print(f"\n[scraper] Saving to database: {db_path}", flush=True)
    ins, upd = _save_jobs_to_db(jobs, db_path)
    print(f"[scraper] Done — {ins} inserted, {upd} updated.", flush=True)

    # Also dump raw JSON for inspection
    out_path = _Path(__file__).parent / "scraped_jobs_raw.json"
    with open(out_path, "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"[scraper] Raw JSON saved to {out_path}", flush=True)
