"""Real Chromium sample against in-memory forms. No live submissions.
Run with --ai to exercise the configured AI field advisor on synthetic data.
"""
import argparse
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from submission.auto_apply import _GreenhouseApplier, _LeverApplier, application_scope
from submission.apply_engine import get_advisor
from submission.portal_store import PortalStore
from submission.portal_accounts import PortalAccountAgent
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--ai', action='store_true')
args = parser.parse_args()
profile = {'personal': {'first_name': 'Sample', 'last_name': 'Candidate',
                        'email': 'sample@example.test', 'phone': '5550100'},
           'professional': {'current_title': 'Software Engineer'}}
answer = 'Software Engineer'
if args.ai:
    advisor = get_advisor()
    if advisor is None:
        raise SystemExit('AI sample unavailable: no key configured for the fields route')
    answer = advisor.answer_field('Current job title', 'text', [], profile)
    if not answer:
        raise SystemExit('AI sample failed: provider returned no answer')
    print('AI field advisor returned an answer')

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.route('**/*', lambda route: route.abort())
    for platform, applier, marker in (
        ('greenhouse', _GreenhouseApplier(), 'id="application_confirmation"'),
        ('lever', _LeverApplier(), 'class="application-confirmation"'),
    ):
        for valid in (True, False):
            page.set_content('''<a id="app-link" class="postings-btn" href="#">Apply</a>
            <form onsubmit="event.preventDefault(); document.querySelector('#result').hidden=false">
            <input id="first_name"><input id="last_name"><input id="email" name="email">
            <input id="phone" name="phone"><input name="name"><input name="org">
            <input id="job_application_answers_attributes_0_text_value">
            <input name="urls[LinkedIn]"><input id="ai_answer">
            ''' + ('' if valid else '<input required aria-label="Unanswered required field">') +
            '<button type="submit">Submit</button></form><div id="result" hidden><div ' + marker +
            '>Application submitted</div></div>')
            page.fill('#ai_answer', answer)
            # Keep production confirmation logic, shorten only its timeout for rejection samples.
            original_wait = page.wait_for_selector
            def wait(selector, **kwargs):
                kwargs['timeout'] = 500
                return original_wait(selector, **kwargs)
            with patch('submission.auto_apply._human_delay'), patch.object(page, 'wait_for_selector', side_effect=wait):
                result = applier.apply(page, profile, None, None, {})
            assert result == valid, (platform, valid, result)
            if platform == 'greenhouse':
                assert page.input_value('#first_name') == 'Sample'
            assert page.input_value('#email') == 'sample@example.test'
            print(platform, 'confirmed' if valid else 'validation rejected', 'PASS')
    # Synthetic HTTPS origin is fulfilled in-process; no portal request is sent.
    account_page = browser.new_page()
    account_page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='''
        <form onsubmit="event.preventDefault(); document.querySelector('#account-success').hidden=false">
          <input name="first_name"><input name="last_name"><input type="email" required>
          <input type="password" required><input type="password" name="confirm" required>
          <button type="submit">Create account</button>
        </form><div id="account-success" hidden>Account created</div>'''))
    account_page.goto('https://portal.example.test/register')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = PortalStore(root / 'accounts.db', root / 'key')
        result = store.prepare_account('Synthetic portal', account_page.url, 'sample@example.test')
        agent = PortalAccountAgent(store)
        assert agent.fill(account_page, result['id'], profile)['status'] == 'form_filled'
        assert account_page.locator('input[type="email"]').input_value() == 'sample@example.test'
        password = store.get_credentials(result['id'])[1]
        assert all(field.input_value() == password for field in account_page.locator('input[type="password"]').all())
        assert agent.submit_account_form(account_page, result['id'], 'register', '#account-success')['status'] == 'confirmed'
        assert store.prepare_account('Synthetic portal', account_page.url, 'sample@example.test')['id'] == result['id']
        print('portal registration, encrypted password reuse, confirmation PASS')
    # Employer wrapper -> Apply link -> cross-origin embedded form.
    wrapper = browser.new_page()
    def wrapper_route(route):
        url = route.request.url
        if 'greenhouse.io' in url:
            body = '''<form onsubmit="event.preventDefault();this.hidden=true;document.querySelector('#application_confirmation').hidden=false">
            <label for="first_name">First Name</label><input id="first_name" aria-required="true">
            <label for="last_name">Last Name</label><input id="last_name" aria-required="true">
            <label for="email">Email</label><input id="email" aria-required="true">
            <input id="phone"><input type="file" id="resume">
            <label for="manual">Unknown required question</label><input id="manual" required>
            <button type="submit">Submit application</button></form>
            <div id="application_confirmation" hidden>Application submitted</div>'''
        elif url.endswith('/apply'):
            body = '<form><input id="newsletter-email"><button type="submit" hidden>Subscribe</button></form><iframe src="https://job-boards.greenhouse.io/embed/job_app?for=synthetic&amp;token=123"></iframe>'
        else:
            body = '<a href="/apply">Apply for this role</a>'
        route.fulfill(content_type='text/html', body=body)
    wrapper.route('**/*', wrapper_route)
    wrapper.goto('https://employer.example.test/job')
    scope, platform = application_scope(wrapper, 'generic')
    assert platform == 'greenhouse'
    applier = _GreenhouseApplier()
    review_calls = []
    def review(missing):
        review_calls.extend(missing)
        assert scope.input_value('#email') == 'sample@example.test'
        assert scope.locator('#resume').evaluate('e=>e.files.length') == 1
        scope.fill('#manual', 'Synthetic answer')
    applier.on_form_ready = review
    with tempfile.TemporaryDirectory() as directory:
        resume = Path(directory) / 'resume.pdf'
        resume.write_bytes(b'synthetic resume')
        with patch('submission.auto_apply._human_delay'):
            assert applier.apply(scope, profile, resume, None, {}) is True
    assert review_calls == ['Unknown required question']
    assert wrapper.input_value('#newsletter-email') == ''
    print('employer Apply link, embedded Greenhouse, upload, manual completion PASS')
    browser.close()
