"""
Indeed Account Creation + Apply Test
--------------------------------------
Run this to test the full Indeed flow:
  1. Creates or logs into an Indeed account
  2. Finds a real job posted today
  3. Walks through the apply form (stops before final submit unless --submit)

Usage:
    python test_indeed_apply.py                # dry run — fills form, does NOT submit
    python test_indeed_apply.py --submit       # actually submits
    python test_indeed_apply.py --headless     # run without browser window (CI mode)
    python test_indeed_apply.py --query "Data Engineer"
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("test_indeed")

ROOT = Path(__file__).parent


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_profile() -> dict:
    path = ROOT / "submission" / "profile.json"
    data = json.loads(path.read_text())
    personal = data.get("personal", {})
    prof = data.get("professional", {})
    return {
        "first_name": personal.get("first_name", ""),
        "last_name":  personal.get("last_name", ""),
        "email":      personal.get("email", ""),
        "phone":      personal.get("phone", ""),
        "location":   f"{personal.get('city','')}, {personal.get('state','')}".strip(", "),
        "linkedin_url": prof.get("linkedin_url", ""),
        "current_title": prof.get("current_title", ""),
    }


def _load_creds() -> tuple[str, str]:
    path = ROOT / "submission" / ".secrets.env"
    creds: dict = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            creds[k.strip()] = v.strip().strip('"').strip("'")
    email = creds.get("INDEED_EMAIL") or creds.get("APPLY_EMAIL", "")
    pw    = creds.get("INDEED_PASSWORD") or creds.get("APPLY_PASSWORD", "")
    return email, pw


def _human_delay(lo=300, hi=900):
    time.sleep(random.uniform(lo / 1000, hi / 1000))


def _fill(page, selector: str, value: str, label: str = ""):
    if not value:
        return
    try:
        el = page.query_selector(selector)
        if el:
            el.click()
            _human_delay(100, 300)
            el.fill(value)
            _human_delay(150, 400)
            log.info("  filled %-25s = %s", label or selector, value[:30])
    except Exception as e:
        log.debug("  could not fill %s: %s", label or selector, e)


# ─────────────────────────────────────────────────────────────────────────────
# Account management
# ─────────────────────────────────────────────────────────────────────────────

def ensure_indeed_account(page, email: str, password: str, profile: dict) -> bool:
    """Login or create an Indeed account. Returns True on success."""
    log.info("Navigating to Indeed login...")
    page.goto("https://secure.indeed.com/account/login",
              wait_until="domcontentloaded", timeout=20_000)
    _human_delay(1000, 2000)

    # Try to fill email
    email_sel = "input[name='__email'], input[type='email'], #emailOrUsername"
    _fill(page, email_sel, email, "email")
    _human_delay(300, 600)

    # Click Continue / Next
    try:
        page.click("button#login-submit-button, button[type='submit']")
        _human_delay(1200, 2000)
    except Exception:
        pass

    # Check if password field appeared
    pw_field = page.query_selector("input[type='password'], input[name='__password']")
    if pw_field:
        log.info("Account exists — logging in...")
        pw_field.fill(password)
        _human_delay(400, 700)
        try:
            page.click("button#login-submit-button, button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=20_000)
            _human_delay(1000, 2000)
        except Exception:
            pass

        if "login" not in page.url and "checkpoint" not in page.url:
            log.info("Login successful: %s", page.url)
            return True
        log.warning("Login may have failed — URL: %s", page.url)
        return False

    # No password field → account might not exist; try to create one
    log.info("No password field found — attempting account creation...")
    page.goto("https://secure.indeed.com/account/register",
              wait_until="domcontentloaded", timeout=20_000)
    _human_delay(800, 1500)

    _fill(page, "input[type='email'], input[name='__email']", email, "email")
    _human_delay(300, 600)

    # Indeed may ask for first/last name on registration
    _fill(page, "input[name='firstName'], #firstName", profile["first_name"], "first_name")
    _fill(page, "input[name='lastName'], #lastName",   profile["last_name"],  "last_name")

    # Set password
    _fill(page, "input[type='password'], input[name='__password']", password, "password")
    _human_delay(300, 600)

    try:
        page.click("button[type='submit']")
        _human_delay(1500, 2500)
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass

    log.info("Account creation attempted. Current URL: %s", page.url)
    return "login" not in page.url


# ─────────────────────────────────────────────────────────────────────────────
# Job search
# ─────────────────────────────────────────────────────────────────────────────

def find_easy_apply_job(page, query: str) -> str | None:
    """Search Indeed for today's jobs and return the first Easy Apply URL."""
    import urllib.parse
    search_url = (
        "https://www.indeed.com/jobs?"
        + urllib.parse.urlencode({
            "q": query,
            "fromage": "1",        # posted in last 1 day
            "iafilter": "1",       # Indeed Apply (Easy Apply) only
            "sort": "date",
        })
    )
    log.info("Searching Indeed: %s", search_url)
    page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
    _human_delay(1500, 2500)

    # Grab first job card link
    try:
        # Job cards have data-jk attribute
        cards = page.query_selector_all("a[data-jk]")
        if cards:
            href = cards[0].get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.indeed.com" + href
            log.info("Found job: %s", href)
            return href
    except Exception as e:
        log.warning("Could not find job cards: %s", e)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Apply flow
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_job(page, job_url: str, profile: dict, resume_path: Path,
                 dry_run: bool = True) -> bool:
    """Walk through the Indeed apply form. Returns True on success/dry-run."""
    log.info("Opening job: %s", job_url)
    page.goto(job_url, wait_until="domcontentloaded", timeout=20_000)
    _human_delay(1500, 2500)

    # Click the Apply / Indeed Apply button
    apply_clicked = False
    for sel in [
        "button[id*='apply']",
        "a[id*='applyButton']",
        "span[id*='applyButton']",
        "button[aria-label*='Apply']",
        "a[aria-label*='Apply']",
        ".jobsearch-IndeedApplyButton-contentWrapper",
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _human_delay(1500, 2500)
                apply_clicked = True
                log.info("Clicked apply button (%s)", sel)
                break
        except Exception:
            pass

    if not apply_clicked:
        log.warning("Apply button not found — may need to navigate into job detail first")

    # Walk multi-step form
    for step in range(1, 10):
        log.info("--- Step %d ---", step)
        _human_delay(600, 1000)

        # Upload resume
        resume_input = page.query_selector("input[type='file']")
        if resume_input and resume_path.exists():
            try:
                resume_input.set_input_files(str(resume_path))
                log.info("  uploaded resume: %s", resume_path.name)
                _human_delay(800, 1500)
            except Exception as e:
                log.debug("  resume upload: %s", e)

        # Fill common fields
        _fill(page, "input[name*='firstName' i], input[id*='firstName' i]",
               profile["first_name"], "first_name")
        _fill(page, "input[name*='lastName' i], input[id*='lastName' i]",
               profile["last_name"], "last_name")
        _fill(page, "input[name*='phone' i], input[type='tel']",
               profile["phone"], "phone")
        _fill(page, "input[name*='city' i], input[name*='location' i]",
               profile["location"], "location")

        # Answer yes/no questions
        try:
            radios = page.query_selector_all("input[type='radio']")
            for r in radios:
                label_text = ""
                try:
                    rid = r.get_attribute("id")
                    if rid:
                        lbl = page.query_selector(f"label[for='{rid}']")
                        if lbl:
                            label_text = (lbl.inner_text() or "").lower()
                except Exception:
                    pass
                # Answer "Yes" to work authorization, "Yes" to sponsorship need
                if any(w in label_text for w in ("yes", "authorized", "eligible")):
                    try:
                        r.check()
                        _human_delay(100, 300)
                    except Exception:
                        pass
        except Exception:
            pass

        # Answer number inputs (years of experience → use 5)
        try:
            num_inputs = page.query_selector_all("input[type='number']")
            for ni in num_inputs:
                if not ni.input_value():
                    ni.fill("5")
                    _human_delay(100, 300)
        except Exception:
            pass

        # Screenshot this step for review
        screenshot_path = ROOT / "output" / "test_indeed" / f"step_{step:02d}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(screenshot_path))
            log.info("  screenshot: %s", screenshot_path)
        except Exception:
            pass

        # Detect submit button
        submit_btn = None
        for sel in [
            "button[aria-label='Submit your application']",
            "button:has-text('Submit your application')",
            "button:has-text('Submit application')",
        ]:
            try:
                b = page.query_selector(sel)
                if b and b.is_visible():
                    submit_btn = b
                    break
            except Exception:
                pass

        if submit_btn:
            if dry_run:
                log.info("DRY RUN — submit button found but NOT clicked.")
                log.info("Screenshots saved to output/test_indeed/")
                return True
            else:
                log.info("Clicking Submit...")
                submit_btn.click()
                _human_delay(2000, 3000)
                page.wait_for_load_state("networkidle", timeout=20_000)
                log.info("Application submitted! URL: %s", page.url)
                return True

        # Try to advance to next step
        advanced = False
        for label in ("Continue", "Next", "Save and continue"):
            try:
                btn = page.query_selector(f"button:has-text('{label}')")
                if btn and btn.is_visible():
                    btn.click()
                    _human_delay(800, 1500)
                    advanced = True
                    log.info("  advanced to next step (%s)", label)
                    break
            except Exception:
                pass
        if not advanced:
            log.info("No more steps detected.")
            break

    log.info("Form walk complete. Screenshots in output/test_indeed/")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit",   action="store_true",
                        help="Actually submit (default is dry-run)")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser without visible window")
    parser.add_argument("--query",    default="Senior Software Engineer",
                        help="Job search query")
    parser.add_argument("--job-url",  default="",
                        help="Skip search and go straight to this job URL")
    args = parser.parse_args()

    dry_run = not args.submit
    if dry_run:
        log.info("=== DRY RUN — form will be filled but NOT submitted ===")
    else:
        log.info("=== LIVE RUN — will submit application ===")
        confirm = input("Type 'yes' to confirm real submission: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return

    # Load credentials and profile
    email, password = _load_creds()
    if not email or not password:
        print("\nIndeed credentials not found in submission/.secrets.env")
        print("Add these lines to submission/.secrets.env:")
        print("  INDEED_EMAIL=your@email.com")
        print("  INDEED_PASSWORD=yourpassword")
        sys.exit(1)

    profile = _load_profile()
    resume_path = ROOT / "data" / "master_resume" / "resume.pdf"
    if not resume_path.exists():
        log.warning("Resume not found at %s — will skip upload", resume_path)

    log.info("Profile: %s %s <%s>", profile["first_name"], profile["last_name"], email)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=50,   # adds 50ms between each action for stability
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
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()

        try:
            # Step 1: Account login / creation
            log.info("\n[1/3] Indeed account setup...")
            ok = ensure_indeed_account(page, email, password, profile)
            if not ok:
                log.warning("Account setup uncertain — continuing anyway")

            # Step 2: Find a job
            if args.job_url:
                job_url = args.job_url
                log.info("\n[2/3] Using provided job URL: %s", job_url)
            else:
                log.info("\n[2/3] Searching for: %r", args.query)
                job_url = find_easy_apply_job(page, args.query)
                if not job_url:
                    log.error("No Easy Apply job found. Try a different --query.")
                    sys.exit(1)

            # Step 3: Apply
            log.info("\n[3/3] Applying...")
            success = apply_to_job(page, job_url, profile, resume_path,
                                   dry_run=dry_run)

            print("\n" + ("=" * 60))
            print(f"  Result: {'DRY RUN OK' if dry_run else ('SUBMITTED' if success else 'FAILED')}")
            print(f"  Job URL: {job_url}")
            print(f"  Screenshots: output/test_indeed/")
            print("=" * 60)

        except Exception as e:
            log.error("Test failed: %s", e, exc_info=True)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
