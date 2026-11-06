"""Real Chromium sample against in-memory forms. No live submissions.
Run with --ai to exercise the configured AI field advisor on synthetic data.
"""
import argparse
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from submission.auto_apply import _GreenhouseApplier, _LeverApplier
from submission.apply_engine import get_advisor
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
    browser.close()
