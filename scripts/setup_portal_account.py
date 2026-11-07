"""Save a locally generated portal password; optionally open the account form."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.portal_store import PortalStore
from submission.portal_accounts import PortalAccountAgent
from submission.identity import application_email


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--url', required=True, help='Exact HTTPS registration or login page')
    parser.add_argument('--email', help='Defaults to saved application profile email')
    parser.add_argument('--platform')
    parser.add_argument('--profile', type=Path, default=ROOT / 'submission/profile.json')
    parser.add_argument('--open', action='store_true', help='Open a visible browser and fill the form')
    parser.add_argument('--action', choices=['register', 'login'], default='register')
    parser.add_argument('--submit-account', action='store_true', help='Submit only the account registration/login form')
    parser.add_argument('--confirmation-selector', help='Portal-specific visible success marker')
    args = parser.parse_args()
    if args.submit_account and not args.open:
        parser.error('--submit-account requires --open')
    try:
        profile = json.loads(args.profile.read_text())
        canonical = application_email()
        if args.email and canonical and args.email.casefold() != canonical.casefold():
            raise ValueError('Email must match the configured application identity')
        email = canonical or args.email or profile.get('personal', profile).get('email', '')
        store = PortalStore(ROOT / 'data/applications.db', ROOT / 'data/.portal_key')
        if args.action == 'login':
            portal = store.find_for_url(args.url, email)
            if not portal:
                raise ValueError('No saved login for this portal/email; add existing credentials in the dashboard first')
            result = {'id': portal['id'], 'created': False, 'status': 'credentials_saved'}
        else:
            result = store.prepare_account(args.name, args.url, email, args.platform)
        print(json.dumps(result))  # Passwords are never printed.
        if args.open:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=False)
                page = browser.new_page()
                try:
                    page.goto(args.url, wait_until='domcontentloaded')
                    agent = PortalAccountAgent(store)
                    state = agent.fill(page, result['id'], profile, args.action)
                    print(json.dumps(state))
                    if args.submit_account and state['status'] == 'form_filled':
                        print(json.dumps(agent.submit_account_form(page, result['id'], args.action, args.confirmation_selector)))
                    input('Complete any CAPTCHA or email verification in the browser, then press Enter to close: ')
                finally:
                    browser.close()
        return 0
    except Exception as exc:
        # Browser errors can include form values. Do not print their raw text.
        print(f'Account setup stopped ({type(exc).__name__}); saved credentials are retained.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
