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
import re
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


def application_scope(page, platform, on_step=lambda message: None):
    """Follow employer Apply links and find supported embedded application forms."""
    import re
    from urllib.parse import urljoin, urlsplit
    for _ in range(3):
        embeds = page.locator('iframe[src*="greenhouse.io"]')
        for index in range(embeds.count()):
            element = embeds.nth(index)
            host = (urlsplit(element.get_attribute('src') or '').hostname or '').lower()
            if host.endswith('.greenhouse.io'):
                frame = element.element_handle().content_frame()
                if frame:
                    frame.wait_for_selector('#first_name', timeout=20000)
                    on_step('Found the embedded Greenhouse application form.')
                    return frame, 'greenhouse'
        for frame in page.frames:
            host = (urlsplit(frame.url).hostname or '').lower()
            if host.endswith('.greenhouse.io') and ('embed' in frame.url or 'job_app' in frame.url):
                frame.wait_for_selector('#first_name', timeout=20000)
                on_step('Found the embedded Greenhouse application form.')
                return frame, 'greenhouse'
        if platform != 'generic':
            return page, platform
        links = page.get_by_role('link', name=re.compile(r'^apply(?: now| for (?:this|the) (?:role|job)| for this position)?$', re.I))
        link = next((links.nth(i) for i in range(links.count()) if links.nth(i).is_visible()), None)
        if link is None:
            # Embedded forms may load after the surrounding employer page.
            try:
                page.locator('iframe[src*="greenhouse.io"]').first.wait_for(state='attached', timeout=3000)
                continue
            except Exception:
                return page, detect_platform(page.url)
        href = link.get_attribute('href') or ''
        on_step('Opening the role’s application form…')
        if href and not href.startswith('#'):
            destination = urljoin(page.url, href)
            if urlsplit(destination).scheme != 'https':
                raise ValueError('Application link must use HTTPS')
            page.goto(destination, wait_until='domcontentloaded', timeout=30000)
        else:
            link.click(timeout=5000)
        platform = detect_platform(page.url)
    return page, platform


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

def _confirmed_submission(page, platform: str) -> bool:
    """Require visible ATS confirmation; a completed click is not success."""
    selectors = {
        "greenhouse": "#application_confirmation, #application-confirmation",
        "lever": ".application-confirmation, .application-success",
    }
    try:
        page.wait_for_selector(selectors[platform], state="visible", timeout=15_000)
        return True
    except Exception:
        logger.warning("%s submission could not be confirmed", platform)
        return False


def _contact_profile(profile: dict) -> dict:
    personal = profile.get("personal", profile)
    name = personal.get("name") or " ".join(
        filter(None, (personal.get("first_name"), personal.get("last_name")))
    )
    return {**profile, **personal, "name": name,
            "linkedin_url": personal.get("linkedin_url") or
            profile.get("professional", {}).get("linkedin_url", "")}


class _GreenhouseApplier:
    """Apply via Greenhouse Jobboard embed."""

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        saved_profile = profile
        profile = _contact_profile(profile)
        # Click Apply button if present
        try:
            links = page.locator("a#app-link, a.btn-apply, a[data-role='apply']")
            if links.count() and links.first.is_visible():
                links.first.click()
                _human_delay(800, 1500)
        except Exception:
            pass  # may already be on form

        # Fill name
        self._fill_if_exists(page, "#first_name", next(iter(profile.get("name", "").split()), ""))
        self._fill_if_exists(page, "#last_name", " ".join(profile.get("name", "").split()[1:]))
        self._fill_if_exists(page, "#email", profile.get("email", ""))
        self._fill_if_exists(page, "#phone", profile.get("phone", ""))

        # Upload resume
        if resume_path and resume_path.exists():
            try:
                page.set_input_files("#resume, input[name='resume']", str(resume_path))
                _human_delay(500, 1000)
            except Exception:
                logger.warning("Could not upload resume to Greenhouse form")

        # Upload cover letter
        if cover_letter_path and cover_letter_path.exists():
            try:
                page.set_input_files("#cover_letter, input[name='cover_letter']", str(cover_letter_path))
                _human_delay(500, 1000)
            except Exception:
                pass

        # LinkedIn URL
        self._fill_if_exists(page, "#job_application_answers_attributes_0_text_value",
                             profile.get("linkedin_url", ""))

        from submission.form_fields import fill_known_fields, missing_required
        fill_known_fields(page, saved_profile)
        missing = missing_required(page)
        if missing:
            review = getattr(self, 'on_form_ready', None)
            if review:
                review(missing)
                missing = missing_required(page)
            if missing:
                logger.warning('Application has %d unanswered required fields', len(missing))
                return False

        # Submit
        _human_delay(1000, 2000)
        try:
            button = page.get_by_role('button', name=re.compile(r'^submit(?: application)?$', re.I))
            if button.count():
                button.first.click(timeout=10000)
            else:
                page.locator("input[type='submit']:visible, button[type='submit']:visible").first.click(timeout=10000)
            return _confirmed_submission(page, "greenhouse")
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
        profile = _contact_profile(profile)
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
            return _confirmed_submission(page, "lever")
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

    @staticmethod
    def _flatten(profile: dict) -> dict:
        """Flatten nested profile.json → {first_name, last_name, email, phone, ...}."""
        p = profile.get("personal", profile)  # support both nested and flat
        name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        flat = {
            "first_name":    p.get("first_name", ""),
            "last_name":     p.get("last_name", ""),
            "name":          name,
            "email":         p.get("email", ""),
            "phone":         p.get("phone", ""),
            "location":      p.get("city", ""),
            "linkedin_url":  profile.get("professional", {}).get("linkedin_url", ""),
            "github_url":    profile.get("professional", {}).get("github_url", ""),
            "portfolio_url": profile.get("professional", {}).get("portfolio_url", ""),
        }
        # Also keep any top-level flat keys that already exist
        for k, v in profile.items():
            if isinstance(v, str) and k not in flat:
                flat[k] = v
        return flat

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        # A newsletter or marketing form is not a job application.
        if not page.locator('input[type="file"], input[autocomplete="given-name"], input[name*="first_name"], input[id="first_name"]').count():
            logger.warning('No recognizable application form on the current page')
            return False
        profile = self._flatten(profile)

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
    Dice Easy Apply:
    1. Login with stored credentials (two-step: email → Continue → password → Sign In)
    2. Navigate to job detail, click Easy Apply
    3. Fill multi-step application form from profile
    4. Upload resume, submit
    """

    def apply(self, page, profile: dict, resume_path: Optional[Path],
              cover_letter_path: Optional[Path], creds: dict) -> bool:
        import time

        email, password = _get_login(creds, "dice")
        if not email:
            email, password = _get_login(creds, "generic")

        job_url = page.url

        # ── Step 1: Login ─────────────────────────────────────────────────────
        if email and password:
            try:
                page.goto("https://www.dice.com/dashboard/login",
                          wait_until="domcontentloaded", timeout=20_000)
                _human_delay(800, 1200)

                # Fill email
                email_el = page.query_selector("input[name='email'], input[type='email']")
                if email_el and email_el.is_visible():
                    email_el.fill(email)
                    _human_delay(300, 600)

                # Click Continue with email
                for sel in ["button[type='submit']", "button:has-text('Continue')"]:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click(); break
                    except Exception:
                        pass
                _human_delay(1500, 2500)

                # Fill password (second step)
                pw_el = page.query_selector("input[type='password']")
                if pw_el and pw_el.is_visible():
                    pw_el.fill(password)
                    _human_delay(300, 600)
                    for sel in ["button[type='submit']", "button:has-text('Sign In')"]:
                        try:
                            btn = page.query_selector(sel)
                            if btn and btn.is_visible():
                                btn.click(); break
                        except Exception:
                            pass
                    _human_delay(2000, 3000)

                logger.info("Dice login complete, URL: %s", page.url)
            except Exception as e:
                logger.warning("Dice login failed: %s — continuing anyway", e)

        # ── Step 2: Navigate to job ───────────────────────────────────────────
        if job_url not in page.url:
            page.goto(job_url, wait_until="domcontentloaded", timeout=20_000)
            _human_delay(1500, 2500)

        # ── Step 3: Click Easy Apply ──────────────────────────────────────────
        applied = False
        for sel in [
            "button[data-testid='apply-button-top']",
            "button:has-text('Easy Apply')",
            "button:has-text('Apply Now')",
            "a:has-text('Easy Apply')",
            "a:has-text('Apply Now')",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    _human_delay(1500, 2500)
                    applied = True
                    break
            except Exception:
                pass

        if not applied:
            logger.warning("No Easy Apply button found — falling back to generic form fill")

        # ── Step 4: Fill application form (multi-step) ────────────────────────
        flat = _GenericApplier._flatten(profile)

        for _step in range(6):  # up to 6 form pages
            _human_delay(800, 1200)

            # Fill standard fields
            field_map = {
                "first_name": flat.get("first_name", ""),
                "last_name":  flat.get("last_name", ""),
                "email":      flat.get("email", ""),
                "phone":      flat.get("phone", ""),
                "name":       flat.get("name", ""),
            }
            for fragment, value in field_map.items():
                if not value:
                    continue
                for attr in ("name", "id", "placeholder", "data-testid"):
                    sel = f"input[{attr}*='{fragment}' i]"
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible() and not el.input_value():
                            el.fill(value)
                            _human_delay(100, 300)
                            break
                    except Exception:
                        pass

            # Upload resume if file input present
            if resume_path and Path(resume_path).exists():
                try:
                    for inp in page.query_selector_all("input[type='file']"):
                        accept = inp.get_attribute("accept") or ""
                        if "pdf" in accept or "doc" in accept or not accept:
                            inp.set_input_files(str(resume_path))
                            _human_delay(800, 1500)
                            break
                except Exception:
                    pass

            # Try Next / Continue / Submit
            clicked_next = False
            for sel in [
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button[type='submit']",
                "button:has-text('Submit')",
                "input[type='submit']",
            ]:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        _human_delay(1200, 2000)
                        clicked_next = True
                        # If we hit Submit, we're done
                        if "submit" in sel.lower():
                            logger.info("Dice apply: submitted on step %d", _step)
                            return True
                        break
                except Exception:
                    pass

            if not clicked_next:
                break

        # Final check — URL changed away from job detail = likely submitted
        return job_url not in page.url


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
    dry_run            If True (default), skip browser launch entirely
    submission_approved Explicit approval required in addition to dry_run=False
    """

    def __init__(
        self,
        profile_path: str = "data/candidate_profile.json",
        credentials_path: str = "submission/.secrets.env",
        resume_pdf_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
        headless: bool = True,
        dry_run: Optional[bool] = None,
        extra_creds: Optional[dict] = None,
        on_step=None,
        submission_approved: bool = False,
        portal_id: Optional[int] = None,
        on_login_ready=None,
        on_form_ready=None,
    ):
        self.profile_path = Path(profile_path)
        self.credentials_path = credentials_path
        self.resume_pdf_path = Path(resume_pdf_path) if resume_pdf_path else None
        self.cover_letter_path = Path(cover_letter_path) if cover_letter_path else None
        self.headless = headless
        self.dry_run = (
            dry_run
            if dry_run is not None
            else os.environ.get("AUTO_APPLY_DRY_RUN", "true").lower() != "false"
        )
        self.submission_approved = submission_approved is True
        self.portal_id = portal_id
        self.on_login_ready = on_login_ready
        self.on_form_ready = on_form_ready
        self._extra_creds: dict = extra_creds or {}
        self._on_step = on_step or (lambda msg: None)
        self._profile: Optional[dict] = None
        self._creds: Optional[dict] = None

    # ── Lazy loaders ──────────────────────────────────────────────────────────

    @property
    def profile(self) -> dict:
        if self._profile is None:
            path = self.profile_path
            if not path.exists():
                # Fall back to submission/profile.json (the user's saved profile)
                fallback = Path(__file__).parent / "profile.json"
                if fallback.exists():
                    path = fallback
                else:
                    raise FileNotFoundError(
                        f"Candidate profile not found: {self.profile_path}. "
                        "Run the pipeline first to generate it."
                    )
            from submission.identity import current_profile
            self._profile = current_profile(json.loads(path.read_text()))
        return self._profile

    @property
    def creds(self) -> dict:
        if self._creds is None:
            base = _load_credentials(self.credentials_path)
            self._creds = {**base, **self._extra_creds}
            from submission.identity import application_email
            email = application_email()
            if email:
                for key in list(self._creds):
                    if key.endswith('_EMAIL') and self._creds[key].casefold() != email.casefold():
                        self._creds.pop(key, None)
                        self._creds.pop(key[:-6] + '_PASSWORD', None)
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
        self._on_step(f"Platform detected: {platform}")

        if self.dry_run:
            logger.info("[DRY RUN] Would apply to %s via %s", job_url, platform)
            self._on_step("DRY RUN — skipping browser launch")
            return {"success": False, "platform": platform, "url": job_url,
                    "error": "dry_run"}

        if not self.submission_approved:
            return {"success": False, "platform": platform, "url": job_url,
                    "error": "submission_approval_required"}

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._on_step("Launching browser…")
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
                if self.portal_id:
                    from submission.portal_store import PortalStore
                    from submission.portal_accounts import PortalAccountAgent
                    from submission.identity import ROOT, application_email
                    store = PortalStore(ROOT / 'data/applications.db', ROOT / 'data/.portal_key')
                    portal = store.get_portal(self.portal_id)
                    if not portal or (application_email() and portal['username'].casefold() != application_email().casefold()):
                        raise ValueError('Selected portal login does not match the application email')
                    self._on_step('Opening saved portal login…')
                    page.goto(portal['url'], wait_until='domcontentloaded', timeout=30000)
                    account = PortalAccountAgent(store)
                    try:
                        state = account.fill(page, self.portal_id, self.profile, 'login')
                        if state['status'] == 'form_filled':
                            account.submit_account_form(page, self.portal_id, 'login')
                    except ValueError:
                        self._on_step('This portal needs manual login in the visible browser.')
                    if self.on_login_ready:
                        self.on_login_ready()
                    else:
                        raise ValueError('Complete portal login in the dashboard before continuing')
                self._on_step(f"Navigating to {job_url[:70]}…")
                page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
                _human_delay(1500, 3000)

                scope, platform = application_scope(page, detect_platform(page.url), self._on_step)

                self._on_step(f"Running {platform} applier…")
                applier = type(_PLATFORM_APPLIERS.get(platform, _PLATFORM_APPLIERS["generic"]))()
                applier.on_form_ready = self.on_form_ready
                success = applier.apply(
                    page=scope,
                    profile=self.profile,
                    resume_path=resume,
                    cover_letter_path=cover,
                    creds=self.creds,
                )

                self._on_step("Done ✓" if success else "Applier returned failure")
                result = {
                    "success": success,
                    "platform": platform,
                    "url": page.url,
                    "error": None if success else 'Application was not confirmed. Required fields or a portal-specific step may need attention.',
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
