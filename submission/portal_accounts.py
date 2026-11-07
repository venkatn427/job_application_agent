"""Non-AI portal registration and login. Never submits a job application."""
import re

from submission.portal_store import portal_origin


def _fill(page, selectors, value, required=False):
    for selector in selectors:
        elements = page.locator(selector)
        for index in range(elements.count()):
            element = elements.nth(index)
            if element.is_visible() and element.is_editable():
                element.fill(value)
                return True
    if required:
        raise ValueError('Required account field not found')
    return False


class PortalAccountAgent:
    def __init__(self, store):
        self.store = store

    def fill(self, page, portal_id, profile, action='register'):
        if action not in ('register', 'login'):
            raise ValueError('Account action must be register or login')
        portal = self.store.get_portal(portal_id)
        if not portal or portal_origin(page.url) != portal_origin(portal['url']):
            raise ValueError('Account page origin does not match saved credentials')
        email, password = self.store.get_credentials(portal_id)
        personal = profile.get('personal', profile)
        if action == 'register':
            _fill(page, ['input[autocomplete="given-name"]', 'input[name="first_name"]', 'input[name="firstName"]'], personal.get('first_name', ''))
            _fill(page, ['input[autocomplete="family-name"]', 'input[name="last_name"]', 'input[name="lastName"]'], personal.get('last_name', ''))
            _fill(page, ['input[autocomplete="name"]', 'input[name="name"]'], ' '.join(filter(None, [personal.get('first_name'), personal.get('last_name')])))
        _fill(page, ['input[type="email"]', 'input[autocomplete="username"]', 'input[name="email"]', 'input[name="username"]'], email, required=True)
        # Fill every visible password field, including confirmation fields.
        passwords = page.locator('input[type="password"]')
        filled = 0
        for index in range(passwords.count()):
            field = passwords.nth(index)
            if field.is_visible() and field.is_editable():
                field.fill(password)
                filled += 1
        if not filled:
            return {'status': 'manual_step_required', 'reason': 'password_field_not_visible', 'portal_id': portal_id}
        return {'status': 'form_filled', 'portal_id': portal_id}

    def submit_account_form(self, page, portal_id, action, confirmation_selector=None):
        portal = self.store.get_portal(portal_id)
        if not portal or portal_origin(page.url) != portal_origin(portal['url']):
            raise ValueError('Account page origin does not match saved credentials')
        if action not in ('register', 'login'):
            raise ValueError('Account action must be register or login')
        pattern = r'^(create account|create an account|register|sign up)$' if action == 'register' else r'^(sign in|log in|login)$'
        button = page.get_by_role('button', name=re.compile(pattern, re.I))
        if button.count() != 1 or not button.is_visible():
            return {'status': 'manual_step_required', 'reason': 'account_button_ambiguous', 'portal_id': portal_id}
        button.click()
        # A click or redirect alone never proves registration or login succeeded.
        if confirmation_selector:
            try:
                page.locator(confirmation_selector).wait_for(state='visible', timeout=10000)
                self.store.touch_last_used(portal_id)
                return {'status': 'confirmed', 'portal_id': portal_id}
            except Exception:
                pass
        return {'status': 'verification_or_review_required', 'portal_id': portal_id}
