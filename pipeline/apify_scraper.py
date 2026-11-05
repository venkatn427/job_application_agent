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

class DiceScraper:
    """
    Scrapes Dice.com using their public job search API.
    No API key required. Filters to jobs posted today (postedDate=ONE).
    """

    _BASE = "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search"

    def search(
        self,
        query: str,
        location: str = "Remote",
        max_results: int = 20,
        easy_apply_only: bool = True,
    ) -> list[JobListing]:
        import urllib.parse
        import urllib.request

        params = {
            "q": query,
            "countryCode": "US",
            "radius": "30",
            "radiusUnit": "mi",
            "page": "1",
            "pageSize": str(min(max_results, 100)),
            "filters.postedDate": "ONE",   # posted today
            "language": "en",
        }
        if easy_apply_only:
            params["filters.easyApply"] = "true"

        url = self._BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("Dice search failed for %r: %s", query, e)
            return []

        jobs = []
        for item in data.get("data", []):
            job_id = item.get("id") or item.get("jobId") or ""
            apply_url = item.get("applyUrl") or item.get("jobDetailUrl") or \
                        f"https://www.dice.com/job-detail/{job_id}"
            jobs.append(JobListing(
                job_id=f"dice_{job_id}",
                title=item.get("title") or "",
                company=item.get("companyName") or "",
                location=item.get("locationName") or location,
                description=item.get("descriptionFragment") or "",
                apply_url=apply_url,
                source="dice",
                job_type=item.get("employmentType") or "",
                requirements=[],
            ))
        logger.info("[dice] %d jobs for %r", len(jobs), query)
        return jobs[:max_results]


class RemoteOKScraper:
    """
    RemoteOK public API — no auth needed.
    Returns remote tech jobs (tagged today).
    """

    _BASE = "https://remoteok.com/api"

    def search(self, query: str = "", max_results: int = 20) -> list[JobListing]:
        import urllib.request

        req = urllib.request.Request(self._BASE, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("RemoteOK search failed: %s", e)
            return []

        jobs = []
        query_lower = query.lower()
        # Normalize for tag matching: "data engineer" → also check "data-engineer"
        query_hyphen = query_lower.replace(" ", "-")
        query_words = set(query_lower.split())
        for item in data:
            if not isinstance(item, dict) or "id" not in item:
                continue
            title = item.get("position") or ""
            tags_list = item.get("tags") or []
            tags_str = " ".join(tags_list).lower()
            tags_hyphen = tags_str.replace(" ", "-")
            if query_lower:
                title_match = query_lower in title.lower()
                tag_match = (query_lower in tags_str or
                             query_hyphen in tags_str or
                             any(w in tags_list for w in query_words))
                if not title_match and not tag_match:
                    continue
            jobs.append(JobListing(
                job_id=f"remoteok_{item['id']}",
                title=title,
                company=item.get("company") or "",
                location="Remote",
                description=item.get("description") or "",
                apply_url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
                source="remoteok",
                job_type="full-time",
                requirements=[],
            ))
        logger.info("[remoteok] %d jobs for %r", len(jobs), query)
        return jobs[:max_results]


class WellfoundScraper:
    """
    Wellfound (AngelList) startup jobs — requires login; returns empty without it.
    Jobs can be browsed at wellfound.com/jobs but have no unauthenticated API.
    Included here as a placeholder; use Playwright apply flow for Wellfound URLs.
    """

    def search(self, query: str, max_results: int = 20) -> list[JobListing]:
        # Wellfound has no public unauthenticated job search API.
        # Jobs scraped via Apify actor 'maxcopell/wellfound' if APIFY_API_TOKEN is set.
        return []


class WeWorkRemotelyScraper:
    """
    We Work Remotely — parses the public RSS feed (no API key needed).
    Category feeds cover: programming, devops, design, marketing, customer support, etc.
    """

    _FEEDS = {
        "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "devops":      "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "data":        "https://weworkremotely.com/categories/remote-data-science-jobs.rss",
        "all":         "https://weworkremotely.com/remote-jobs.rss",
        "back-end":    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    }

    def search(self, query: str = "", max_results: int = 20,
               categories: list[str] | None = None) -> list[JobListing]:
        import xml.etree.ElementTree as ET

        feed_urls = []
        for cat in (categories or ["programming", "devops", "data"]):
            url = self._FEEDS.get(cat)
            if url:
                feed_urls.append(url)

        query_lower = query.lower()
        jobs: list[JobListing] = []
        seen_titles: set[str] = set()

        for feed_url in feed_urls:
            if len(jobs) >= max_results:
                break
            try:
                resp = requests.get(feed_url, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"},
                                    allow_redirects=True)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
            except Exception as e:
                logger.warning("[weworkremotely] Feed %s failed: %s", feed_url, e)
                continue

            ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
            for item in root.iter("item"):
                if len(jobs) >= max_results:
                    break
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description")
                pub_el   = item.find("pubDate")
                if title_el is None:
                    continue

                raw_title = (title_el.text or "").strip()
                # WWR format: "Company: Job Title" or just "Job Title"
                if ": " in raw_title:
                    company_part, _, job_title = raw_title.partition(": ")
                else:
                    company_part = ""
                    job_title = raw_title

                if query_lower and query_lower not in job_title.lower() and \
                        query_lower not in raw_title.lower():
                    continue

                dedup_key = f"{company_part}|{job_title}"
                if dedup_key in seen_titles:
                    continue
                seen_titles.add(dedup_key)

                link = (link_el.text or "").strip() if link_el is not None else ""
                desc = (desc_el.text or "").strip() if desc_el is not None else ""
                posted = (pub_el.text or "")[:16] if pub_el is not None else None

                jobs.append(JobListing(
                    title=job_title,
                    company=company_part or "Unknown",
                    location="Remote",
                    description=desc,
                    apply_url=link,
                    posted_date=posted,
                    source="weworkremotely",
                    job_type="full-time",
                    requirements=[],
                ))

        logger.info("[weworkremotely] %d jobs for %r", len(jobs), query)
        return jobs[:max_results]


class TheMuseScraper:
    """
    The Muse public jobs API — no API key required for basic use.
    Good coverage of tech, marketing, and design roles at mid-sized companies.
    """

    _BASE = "https://www.themuse.com/api/public/jobs"

    def search(self, query: str, max_results: int = 20,
               category: str = "") -> list[JobListing]:
        import urllib.parse

        params = {
            "page": "0",
            "descending": "true",
            "query": query,
        }
        if category:
            params["category"] = category
        url = self._BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("[themuse] Search failed for %r: %s", query, e)
            return []

        jobs = []
        q_words = [w for w in query.lower().split() if len(w) > 2]
        for item in data.get("results", []):
            title = item.get("name") or ""
            company = (item.get("company") or {}).get("name") or ""
            if not title:
                continue
            # Client-side keyword filter: at least one query word must appear in title
            if q_words and not any(w in title.lower() for w in q_words):
                continue

            locs = item.get("locations") or [{}]
            location = locs[0].get("name") if locs else "Remote"

            refs = item.get("refs") or {}
            apply_url = refs.get("landing_page") or ""

            levels = item.get("levels") or [{}]
            level = levels[0].get("name") if levels else None

            jobs.append(JobListing(
                job_id=f"muse_{item.get('id', '')}",
                title=title,
                company=company,
                location=location,
                description=item.get("contents") or "",
                apply_url=apply_url,
                posted_date=item.get("publication_date", "")[:10] or None,
                job_type=level,
                source="themuse",
                requirements=[],
            ))

        logger.info("[themuse] %d jobs for %r", len(jobs), query)
        return jobs[:max_results]


class ZipRecruiterScraper:
    """
    ZipRecruiter — protected by Cloudflare; no public unauthenticated API.
    Returns empty. Use Playwright apply flow for ZipRecruiter URLs found elsewhere,
    or use the Apify actor 'vaclavrut/ziprecruiter-jobs-scraper' if APIFY_API_TOKEN is set.
    """

    def search(self, query: str, location: str = "Remote", max_results: int = 20,
               days_ago: int = 1) -> list[JobListing]:
        return []


class USAJobsScraper:
    """
    USAJobs (US federal jobs) public API — no key needed for basic search.
    Set USAJOBS_API_KEY and USAJOBS_USER_AGENT in .env for full access.
    """

    _BASE = "https://data.usajobs.gov/api/search"

    def search(self, query: str, max_results: int = 10) -> list[JobListing]:
        import urllib.parse
        import urllib.request

        api_key = os.environ.get("USAJOBS_API_KEY", "")
        user_agent = os.environ.get("USAJOBS_USER_AGENT", "jobapp@example.com")
        if not api_key:
            return []   # USAJobs requires a key; skip silently

        params = {
            "Keyword": query,
            "ResultsPerPage": str(min(max_results, 25)),
            "DatePosted": "1",
        }
        url = self._BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Host": "data.usajobs.gov",
            "User-Agent": user_agent,
            "Authorization-Key": api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("USAJobs search failed for %r: %s", query, e)
            return []

        jobs = []
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            m = item.get("MatchedObjectDescriptor", {})
            jobs.append(JobListing(
                job_id=f"usajobs_{m.get('PositionID', '')}",
                title=m.get("PositionTitle") or "",
                company=m.get("OrganizationName") or "US Government",
                location=(m.get("PositionLocation") or [{}])[0].get("LocationName") or "",
                description=m.get("QualificationSummary") or "",
                apply_url=m.get("PositionURI") or "",
                source="usajobs",
                job_type="full-time",
                requirements=[],
            ))
        logger.info("[usajobs] %d jobs for %r", len(jobs), query)
        return jobs[:max_results]


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
        self.dice = DiceScraper()
        self.remoteok = RemoteOKScraper()
        self.wellfound = WellfoundScraper()
        self.usajobs = USAJobsScraper()
        self.weworkremotely = WeWorkRemotelyScraper()
        self.themuse = TheMuseScraper()
        self.ziprecruiter = ZipRecruiterScraper()
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
        all_sources = sources or ["linkedin", "indeed", "greenhouse", "lever",
                                  "dice", "remoteok", "wellfound", "usajobs",
                                  "weworkremotely", "themuse", "ziprecruiter"]
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

        if "dice" in all_sources:
            for query in queries:
                try:
                    jobs = self.dice.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("Dice: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("Dice scrape failed: %s", e)

        if "remoteok" in all_sources:
            for query in queries:
                try:
                    jobs = self.remoteok.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("RemoteOK: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("RemoteOK scrape failed: %s", e)

        if "wellfound" in all_sources:
            for query in queries:
                try:
                    jobs = self.wellfound.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("Wellfound: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("Wellfound scrape failed: %s", e)

        if "usajobs" in all_sources:
            for query in queries:
                try:
                    jobs = self.usajobs.search(query, max_results=10)
                    results.extend(self._dedup(jobs))
                    logger.info("USAJobs: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("USAJobs scrape failed: %s", e)

        if "weworkremotely" in all_sources:
            for query in queries:
                try:
                    jobs = self.weworkremotely.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("WeWorkRemotely: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("WeWorkRemotely scrape failed: %s", e)

        if "themuse" in all_sources:
            for query in queries:
                try:
                    jobs = self.themuse.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("TheMuse: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("TheMuse scrape failed: %s", e)

        if "ziprecruiter" in all_sources:
            for query in queries:
                try:
                    jobs = self.ziprecruiter.search(query, max_results=max_per_source)
                    results.extend(self._dedup(jobs))
                    logger.info("ZipRecruiter: +%d jobs for %r", len(jobs), query)
                except Exception as e:
                    logger.error("ZipRecruiter scrape failed: %s", e)

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
