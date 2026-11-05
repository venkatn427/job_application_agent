"""
create_account.py — Create job portal accounts using saved profile details.
---------------------------------------------------------------------------
Usage:
    python submission/create_account.py --portal linkedin
    python submission/create_account.py --portal workday --save
    python submission/create_account.py --portal indeed --dry-run

Pauses ONLY for email/phone OTP verification. Everything else is automated.
Uses PlaywrightEngine (local Chromium) — no Claude needed.
"""

import argparse
import getpass
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Portal signup configs ─────────────────────────────────────────────────────

PORTAL_CONFIGS = {
    "linkedin": {
        "signup_url": "https://www.linkedin.com/signup",
        "fields": {
            "email":      ["#email-address", "input[name='session_key']", "input[type='email']"],
            "password":   ["#password",      "input[name='session_password']", "input[type='password']"],
            "first_name": ["#firstName",     "input[name='firstName']"],
            "last_name":  ["#lastName",      "input[name='lastName']"],
        },
        "submit":           ["button[data-id='sign-up-submit']", "button[type='submit']"],
        "verify_selectors": ["#email-verification-pin", "input[name='pin']",
                             "input[placeholder*='code']", "input[placeholder*='Code']"],
        "verify_submit":    ["button[type='submit']", "button[data-id='email-verify-submit']"],
        "success_signal":   ["feed", "mynetwork", "jobs"],  # substrings in URL after success
    },
    "indeed": {
        "signup_url": "https://secure.indeed.com/auth?hl=en_US&co=US&continue=%2F&tmpl=login&service=my&from=gnav-util-homepage&jsContinue=&empContinue=&action=Login&signup=1",
        "fields": {
            "email":    ["input[name='__email']", "input[type='email']", "#ifl-InputFormField-3"],
            "password": ["input[type='password']", "#ifl-InputFormField-7"],
        },
        "submit":           ["button[type='submit']", "#ifl-FormEl-primaryButton"],
        "verify_selectors": ["input[name='__vcode']", "input[placeholder*='code']",
                             "input[placeholder*='Code']"],
        "verify_submit":    ["button[type='submit']"],
        "success_signal":   ["indeed.com/account", "indeed.com/jobs", "indeed.com/recommended"],
    },
    "dice": {
        "signup_url": "https://www.dice.com/register",
        "fields": {
            "first_name": ["input[name='firstName']", "#firstName"],
            "last_name":  ["input[name='lastName']",  "#lastName"],
            "email":      ["input[name='email']",     "#email", "input[type='email']"],
            "password":   ["input[name='password']",  "#password", "input[type='password']"],
        },
        "submit":           ["button[type='submit']", "input[type='submit']"],
        "verify_selectors": ["input[name='verificationCode']", "input[placeholder*='code']"],
        "verify_submit":    ["button[type='submit']"],
        "success_signal":   ["dice.com/dashboard", "dice.com/career-profile"],
    },
    "ziprecruiter": {
        "signup_url": "https://www.ziprecruiter.com/registration",
        "fields": {
            "first_name": ["input[name='first_name']", "#first_name"],
            "last_name":  ["input[name='last_name']",  "#last_name"],
            "email":      ["input[name='email']",      "#email",    "input[type='email']"],
            "password":   ["input[name='password']",   "#password", "input[type='password']"],
        },
        "submit":           ["button[type='submit']", "input[type='submit']"],
        "verify_selectors": ["input[name='verification_code']", "input[placeholder*='code']"],
        "verify_submit":    ["button[type='submit']"],
        "success_signal":   ["ziprecruiter.com/jobs", "ziprecruiter.com/candidate"],
    },
    "workday": {
        # Workday signup URL varies by company; use generic pattern
        "signup_url": None,  # detected from job URL context
        "fields": {
            "email":      ["input[data-automation-id='email']",     "input[type='email']"],
            "password":   ["input[data-automation-id='password']",  "input[type='password']"],
            "first_name": ["input[data-automation-id='firstName']", "input[name='firstName']"],
            "last_name":  ["input[data-automation-id='lastName']",  "input[name='lastName']"],
        },
        "submit":           ["button[data-automation-id='createAccountButton']",
                             "button[type='submit']"],
        "verify_selectors": ["input[data-automation-id='verificationCode']",
                             "input[placeholder*='code']", "input[placeholder*='Code']"],
        "verify_submit":    ["button[data-automation-id='verifyButton']", "button[type='submit']"],
        "success_signal":   ["myworkday", "workday"],
    },
    "apple": {
        "signup_url": "https://appleid.apple.com/account",
        "fields": {
            "first_name": ["#firstName",          "input[name='firstName']"],
            "last_name":  ["#lastName",           "input[name='lastName']"],
            "email":      ["#primaryEmailAddress","input[name='primaryEmailAddress']", "input[type='email']"],
            "password":   ["#password",           "input[name='password']",           "input[type='password']"],
        },
        "submit":           ["button[name='submit']", "button[type='submit']"],
        "verify_selectors": ["#trustedDeviceCode", "input[name='code']",
                             "input[placeholder*='code']"],
        "verify_submit":    ["button[name='verify']", "button[type='submit']"],
        "success_signal":   ["appleid.apple.com/account", "appleid.apple.com/manage"],
    },
    "icims": {
        "signup_url": None,  # detected from company-specific iCIMS URL
        "fields": {
            "email":      ["input[name='username']", "input[type='email']"],
            "password":   ["input[name='password']", "input[type='password']"],
        },
        "submit":           ["input[type='submit']", "button[type='submit']"],
        "verify_selectors": ["input[name='code']", "input[placeholder*='code']"],
        "verify_submit":    ["button[type='submit']"],
        "success_signal":   ["icims.com"],
    },
    "smartrecruiters": {
        "signup_url": "https://jobs.smartrecruiters.com/",
        "fields": {
            "first_name": ["input[name='firstName']"],
            "last_name":  ["input[name='lastName']"],
            "email":      ["input[name='email']",    "input[type='email']"],
            "password":   ["input[name='password']", "input[type='password']"],
        },
        "submit":           ["button[type='submit']"],
        "verify_selectors": ["input[name='code']", "input[placeholder*='code']"],
        "verify_submit":    ["button[type='submit']"],
        "success_signal":   ["smartrecruiters.com"],
    },
}


# ── Core account creation logic ────────────────────────────────────────────────

def _try_fill(page, selectors: list, value: str) -> bool:
    """Try each selector in order; return True on first success."""
    import time, random
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(random.uniform(0.1, 0.3))
                el.fill(value)
                time.sleep(random.uniform(0.05, 0.15))
                return True
        except Exception:
            pass
    return False


def _try_click(page, selectors: list) -> bool:
    """Try each selector; return True on first successful click."""
    import time, random
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(random.uniform(0.3, 0.7))
                return True
        except Exception:
            pass
    return False


def _otp_pause(prompt: str = "Enter verification code from your email/phone: ") -> str:
    print(f"\n⏸  Verification required!")
    print(f"   {prompt}")
    code = input("   Code: ").strip()
    return code


def _detect_otp_screen(page) -> bool:
    text = ""
    try:
        text = page.inner_text("body").lower()[:2000]
    except Exception:
        pass
    return any(s in text for s in [
        "verification code", "verify your email", "verify your phone",
        "check your email", "enter the code", "confirm your email",
        "we sent a code", "sent you a code",
    ])


def create_account(
    portal: str,
    page,
    profile: dict,
    password: str,
    dry_run: bool = False,
    signup_url: Optional[str] = None,
) -> dict:
    """
    Create an account on the given portal.
    Returns {success, username, portal, error}.
    Pauses for OTP if needed.
    """
    config = PORTAL_CONFIGS.get(portal, {})
    url = signup_url or config.get("signup_url")

    if not url:
        return {"success": False, "portal": portal,
                "error": f"No signup URL known for portal '{portal}'. Pass --signup-url."}

    # Extract profile fields
    personal = profile.get("personal", {})
    first_name = personal.get("first_name", "")
    last_name  = personal.get("last_name",  "")
    email      = personal.get("email", "")

    if not email:
        return {"success": False, "portal": portal,
                "error": "No email found in profile.json personal.email"}

    logger.info("Creating %s account for %s", portal, email)

    if dry_run:
        logger.info("[DRY RUN] Would navigate to %s and fill: email=%s, name=%s %s",
                    url, email, first_name, last_name)
        return {"success": False, "portal": portal, "username": email, "error": "dry_run"}

    import time, random

    # Navigate to signup page
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(random.uniform(1.0, 2.0))

    fields = config.get("fields", {})

    # Fill form fields
    filled = {}
    if "first_name" in fields and first_name:
        filled["first_name"] = _try_fill(page, fields["first_name"], first_name)
    if "last_name" in fields and last_name:
        filled["last_name"] = _try_fill(page, fields["last_name"], last_name)
    if "email" in fields:
        filled["email"] = _try_fill(page, fields["email"], email)
    if "password" in fields:
        filled["password"] = _try_fill(page, fields["password"], password)

    logger.info("Fields filled: %s", filled)
    time.sleep(random.uniform(0.5, 1.0))

    # Submit
    submit_sels = config.get("submit", ["button[type='submit']", "input[type='submit']"])
    _try_click(page, submit_sels)
    time.sleep(random.uniform(2.0, 3.0))

    # OTP loop (up to 3 attempts)
    for attempt in range(3):
        if not _detect_otp_screen(page):
            break
        code = _otp_pause(f"Check email/phone for verification code (attempt {attempt+1}/3):")
        verify_sels = config.get("verify_selectors",
                                 ["input[placeholder*='code']", "input[name='code']"])
        _try_fill(page, verify_sels, code)
        time.sleep(0.5)
        verify_submit = config.get("verify_submit", ["button[type='submit']"])
        _try_click(page, verify_submit)
        time.sleep(2.0)

    current_url = page.url.lower()
    success_signals = config.get("success_signal", [])
    success = any(sig in current_url for sig in success_signals)

    if not success:
        # Check page text for success/error indicators
        text = ""
        try:
            text = page.inner_text("body").lower()[:1000]
        except Exception:
            pass
        success = any(s in text for s in [
            "welcome", "account created", "successfully", "verify your email",
            "check your inbox",
        ])

    return {
        "success": success,
        "portal": portal,
        "username": email,
        "url": page.url,
        "error": None if success else "Could not confirm account creation",
    }


def _find_signup_link(page) -> Optional[str]:
    """Scan current page for a 'Create account' or 'Sign up' link."""
    try:
        for text_hint in ["create account", "create an account", "sign up", "register", "join"]:
            links = page.query_selector_all("a")
            for link in links:
                try:
                    t = (link.inner_text() or "").lower().strip()
                    if text_hint in t:
                        return link.get_attribute("href")
                except Exception:
                    pass
    except Exception:
        pass
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create a job portal account using pre-saved profile details."
    )
    parser.add_argument("--portal",    required=True,
                        choices=list(PORTAL_CONFIGS.keys()) + ["generic"],
                        help="Portal name (linkedin, indeed, dice, workday, apple, ...)")
    parser.add_argument("--signup-url", help="Override signup URL (required for workday/icims)")
    parser.add_argument("--email",      help="Override email from profile.json")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Fill form but do not submit; for testing")
    parser.add_argument("--save",       action="store_true",
                        help="Save credentials to portal store after success")
    parser.add_argument("--headless",   action="store_true", default=False,
                        help="Run headless (default: visible browser so you can enter OTP)")
    parser.add_argument("--profile",    default="submission/profile.json",
                        help="Path to profile.json")
    args = parser.parse_args()

    # Load profile
    profile_path = ROOT / args.profile
    if not profile_path.exists():
        print(f"ERROR: Profile not found: {profile_path}")
        sys.exit(1)
    with open(profile_path) as f:
        profile = json.load(f)

    # Email override
    if args.email:
        profile.setdefault("personal", {})["email"] = args.email

    # Password: check portal store first, else prompt
    password = None
    if args.save:
        from submission.portal_store import PortalStore
        store = PortalStore()
        default = store.get_default_credentials()
        if default:
            _, password = default
            print(f"Using default credentials from portal store.")

    if not password:
        # Check portal store for this specific portal
        try:
            from submission.portal_store import PortalStore
            store = PortalStore()
            match = store.find_by_platform(args.portal)
            if match:
                _, password = store.get_credentials(match["id"])
                print(f"Using existing credentials for {args.portal} from store.")
        except Exception:
            pass

    if not password:
        password = getpass.getpass(f"Password to use for new {args.portal} account: ")
        if not password:
            print("ERROR: Password required")
            sys.exit(1)

    # Launch browser
    from submission.apply_engine import PlaywrightEngine
    print(f"\n→ Launching browser (headless={args.headless})…")
    engine = PlaywrightEngine(headless=args.headless)
    engine.launch()

    try:
        result = create_account(
            portal=args.portal,
            page=engine.page,
            profile=profile,
            password=password,
            dry_run=args.dry_run,
            signup_url=args.signup_url,
        )
    finally:
        if not args.dry_run:
            input("\nPress Enter to close the browser…")
        engine.close()

    print(f"\n{'✓' if result['success'] else '✗'} Result: {json.dumps(result, indent=2)}")

    if result["success"] and args.save:
        from submission.portal_store import PortalStore
        store = PortalStore()
        config = PORTAL_CONFIGS.get(args.portal, {})
        login_url = config.get("signup_url", result.get("url", ""))
        pid = store.add_portal(
            name=args.portal.capitalize(),
            url=login_url,
            username=result["username"],
            password=password,
            platform=args.portal,
        )
        print(f"✓ Saved to portal store (id={pid})")


if __name__ == "__main__":
    main()
