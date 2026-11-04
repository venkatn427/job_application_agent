"""
Playwright Auto-Apply
----------------------
Automates job applications exactly as a human would:
  1. Navigate to the job posting URL
  2. Detect the ATS platform (Greenhouse, Lever, LinkedIn, Indeed, Workday, etc.)
  3. Log in or create an account using saved local credentials
  4. Fill every form field from the candidate profile
  5. Upload resume + cover letter
  6. Answer screening questions using the same logic as dice_submitter
  7. Review and submit

Usage:
    from submission.auto_apply import AutoApplier
    applier = AutoApplier(profile_path="data/candidate_profile.json",
                          credentials_path="submission/.secrets.env")
    result = applier.apply(job_url="https://boards.greenhouse.io/...")

SAFETY: Set AUTO_APPLY_DRY_RUN=true in your .env to run without submitting.
"""

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Platform detection
# ─────────────────────────────────────────────────────────────────────────────

_PLATFORM_PATTERNS = {
    "greenhouse":      ["boards.greenhouse.io", "greenhouse.io/apply"],
    "lever":           ["jobs.lever.co", "lever.co/"],
    "workday":         ["myworkdayjobs.com", "workday.com"],
    "linkedin":        ["linkedin.com/jobs", "linkedin.com/apply"],
    "indeed":          ["indeed.com/apply", "indeed.com/viewjob"],
    "dice":            ["dice.com/job-detail", "dice.com/jobs"],
    "icims":           ["icims.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "jobvite":         ["jobs.jobvite.com", "jobvite.com"],
    "taleo":           ["taleo.net"],
    "ashby":           ["jobs.ashbyhq.com", "ashbyhq.com"],
    "wellfound":       ["wellfound.com/jobs", "angel.co/jobs"],
    "ziprecruiter":    ["ziprecruiter.com/jobs", "ziprecruiter.com/c/"],
}


def detect_platform(url: str) -> str:
    """Return the ATS platform name from a job URL, or 'generic'."""
    url_lower = url.lower()
    for platform, patterns in _PLATFORM_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return "generic"


# ─────────────────────────────────────────────────────────────────────────────
# Human-timing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _human_delay(min_ms: int = 300, max_ms: int = 900) -> None:
    """Sleep a randomized human-like interval."""
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _type_humanly(page, selector: str, text: str) -> None:
    """Click a field and type with per-character random delays, like a human."""
    page.click(selector)
    _human_delay(100, 300)
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.03, 0.12))
    _human_delay(200, 500)


# ─────────────────────────────────────────────────────────────────────────────
# Credential loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_credentials(secrets_path: str) -> dict:
    """
    Load login credentials from the secrets env file.
    Expected keys (platform-prefixed):
      GREENHOUSE_EMAIL, GREENHOUSE_PASSWORD
      LEVER_EMAIL, LEVER_PASSWORD
      LINKEDIN_EMAIL, LINKEDIN_PASSWORD
      INDEED_EMAIL, INDEED_PASSWORD
      (generic fallback: APPLY_EMAIL, APPLY_PASSWORD)
    """
    creds: dict = {}
    path = Path(secrets_path)
    if not path.exists():
        logger.warning("Secrets file not found: %s", secrets_path)
        return creds
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        creds[key.strip()] = val.strip().strip('"').strip("'")
    return creds


def _get_login(creds: dict, platform: str) -> tuple[str, str]:
    prefix = platform.upper()
    email = (
        creds.get(f"{prefix}_EMAIL")
        or creds.get("APPLY_EMAIL", "")
    )
    password = (
        creds.get(f"{prefix}_PASSWORD")
        or creds.get("APPLY_PASSWORD", "")
    )
    return email, password


# ─────────────────────────────────────────────────────────────────────────────
# Platform-specific apply strategies
# ─────────────────────────────────────────────────────────────────────────────

class _GreenhouseApplier:
    """Apply via Greenhouse Jobboard embed."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        # Click Apply button if present
        try:
            page.wait_for_selector("a#app-link, a.btn-apply, a[data-role='apply']",
                                   timeout=10_000)
            page.click("a#app-link, a.btn-apply, a[data-role='apply']")
            _human_delay(800, 1500)
        except Exception:
            pass  # may already be on form

        # Fill name
        self._fill_if_exists(page, "#first_name", profile.get("name", "").split()[0])
        self._fill_if_exists(page, "#last_name", " ".join(profile.get("name", "").split()[1:]))
        self._fill_if_exists(page, "#email", profile.get("email", ""))
        self._fill_if_exists(page, "#phone", profile.get("phone", ""))

        # Upload resume
        if resume_path and resume_path.exists():
            try:
                page.set_input_files("input[name='resume']", str(resume_path))
                _human_delay(500, 1000)
            except Exception:
                logger.warning("Could not upload resume to Greenhouse form")

        # Upload cover letter
        if cover_letter_path and cover_letter_path.exists():
            try:
                page.set_input_files("input[name='cover_letter']", str(cover_letter_path))
                _human_delay(500, 1000)
            except Exception:
                pass

        # LinkedIn URL
        self._fill_if_exists(page, "#job_application_answers_attributes_0_text_value",
                             profile.get("linkedin_url", ""))

        # Submit
        _human_delay(1000, 2000)
        try:
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as e:
            logger.error("Greenhouse submit failed: %s", e)
            return False

    def _fill_if_exists(self, page, selector: str, value: str) -> None:
        if not value:
            return
        try:
            page.fill(selector, value)
            _human_delay(200, 500)
        except Exception:
            pass


class _LeverApplier:
    """Apply via Lever job posting."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        try:
            page.wait_for_selector(".template-btn-submit, a.postings-btn",
                                   timeout=10_000)
            page.click(".template-btn-submit, a.postings-btn")
            _human_delay(800, 1500)
        except Exception:
            pass

        name = profile.get("name", "")
        self._fill_if_exists(page, "input[name='name']", name)
        self._fill_if_exists(page, "input[name='email']", profile.get("email", ""))
        self._fill_if_exists(page, "input[name='phone']", profile.get("phone", ""))
        self._fill_if_exists(page, "input[name='org']", profile.get("current_title", ""))
        self._fill_if_exists(page, "input[name='urls[LinkedIn]']",
                             profile.get("linkedin_url", ""))

        if resume_path and resume_path.exists():
            try:
                page.set_input_files("input[type='file']", str(resume_path))
                _human_delay(600, 1200)
            except Exception:
                logger.warning("Could not upload resume to Lever form")

        _human_delay(1000, 2000)
        try:
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as e:
            logger.error("Lever submit failed: %s", e)
            return False

    def _fill_if_exists(self, page, selector: str, value: str) -> None:
        if not value:
            return
        try:
            page.fill(selector, value)
            _human_delay(150, 400)
        except Exception:
            pass


class _LinkedInApplier:
    """Apply via LinkedIn Easy Apply (creates account if needed)."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        email, password = _get_login(creds, "linkedin")
        if not email or not password:
            logger.error("LinkedIn credentials not found in secrets file")
            return False

        # Login or create account if needed
        if "linkedin.com/login" in page.url or "linkedin.com/checkpoint" in page.url:
            self._login(page, email, password)
        elif not self._is_logged_in(page):
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            _human_delay(1000, 2000)
            # Try login; if account not found, create one
            if not self._login(page, email, password):
                logger.info("LinkedIn login failed — attempting account creation")
                self._create_account(page, profile, email, password, creds)

        # Click Easy Apply
        try:
            page.wait_for_selector(".jobs-apply-button, button[aria-label*='Easy Apply']",
                                   timeout=10_000)
            page.click(".jobs-apply-button, button[aria-label*='Easy Apply']")
            _human_delay(1000, 2000)
        except Exception:
            logger.error("LinkedIn Easy Apply button not found")
            return False

        # Walk through multi-step form
        max_steps = 10
        for _ in range(max_steps):
            self._fill_step(page, profile, resume_path)
            _human_delay(500, 1000)

            # Try to advance or submit
            if self._try_submit(page):
                return True
            if not self._try_next(page):
                break

        return False

    def _is_logged_in(self, page) -> bool:
        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded",
                      timeout=10_000)
            _human_delay(500, 1000)
            return "feed" in page.url or "mynetwork" in page.url
        except Exception:
            return False

    def _login(self, page, email: str, password: str) -> bool:
        """Returns True on success."""
        try:
            page.fill("#username", email)
            _human_delay(300, 700)
            page.fill("#password", password)
            _human_delay(300, 700)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15_000)
            _human_delay(1000, 2000)
            # Success if redirected away from login
            return "login" not in page.url and "checkpoint" not in page.url
        except Exception as e:
            logger.warning("LinkedIn login attempt failed: %s", e)
            return False

    def _create_account(self, page, profile: dict, email: str, password: str,
                        creds: dict) -> None:
        """Create a new LinkedIn account."""
        try:
            page.goto("https://www.linkedin.com/signup", wait_until="domcontentloaded")
            _human_delay(1000, 2000)
            # Email
            page.fill("input[name='email'], #email-or-phone", email)
            _human_delay(400, 800)
            page.click("button[type='submit'], .sign-in-form__submit-btn")
            _human_delay(800, 1500)
            # Password
            page.fill("input[name='password'], #password", password)
            _human_delay(300, 600)
            page.click("button[type='submit']")
            _human_delay(800, 1500)
            # First / last name
            first = (profile.get("name") or "").split()[0]
            last = " ".join((profile.get("name") or "").split()[1:])
            page.fill("input[name='firstName'], #first-name", first)
            _human_delay(200, 500)
            page.fill("input[name='lastName'], #last-name", last)
            _human_delay(200, 500)
            page.click("button[type='submit']")
            _human_delay(1000, 2000)
            logger.info("LinkedIn account creation initiated for %s", email)
        except Exception as e:
            logger.error("LinkedIn account creation failed: %s", e)

    def _fill_step(self, page, profile: dict, resume_path: Optional[Path]) -> None:
        # Phone
        try:
            phone_input = page.query_selector(
                "input[id*='phoneNumber'], input[name*='phone']"
            )
            if phone_input and not phone_input.input_value():
                phone_input.fill(profile.get("phone", ""))
                _human_delay(200, 500)
        except Exception:
            pass

        # Resume upload
        if resume_path and resume_path.exists():
            try:
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    file_input.set_input_files(str(resume_path))
                    _human_delay(600, 1200)
            except Exception:
                pass

        # Yes/No questions (answer "Yes" to most, "No" to sponsorship)
        try:
            selects = page.query_selector_all("select")
            for sel in selects:
                label_text = ""
                try:
                    label_id = sel.get_attribute("id")
                    if label_id:
                        lbl = page.query_selector(f"label[for='{label_id}']")
                        if lbl:
                            label_text = (lbl.inner_text() or "").lower()
                except Exception:
                    pass

                if "sponsor" in label_text:
                    sel.select_option(label="Yes")
                elif "authoriz" in label_text or "legally" in label_text:
                    sel.select_option(label="Yes")
                _human_delay(100, 300)
        except Exception:
            pass

    def _try_next(self, page) -> bool:
        try:
            btn = page.query_selector(
                "button[aria-label='Continue to next step'], "
                "button[aria-label*='Next'], "
                "button[data-easy-apply-next-button]"
            )
            if btn:
                btn.click()
                _human_delay(500, 1000)
                return True
        except Exception:
            pass
        return False

    def _try_submit(self, page) -> bool:
        try:
            btn = page.query_selector(
                "button[aria-label='Submit application'], "
                "button[aria-label*='Submit']"
            )
            if btn:
                btn.click()
                page.wait_for_load_state("networkidle", timeout=15_000)
                return True
        except Exception:
            pass
        return False


class _IndeedApplier:
    """Apply via Indeed — creates or logs into an Indeed account, then applies."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        email, password = _get_login(creds, "indeed")
        if not email or not password:
            logger.warning("Indeed credentials not set — trying guest apply")
        else:
            self._ensure_logged_in(page, profile, email, password)

        # Click Apply / Continue to apply
        try:
            page.wait_for_selector(
                "button[id*='apply'], a[id*='applyButton'], button[aria-label*='Apply']",
                timeout=10_000,
            )
            page.click("button[id*='apply'], a[id*='applyButton'], button[aria-label*='Apply']")
            _human_delay(1000, 2000)
        except Exception:
            pass

        # Walk multi-step form (up to 8 steps)
        for _ in range(8):
            self._fill_step(page, profile, resume_path)
            _human_delay(500, 1000)
            if self._try_submit(page):
                return True
            if not self._try_next(page):
                break
        return False

    def _ensure_logged_in(self, page, profile: dict, email: str, password: str) -> None:
        try:
            page.goto("https://secure.indeed.com/account/login",
                      wait_until="domcontentloaded", timeout=15_000)
            _human_delay(800, 1500)

            # Try sign in
            page.fill("input[name='__email'], input[type='email']", email)
            _human_delay(300, 600)
            next_btn = page.query_selector("button[type='submit'], #login-submit-button")
            if next_btn:
                next_btn.click()
                _human_delay(800, 1500)

            pwd_field = page.query_selector("input[type='password'], input[name='__password']")
            if pwd_field:
                pwd_field.fill(password)
                _human_delay(300, 600)
                page.click("button[type='submit'], #login-submit-button")
                _human_delay(1000, 2000)
                if "login" not in page.url:
                    logger.info("Indeed login successful")
                    return

            # Login failed — try account creation
            logger.info("Indeed login failed — attempting account creation")
            self._create_account(page, profile, email, password)
        except Exception as e:
            logger.warning("Indeed login/create failed: %s", e)

    def _create_account(self, page, profile: dict, email: str, password: str) -> None:
        try:
            page.goto("https://secure.indeed.com/account/register",
                      wait_until="domcontentloaded", timeout=15_000)
            _human_delay(800, 1500)
            page.fill("input[name='__email'], input[type='email']", email)
            _human_delay(300, 600)
            page.fill("input[name='__password'], input[type='password']", password)
            _human_delay(300, 600)
            page.click("button[type='submit']")
            _human_delay(1000, 2000)
            logger.info("Indeed account creation attempted for %s", email)
        except Exception as e:
            logger.error("Indeed account creation failed: %s", e)

    def _fill_step(self, page, profile: dict, resume_path: Optional[Path]) -> None:
        for attr in ("name", "id", "placeholder"):
            for fragment, key in [
                ("phone", "phone"), ("city", "location"),
                ("first", "first_name"), ("last", "last_name"),
            ]:
                val = profile.get(key) or profile.get("name", "")
                if key in ("first_name", "last_name"):
                    parts = (profile.get("name") or "").split()
                    val = parts[0] if key == "first_name" else " ".join(parts[1:])
                try:
                    el = page.query_selector(f"input[{attr}*='{fragment}' i]")
                    if el and not el.input_value():
                        el.fill(val)
                        _human_delay(100, 300)
                except Exception:
                    pass

        if resume_path and resume_path.exists():
            try:
                page.set_input_files("input[type='file']", str(resume_path))
                _human_delay(600, 1200)
            except Exception:
                pass

    def _try_next(self, page) -> bool:
        for label in ("Continue", "Next", "Save and continue"):
            try:
                btn = page.query_selector(f"button:has-text('{label}')")
                if btn:
                    btn.click()
                    _human_delay(500, 1000)
                    return True
            except Exception:
                pass
        return False

    def _try_submit(self, page) -> bool:
        for label in ("Submit your application", "Submit application", "Apply"):
            try:
                btn = page.query_selector(f"button:has-text('{label}')")
                if btn:
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    return True
            except Exception:
                pass
        return False


class _GenericApplier:
    """
    Best-effort filler for any form.
    Looks for common field name/id patterns and fills them.
    """

    _FIELD_MAP = {
        # selector fragment → profile key
        "first_name":    "first_name",
        "last_name":     "last_name",
        "full_name":     "name",
        "name":          "name",
        "email":         "email",
        "phone":         "phone",
        "linkedin":      "linkedin_url",
        "github":        "github_url",
        "portfolio":     "portfolio_url",
        "city":          "location",
        "location":      "location",
    }

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        profile["first_name"] = (profile.get("name") or "").split()[0]
        profile["last_name"] = " ".join((profile.get("name") or "").split()[1:])

        for fragment, key in self._FIELD_MAP.items():
            value = profile.get(key, "")
            if not value:
                continue
            for attr in ("name", "id", "placeholder"):
                selector = f"input[{attr}*='{fragment}' i]"
                try:
                    el = page.query_selector(selector)
                    if el and not el.input_value():
                        el.fill(value)
                        _human_delay(100, 300)
                        break
                except Exception:
                    pass

        if resume_path and resume_path.exists():
            try:
                inputs = page.query_selector_all("input[type='file']")
                for inp in inputs:
                    accept = inp.get_attribute("accept") or ""
                    if "pdf" in accept or "doc" in accept or not accept:
                        inp.set_input_files(str(resume_path))
                        _human_delay(600, 1200)
                        break
            except Exception:
                pass

        _human_delay(1000, 2000)
        try:
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as e:
            logger.error("Generic submit failed: %s", e)
            return False


class _DiceApplier:
    """
    Dice Easy Apply — delegates to the existing submitter.py logic which
    reads profile.json and answers all screening questions correctly.
    """

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        from pathlib import Path as _Path
        import sys, os

        # submitter.py path
        sub_dir = _Path(__file__).parent
        if str(sub_dir) not in sys.path:
            sys.path.insert(0, str(sub_dir))

        try:
            from submitter import DiceSubmitter
            submitter = DiceSubmitter(profile_path=str(sub_dir / "profile.json"))
            return submitter.apply(page=page, resume_path=resume_path)
        except Exception as e:
            logger.error("Dice submitter error: %s — falling back to generic", e)
            return _GenericApplier().apply(page, profile, resume_path,
                                           cover_letter_path, creds)


class _ZipRecruiterApplier:
    """ZipRecruiter one-click apply (requires login)."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        email, password = _get_login(creds, "ziprecruiter")
        if not email:
            email, password = _get_login(creds, "generic")

        # Login if credentials available
        if email and password:
            try:
                current = page.url
                page.goto("https://www.ziprecruiter.com/login",
                          wait_until="domcontentloaded", timeout=15_000)
                _human_delay(800, 1500)
                page.fill("input[name='email'], input[type='email']", email)
                _human_delay(200, 500)
                page.fill("input[name='password'], input[type='password']", password)
                _human_delay(200, 500)
                page.click("button[type='submit']")
                _human_delay(1000, 2000)
                page.goto(current, wait_until="domcontentloaded", timeout=15_000)
                _human_delay(800, 1500)
            except Exception as e:
                logger.warning("ZipRecruiter login failed: %s", e)

        # Apply button
        try:
            page.click("a[data-testid*='apply'], button[data-testid*='apply'], "
                       "a.apply_button, button.apply_button")
            _human_delay(800, 1500)
        except Exception:
            pass

        return _GenericApplier().apply(page, profile, resume_path, cover_letter_path, creds)


_PLATFORM_APPLIERS = {
    "greenhouse":      _GreenhouseApplier(),
    "lever":           _LeverApplier(),
    "linkedin":        _LinkedInApplier(),
    "indeed":          _IndeedApplier(),
    "dice":            _DiceApplier(),
    "ziprecruiter":    _ZipRecruiterApplier(),
    "generic":         _GenericApplier(),
}


# ─────────────────────────────────────────────────────────────────────────────
# Main AutoApplier
# ─────────────────────────────────────────────────────────────────────────────

class AutoApplier:
    """
    Human-like Playwright job applier.

    Parameters
    ----------
    profile_path       Path to cached candidate_profile.json
    credentials_path   Path to .secrets.env with platform login credentials
    resume_pdf_path    Path to the tailored resume PDF to upload
    cover_letter_path  Optional cover letter DOCX/PDF to upload
    headless           Run browser headlessly (default True in production)
    dry_run            If True, fill forms but do NOT click Submit
    """

    def __init__(
        self,
        profile_path: str = "data/candidate_profile.json",
        credentials_path: str = "submission/.secrets.env",
        resume_pdf_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
        headless: bool = True,
        dry_run: Optional[bool] = None,
    ):
        self.profile_path = Path(profile_path)
        self.credentials_path = credentials_path
        self.resume_pdf_path = Path(resume_pdf_path) if resume_pdf_path else None
        self.cover_letter_path = Path(cover_letter_path) if cover_letter_path else None
        self.headless = headless
        self.dry_run = (
            dry_run
            if dry_run is not None
            else os.environ.get("AUTO_APPLY_DRY_RUN", "false").lower() == "true"
        )
        self._profile: Optional[dict] = None
        self._creds: Optional[dict] = None

    # ── Lazy loaders ──────────────────────────────────────────────────────────

    @property
    def profile(self) -> dict:
        if self._profile is None:
            if not self.profile_path.exists():
                raise FileNotFoundError(
                    f"Candidate profile not found: {self.profile_path}. "
                    "Run the pipeline first to generate it."
                )
            self._profile = json.loads(self.profile_path.read_text())
        return self._profile

    @property
    def creds(self) -> dict:
        if self._creds is None:
            self._creds = _load_credentials(self.credentials_path)
        return self._creds

    # ── Main entry point ──────────────────────────────────────────────────────

    def apply(
        self,
        job_url: str,
        resume_pdf_path: Optional[Path] = None,
        cover_letter_path: Optional[Path] = None,
    ) -> dict:
        """
        Navigate to job_url and submit an application.
        Returns a result dict with keys: success, platform, url, error.
        """
        resume = resume_pdf_path or self.resume_pdf_path
        cover = cover_letter_path or self.cover_letter_path
        platform = detect_platform(job_url)

        if self.dry_run:
            logger.info("[DRY RUN] Would apply to %s via %s", job_url, platform)
            return {"success": False, "platform": platform, "url": job_url,
                    "error": "dry_run"}

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        logger.info("Starting Playwright apply: %s (platform=%s)", job_url, platform)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/Los_Angeles",
                viewport={"width": 1280, "height": 900},
            )
            # Mask automation signals
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
            """)

            page = context.new_page()
            try:
                page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
                _human_delay(1500, 3000)

                applier = _PLATFORM_APPLIERS.get(platform, _PLATFORM_APPLIERS["generic"])
                success = applier.apply(
                    page=page,
                    profile=self.profile,
                    resume_path=resume,
                    cover_letter_path=cover,
                    creds=self.creds,
                )

                result = {
                    "success": success,
                    "platform": platform,
                    "url": page.url,
                    "error": None,
                }
                logger.info("Apply result: %s", result)
                return result

            except Exception as e:
                logger.error("AutoApplier error on %s: %s", job_url, e, exc_info=True)
                return {"success": False, "platform": platform,
                        "url": job_url, "error": str(e)}
            finally:
                context.close()
                browser.close()
