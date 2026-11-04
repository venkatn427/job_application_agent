"""
Apify Job Scraper
-----------------
Fetches job listings from LinkedIn, Indeed, Greenhouse, and Lever
using Apify actors. Falls back to direct scrapers when Apify is unavailable.

Actor IDs used:
  - LinkedIn  : curious_coder~linkedin-jobs-scraper
  - Indeed    : misceres~indeed-scraper
  - Greenhouse: apify/website-content-crawler (generic)
  - Lever     : apify/website-content-crawler (generic)

Set APIFY_API_TOKEN in your .env to enable.
"""

import os
import json
import time
import logging
import hashlib
from typing import Optional
from dataclasses import dataclass, field, asdict
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"
REQUEST_TIMEOUT = 60
POLL_INTERVAL = 5      # seconds between run-status polls
MAX_POLL_WAIT = 300    # 5 minutes max wait per actor run


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class JobListing:
    title: str
    company: str
    location: Optional[str] = None
    job_type: Optional[str] = None
    salary_range: Optional[str] = None
    description: Optional[str] = None
    requirements: list[str] = field(default_factory=list)
    apply_url: Optional[str] = None
    posted_date: Optional[str] = None
    company_about: Optional[str] = None
    source: Optional[str] = None
    job_id: Optional[str] = None          # deduplication key

    def __post_init__(self):
        if not self.job_id:
            raw = f"{self.title}|{self.company}|{self.apply_url or ''}"
            self.job_id = hashlib.md5(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Apify HTTP client
# ─────────────────────────────────────────────────────────────────────────────

class ApifyClient:
    """Thin wrapper around the Apify REST API."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("APIFY_API_TOKEN", "")
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # Token in Authorization header — never expose it in URLs or log strings
        if self.token:
            self._session.headers["Authorization"] = f"Bearer {self.token}"

    def run_actor(self, actor_id: str, input_data: dict) -> Optional[str]:
        """Start an actor run and return the run ID.

        Actor IDs must use '~' as the owner/name separator (e.g. 'owner~actor-name'),
        not '/' which the Apify REST API rejects with 404.
        """
        url = f"{APIFY_BASE_URL}/acts/{actor_id}/runs"
        resp = self._session.post(url, json=input_data, timeout=REQUEST_TIMEOUT)
        if resp.status_code not in (200, 201):
            # Log only the status code — not the URL (which may contain sensitive paths)
            logger.error("Apify actor %s start failed: HTTP %s", actor_id, resp.status_code)
            return None
        run_id = resp.json().get("data", {}).get("id")
        logger.info("Started Apify actor %s → run %s", actor_id, run_id)
        return run_id

    def wait_for_run(self, run_id: str) -> bool:
        """Poll until the run finishes. Returns True on SUCCEEDED."""
        url = f"{APIFY_BASE_URL}/actor-runs/{run_id}"
        elapsed = 0
        while elapsed < MAX_POLL_WAIT:
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("Poll failed for run %s: %s", run_id, resp.status_code)
                return False
            status = resp.json().get("data", {}).get("status", "")
            if status == "SUCCEEDED":
                return True
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                logger.error("Apify run %s ended with status %s", run_id, status)
                return False
            logger.debug("Run %s status=%s — waiting %ds", run_id, status, POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
        logger.error("Timed out waiting for run %s", run_id)
        return False

    def get_dataset(self, run_id: str) -> list[dict]:
        """Fetch all items from the default dataset of a completed run."""
        run_url = f"{APIFY_BASE_URL}/actor-runs/{run_id}"
        run_resp = self._session.get(run_url, timeout=REQUEST_TIMEOUT)
        if run_resp.status_code != 200:
            return []
        dataset_id = run_resp.json().get("data", {}).get("defaultDatasetId")
        if not dataset_id:
            return []

        items_url = f"{APIFY_BASE_URL}/datasets/{dataset_id}/items"
        resp = self._session.get(items_url, params={"format": "json", "clean": "true"}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.error("Dataset fetch failed for run %s: HTTP %s", run_id, resp.status_code)
            return []
        return resp.json() if isinstance(resp.json(), list) else resp.json().get("items", [])

    def scrape(self, actor_id: str, input_data: dict) -> list[dict]:
        """Run actor synchronously and return dataset items."""
        if not self.token:
            logger.warning("No APIFY_API_TOKEN — skipping Apify actor %s", actor_id)
            return []
        run_id = self.run_actor(actor_id, input_data)
        if not run_id:
            return []
        if not self.wait_for_run(run_id):
            return []
        return self.get_dataset(run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Per-source scrapers
# ─────────────────────────────────────────────────────────────────────────────

class LinkedInApifyScraper:
    """
    Uses the 'curious_coder~linkedin-jobs-scraper' actor.
    Input schema: { searchUrl, maxJobs }
    """
    ACTOR_ID = "curious_coder~linkedin-jobs-scraper"

    def __init__(self, client: ApifyClient):
        self.client = client

    def search(self, query: str, location: str = "Remote", max_results: int = 25) -> list[JobListing]:
        params = urlencode({"keywords": query, "location": location, "f_WT": "2"})
        search_url = f"https://www.linkedin.com/jobs/search/?{params}"
        input_data = {"searchUrl": search_url, "maxJobs": max_results}

        logger.info("[linkedin/apify] Scraping: query=%r location=%r", query, location)
        items = self.client.scrape(self.ACTOR_ID, input_data)
        return [self._parse(i) for i in items if self._parse(i)]

    def _parse(self, item: dict) -> Optional[JobListing]:
        title = item.get("title") or item.get("jobTitle", "")
        company = item.get("company") or item.get("companyName", "")
        if not title or not company:
            return None
        return JobListing(
            title=str(title).strip(),
            company=str(company).strip(),
            location=item.get("location"),
            job_type=item.get("employmentType") or item.get("jobType"),
            salary_range=item.get("salary") or item.get("salaryRange"),
            description=item.get("description") or item.get("jobDescription"),
            apply_url=item.get("applyUrl") or item.get("jobUrl") or item.get("url"),
            posted_date=item.get("postedDate") or item.get("postedAt"),
            company_about=item.get("companyDescription"),
            source="linkedin",
        )


class IndeedApifyScraper:
    """
    Uses the 'misceres~indeed-scraper' actor.
    Input schema: { country, location, position, maxItems }
    """
    ACTOR_ID = "misceres~indeed-scraper"

    def __init__(self, client: ApifyClient):
        self.client = client

    def search(self, query: str, location: str = "Remote", max_results: int = 25) -> list[JobListing]:
        input_data = {
            "country": "US",
            "location": location,
            "position": query,
            "maxItems": max_results,
            "parseCompanyDetails": False,
            "saveOnlyUniqueItems": True,
        }
        logger.info("[indeed/apify] Scraping: query=%r location=%r", query, location)
        items = self.client.scrape(self.ACTOR_ID, input_data)
        return [j for j in (self._parse(i) for i in items) if j]

    def _parse(self, item: dict) -> Optional[JobListing]:
        title = item.get("positionName") or item.get("title", "")
        company = item.get("company", "")
        if not title or not company:
            return None
        salary_parts = []
        if item.get("salary"):
            salary_parts.append(item["salary"])
        return JobListing(
            title=str(title).strip(),
            company=str(company).strip(),
            location=item.get("location"),
            job_type=item.get("jobType"),
            salary_range=" ".join(salary_parts) or None,
            description=item.get("description") or item.get("jobDescription"),
            apply_url=item.get("url") or item.get("externalApplyLink"),
            posted_date=item.get("postedAt") or item.get("datePosted"),
            source="indeed",
        )


class GreenhouseScraper:
    """
    Greenhouse boards are public JSON APIs: https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    No Apify needed — we hit the API directly.
    """

    def search(self, companies: list[str], query: str = "", max_results: int = 25) -> list[JobListing]:
        jobs: list[JobListing] = []
        for company_slug in companies:
            if len(jobs) >= max_results:
                break
            try:
                url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning("[greenhouse] %s returned %s", company_slug, resp.status_code)
                    continue
                data = resp.json().get("jobs", [])
                for j in data:
                    if len(jobs) >= max_results:
                        break
                    title = j.get("title", "")
                    if query and query.lower() not in title.lower():
                        continue
                    loc = j.get("location", {}).get("name")
                    dept = j.get("departments", [{}])[0].get("name") if j.get("departments") else None
                    jobs.append(JobListing(
                        title=title.strip(),
                        company=company_slug.replace("-", " ").title(),
                        location=loc,
                        description=j.get("content", ""),
                        apply_url=j.get("absolute_url"),
                        posted_date=j.get("updated_at", "")[:10] or None,
                        requirements=[dept] if dept else [],
                        source="greenhouse",
                    ))
                logger.info("[greenhouse] %s → %d jobs", company_slug, len(data))
            except Exception as e:
                logger.warning("[greenhouse] Error for %s: %s", company_slug, e)
        return jobs


class LeverScraper:
    """
    Lever boards are public JSON APIs: https://api.lever.co/v0/postings/{company}?mode=json
    """

    def search(self, companies: list[str], query: str = "", max_results: int = 25) -> list[JobListing]:
        jobs: list[JobListing] = []
        for company_slug in companies:
            if len(jobs) >= max_results:
                break
            try:
                url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning("[lever] %s returned %s", company_slug, resp.status_code)
                    continue
                data = resp.json()
                if not isinstance(data, list):
                    data = data.get("data", [])
                for j in data:
                    if len(jobs) >= max_results:
                        break
                    title = j.get("text", "")
                    if query and query.lower() not in title.lower():
                        continue
                    categories = j.get("categories", {})
                    loc = categories.get("location") or categories.get("allLocations", [""])[0]
                    desc_parts = []
                    for section in j.get("descriptionPlain", "").split("\n"):
                        desc_parts.append(section)
                    jobs.append(JobListing(
                        title=title.strip(),
                        company=company_slug.replace("-", " ").title(),
                        location=loc,
                        job_type=categories.get("commitment"),
                        description="\n".join(desc_parts),
                        apply_url=j.get("hostedUrl") or j.get("applyUrl"),
                        posted_date=None,  # Lever doesn't expose posted_date in v0
                        source="lever",
                    ))
                logger.info("[lever] %s → %d jobs", company_slug, len(data))
            except Exception as e:
                logger.warning("[lever] Error for %s: %s", company_slug, e)
        return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Unified scraper facade
# ─────────────────────────────────────────────────────────────────────────────

class JobFetcher:
    """
    Coordinates all job board scrapers and returns a deduplicated list.

    Config keys (from settings.yaml → job_search):
      queries        : list of search strings
      locations      : list of locations
      max_per_source : max jobs per source
      greenhouse_companies : list of company slugs
      lever_companies      : list of company slugs
    """

    def __init__(self, apify_token: Optional[str] = None):
        self.client = ApifyClient(apify_token)
        self.linkedin = LinkedInApifyScraper(self.client)
        self.indeed = IndeedApifyScraper(self.client)
        self.greenhouse = GreenhouseScraper()
        self.lever = LeverScraper()
        self._seen: set[str] = set()

    def fetch_all(
        self,
        queries: list[str],
        locations: list[str] | None = None,
        max_per_source: int = 25,
        greenhouse_companies: list[str] | None = None,
        lever_companies: list[str] | None = None,
        sources: list[str] | None = None,
        exclude_titles: list[str] | None = None,
    ) -> list[JobListing]:
        """
        Scrape all configured sources and return deduplicated listings.
        sources: subset of ['linkedin','indeed','greenhouse','lever'] or None for all
        exclude_titles: words that, if found in a job title, cause it to be dropped
        """
        all_sources = sources or ["linkedin", "indeed", "greenhouse", "lever"]
        locations = locations or ["Remote"]
        greenhouse_companies = greenhouse_companies or []
        lever_companies = lever_companies or []
        _exclude = [e.lower() for e in (exclude_titles or [])]
        results: list[JobListing] = []

        for query in queries:
            for location in locations:
                if "linkedin" in all_sources:
                    try:
                        jobs = self.linkedin.search(query, location, max_per_source)
                        results.extend(self._dedup(jobs))
                        logger.info("LinkedIn: +%d jobs for %r/%r", len(jobs), query, location)
                    except Exception as e:
                        logger.error("LinkedIn scrape failed: %s", e)

                if "indeed" in all_sources:
                    try:
                        jobs = self.indeed.search(query, location, max_per_source)
                        results.extend(self._dedup(jobs))
                        logger.info("Indeed: +%d jobs for %r/%r", len(jobs), query, location)
                    except Exception as e:
                        logger.error("Indeed scrape failed: %s", e)

        if "greenhouse" in all_sources and greenhouse_companies:
            for query in queries:
                try:
                    jobs = self.greenhouse.search(greenhouse_companies, query, max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("Greenhouse: +%d jobs", len(jobs))
                except Exception as e:
                    logger.error("Greenhouse scrape failed: %s", e)

        if "lever" in all_sources and lever_companies:
            for query in queries:
                try:
                    jobs = self.lever.search(lever_companies, query, max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("Lever: +%d jobs", len(jobs))
                except Exception as e:
                    logger.error("Lever scrape failed: %s", e)

        # Apply title exclusion filter
        if _exclude:
            before = len(results)
            results = [
                j for j in results
                if not any(excl in (j.title or "").lower() for excl in _exclude)
            ]
            logger.info("Excluded %d jobs by title filter", before - len(results))

        logger.info("Total unique listings fetched: %d", len(results))
        return results

    def _dedup(self, jobs: list[JobListing]) -> list[JobListing]:
        fresh = []
        for j in jobs:
            if j.job_id not in self._seen:
                self._seen.add(j.job_id)
                fresh.append(j)
        return fresh
