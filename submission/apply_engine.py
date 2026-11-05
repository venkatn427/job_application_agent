"""
Apply Engine
------------
PlaywrightEngine  — local Chromium via sync_playwright, no Claude dependency.
ClaudeFieldAdvisor — optional AI layer for unknown form fields (requires ANTHROPIC_API_KEY).
ScreeningMemory   — save/reuse screening Q&A across applications via local FTS5 memory.
ResumeAdapter     — AI mode: tailor resume + cover letter per job; non-AI: use master files.

All browser automation runs locally. Claude API is only called when:
  1. ANTHROPIC_API_KEY is set (in .env or via UI settings)
  2. A form field cannot be answered from profile.json
"""

import hashlib
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent


# ── Human-like delays ─────────────────────────────────────────────────────────

def _human_delay(min_ms: int = 300, max_ms: int = 900) -> None:
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _type_humanly(page, selector: str, text: str) -> None:
    page.click(selector)
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.03, 0.12))


# ── PlaywrightEngine ──────────────────────────────────────────────────────────

class PlaywrightEngine:
    """
    Wraps sync_playwright Chromium with anti-detection settings.
    Exposes .page for direct use by platform applier classes in auto_apply.py.
    """

    _LAUNCH_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def launch(self) -> None:
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=self._LAUNCH_ARGS,
        )
        self._context = self._browser.new_context(
            user_agent=self._USER_AGENT,
            locale="en-US",
            timezone_id="America/Los_Angeles",
            viewport={"width": 1280, "height": 900},
        )
        self._page = self._context.new_page()
        # Mask navigator.webdriver
        self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._playwright = None

    def __enter__(self):
        self.launch()
        return self

    def __exit__(self, *_):
        self.close()

    @property
    def page(self):
        """Raw Playwright page — used by auto_apply.py platform appliers."""
        if self._page is None:
            raise RuntimeError("Call launch() first")
        return self._page

    def navigate(self, url: str, wait: str = "domcontentloaded") -> None:
        try:
            self.page.goto(url, wait_until=wait, timeout=45_000)
        except Exception:
            # Fallback: page may have loaded despite timeout (e.g. slow analytics)
            pass
        _human_delay(400, 800)

    def snapshot(self) -> str:
        """Return visible text content for login-wall detection."""
        try:
            return self.page.inner_text("body")
        except Exception:
            return self.page.url

    def current_url(self) -> str:
        return self.page.url

    def click(self, selector: str) -> None:
        self.page.click(selector)
        _human_delay(200, 500)

    def fill(self, selector: str, value: str) -> None:
        self.page.fill(selector, value)
        _human_delay(100, 300)

    def type_humanly(self, selector: str, value: str) -> None:
        _type_humanly(self.page, selector, value)

    def upload(self, selector: str, path: str) -> None:
        self.page.set_input_files(selector, path)
        _human_delay(200, 400)

    def select_option(self, selector: str, value: str) -> None:
        self.page.select_option(selector, value=value)
        _human_delay(100, 300)

    def wait_ms(self, ms: int = 500) -> None:
        time.sleep(ms / 1000)

    def wait_for_selector(self, selector: str, timeout: int = 10_000):
        return self.page.wait_for_selector(selector, timeout=timeout)

    def query_selector(self, selector: str):
        return self.page.query_selector(selector)

    def query_selector_all(self, selector: str):
        return self.page.query_selector_all(selector)


# ── Login-wall detection ──────────────────────────────────────────────────────

_LOGIN_URL_SIGNALS = ["login", "signin", "sign-in", "auth", "account/login"]
_SIGNUP_TEXT_SIGNALS = ["create account", "create an account", "sign up", "register", "join now"]
_LOGIN_TEXT_SIGNALS  = ["sign in", "log in", "login", "enter your password"]

def detect_login_wall(engine: PlaywrightEngine) -> Optional[str]:
    """
    Returns 'login' | 'signup' | None.
    Called after navigating to a job URL to detect authentication gates.
    """
    url = engine.current_url().lower()
    text = engine.snapshot().lower()[:3000]

    # Workday external applications always need an account
    if "myworkdayjobs.com" in url or "wd5.myworkday" in url or "wd3.myworkday" in url:
        if "apply" in url or "apply" in text[:500]:
            return "login"

    for sig in _LOGIN_URL_SIGNALS:
        if sig in url:
            # Check if it's a create-account page vs login page
            for s in _SIGNUP_TEXT_SIGNALS:
                if s in text:
                    return "signup"
            return "login"

    for s in _SIGNUP_TEXT_SIGNALS:
        if s in text:
            return "signup"
    for s in _LOGIN_TEXT_SIGNALS:
        if s in text:
            return "login"
    return None


# ── ClaudeFieldAdvisor ────────────────────────────────────────────────────────

class ClaudeFieldAdvisor:
    """
    Optional AI layer — only instantiated when ANTHROPIC_API_KEY is set.
    Answers form fields that cannot be mapped from profile.json.
    Uses claude-haiku-5-5 (cheapest/fastest) for single-field answers.
    """

    _SYSTEM = (
        "You are a job application assistant. Given a form field label, type, and "
        "options, return only the best answer string based on the candidate profile. "
        "Be concise. Return exactly the answer text with no explanation."
    )

    def __init__(self, api_key: str, model: str = "claude-haiku-5-5"):
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def answer_field(self, field_label: str, field_type: str,
                     options: list[str], profile: dict) -> Optional[str]:
        """Return best answer string, or None if unsure."""
        try:
            profile_summary = json.dumps({
                k: v for k, v in profile.items()
                if k in ("personal", "professional", "work_authorization",
                         "preferences", "screening_defaults")
            }, indent=2)[:2000]

            opts_str = f"\nOptions: {options}" if options else ""
            prompt = (
                f"Candidate profile:\n{profile_summary}\n\n"
                f"Form field label: {field_label}\n"
                f"Field type: {field_type}{opts_str}\n\n"
                "What should the candidate answer? Return only the answer, nothing else."
            )
            msg = self._get_client().messages.create(
                model=self._model,
                max_tokens=200,
                system=self._SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = msg.content[0].text.strip()
            return answer if answer else None
        except Exception as e:
            logger.warning("ClaudeFieldAdvisor failed for '%s': %s", field_label, e)
            return None

    def tailor_resume(self, master_resume_text: str, job_description: str,
                      job_title: str, company: str) -> dict:
        """
        Return a tailored resume as a JSON dict with keys:
        summary, skills (list), experience (list of dicts), education (list).
        """
        try:
            prompt = (
                f"Job: {job_title} at {company}\n\n"
                f"Job Description:\n{job_description[:3000]}\n\n"
                f"Master Resume:\n{master_resume_text[:3000]}\n\n"
                "Rewrite the resume to match this job. Add relevant keywords from the JD. "
                "Return ONLY valid JSON with keys: summary (string), skills (string list), "
                "experience (list of {company, title, dates, bullets[]}), education (list of {school, degree, year})."
            )
            msg = self._get_client().messages.create(
                model="claude-sonnet-5-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            logger.warning("tailor_resume failed: %s", e)
            return {}

    def write_cover_letter(self, profile: dict, job_title: str, company: str,
                           job_description: str) -> str:
        """Return a 3-paragraph cover letter as plain text."""
        try:
            name = ""
            if "personal" in profile:
                p = profile["personal"]
                name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            prompt = (
                f"Write a 3-paragraph cover letter for {name} applying to "
                f"{job_title} at {company}.\n\n"
                f"Job Description:\n{job_description[:2000]}\n\n"
                "Be specific, professional, and enthusiastic. No placeholders. "
                "Return only the letter body (no greeting line, no signature)."
            )
            msg = self._get_client().messages.create(
                model="claude-haiku-5-5",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            logger.warning("write_cover_letter failed: %s", e)
            return ""


def get_advisor() -> Optional[ClaudeFieldAdvisor]:
    """Return ClaudeFieldAdvisor if ANTHROPIC_API_KEY is set, else None."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return ClaudeFieldAdvisor(key) if key else None


# ── ScreeningMemory ───────────────────────────────────────────────────────────

class ScreeningMemory:
    """
    Save and retrieve screening question answers via the local FTS5 memory store.
    Keys: screening:{portal}:{question_hash}
    Both AI and non-AI modes use this — AI mode also saves new answers.
    """

    def __init__(self, memory_db_path: Optional[Path] = None):
        db_path = memory_db_path or ROOT / "data" / "memory.db"
        # Import MemoryStore from tools/local_memory.py
        sys.path.insert(0, str(ROOT))
        from tools.local_memory import MemoryStore
        self._store = MemoryStore(db_path)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.lower().strip().encode()).hexdigest()[:10]

    def lookup(self, question_text: str, portal: str = "generic") -> Optional[str]:
        """FTS5 search for similar past question. Returns stored answer or None."""
        try:
            query = f"screening {portal} {question_text[:80]}"
            results = self._store.search(query, limit=5)
            for r in results:
                try:
                    data = json.loads(r.get("content", "{}"))
                    if data.get("answer"):
                        return data["answer"]
                except Exception:
                    pass
        except Exception as e:
            logger.debug("ScreeningMemory.lookup failed: %s", e)
        return None

    def save(self, question_text: str, answer: str,
             portal: str = "generic", source: str = "automated") -> None:
        """Save a question/answer pair for future reuse."""
        try:
            key = f"screening:{portal}:{self._hash(question_text)}"
            content = json.dumps({
                "question": question_text,
                "answer": answer,
                "source": source,
                "portal": portal,
                "saved_at": datetime.now().isoformat(),
            })
            self._store.save(key, content)
        except Exception as e:
            logger.debug("ScreeningMemory.save failed: %s", e)


# ── ResumeAdapter ─────────────────────────────────────────────────────────────

class ResumeAdapter:
    """
    Prepares resume + cover letter before each apply.

    AI mode (advisor not None):
      - Scrapes job description from URL (best effort)
      - Calls ClaudeFieldAdvisor to tailor resume JSON + cover letter text
      - Renders both to PDF via pipeline/document_builder.py
      - Caches to data/tailored/{job_hash}/

    Non-AI mode:
      - Returns master_resume_path as-is
      - Returns master_cover_letter_path if it exists, else None
    """

    def __init__(self,
                 advisor: Optional[ClaudeFieldAdvisor] = None,
                 master_resume: Optional[Path] = None,
                 master_cover_letter: Optional[Path] = None):
        self._advisor = advisor
        self.master_resume = master_resume or ROOT / "data" / "master_resume" / "resume.pdf"
        self.master_cl = master_cover_letter or ROOT / "data" / "master_resume" / "cover_letter.pdf"

    def prepare(self, job_url: str, job_title: str = "", company: str = "",
                job_description: str = "",
                on_step: Optional[Callable] = None) -> tuple[Optional[str], Optional[str]]:
        """
        Returns (resume_pdf_path, cover_letter_pdf_path).
        cover_letter_pdf_path may be None if not available.
        """
        def _step(msg):
            if on_step:
                on_step(msg)
            logger.info(msg)

        if not self._advisor:
            _step("Automated mode: using master resume")
            resume = str(self.master_resume) if self.master_resume.exists() else None
            cl = str(self.master_cl) if self.master_cl.exists() else None
            return resume, cl

        # AI mode
        _step("AI mode: tailoring resume to job description…")

        # Scrape JD if not provided
        if not job_description and job_url:
            job_description = self._scrape_jd(job_url)

        if not job_description:
            _step("Could not fetch job description — using master resume")
            resume = str(self.master_resume) if self.master_resume.exists() else None
            cl = str(self.master_cl) if self.master_cl.exists() else None
            return resume, cl

        # Cache dir
        job_hash = hashlib.md5(job_url.encode()).hexdigest()[:8]
        cache_dir = ROOT / "data" / "tailored" / job_hash
        cache_dir.mkdir(parents=True, exist_ok=True)

        cached_resume = cache_dir / "resume.pdf"
        cached_cl = cache_dir / "cover_letter.pdf"

        if cached_resume.exists() and cached_cl.exists():
            _step("Using cached tailored resume")
            return str(cached_resume), str(cached_cl)

        # Read master resume text
        master_text = self._extract_pdf_text(self.master_resume)

        # Tailor resume
        _step("Generating tailored resume with Claude…")
        tailored = self._advisor.tailor_resume(master_text, job_description, job_title, company)

        # Generate cover letter
        _step("Generating cover letter with Claude…")
        cl_text = self._advisor.write_cover_letter({}, job_title, company, job_description)

        # Render to PDF
        resume_path = self._render_resume_pdf(tailored, str(cached_resume), job_title, company)
        cl_path = self._render_cl_pdf(cl_text, str(cached_cl))

        if resume_path:
            _step(f"Tailored resume saved: {resume_path}")
        if cl_path:
            _step(f"Cover letter saved: {cl_path}")

        return (
            resume_path or (str(self.master_resume) if self.master_resume.exists() else None),
            cl_path or (str(self.master_cl) if self.master_cl.exists() else None),
        )

    def _scrape_jd(self, url: str) -> str:
        try:
            import requests
            from bs4 import BeautifulSoup
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True)[:5000]
        except Exception as e:
            logger.debug("JD scrape failed: %s", e)
            return ""

    def _extract_pdf_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)[:4000]
        except Exception:
            return ""

    def _render_resume_pdf(self, data: dict, out_path: str,
                           job_title: str, company: str) -> Optional[str]:
        if not data:
            return None
        try:
            sys.path.insert(0, str(ROOT))
            from pipeline.document_builder import ResumeBuilder
            builder = ResumeBuilder()
            builder.build_from_dict(data, out_path)
            return out_path
        except Exception as e:
            logger.warning("render_resume_pdf failed: %s", e)
            return None

    def _render_cl_pdf(self, text: str, out_path: str) -> Optional[str]:
        if not text:
            return None
        try:
            from pipeline.document_builder import ResumeBuilder
            builder = ResumeBuilder()
            builder.build_cover_letter(text, out_path)
            return out_path
        except Exception as e:
            logger.warning("render_cl_pdf failed: %s", e)
            return None
