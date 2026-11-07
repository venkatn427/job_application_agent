import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from submission.portal_accounts import PortalAccountAgent
from submission.portal_store import PortalStore, generate_password


class PortalAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = PortalStore(root / 'portals.db', root / 'key')

    def test_generation_encryption_and_reuse(self):
        result = self.store.prepare_account('Example', 'https://portal.example.test/register', 'sample@example.test')
        email, password = self.store.get_credentials(result['id'])
        self.assertEqual(email, 'sample@example.test')
        self.assertEqual(len(password), 24)
        for predicate in (str.islower, str.isupper, str.isdigit):
            self.assertTrue(any(predicate(char) for char in password))
        self.assertTrue(any(char in '!@#$%*-_' for char in password))
        self.assertNotIn(password.encode(), self.store.db_path.read_bytes())
        self.assertNotIn(password, json.dumps(self.store.list_portals()))
        again = self.store.prepare_account('Example', 'https://portal.example.test/login', email)
        self.assertFalse(again['created'])
        self.assertEqual(self.store.get_credentials(again['id'])[1], password)
        self.assertEqual(self.store._key_path.stat().st_mode & 0o777, 0o600)

    def test_credentials_bound_to_origin_and_email(self):
        self.store.prepare_account('A', 'https://one.example.test/register', 'old@example.test')
        self.assertIsNone(self.store.find_for_url('https://two.example.test/register', 'old@example.test'))
        self.assertIsNone(self.store.find_for_url('https://one.example.test/register', 'new@example.test'))
        with self.assertRaises(ValueError):
            self.store.prepare_account('A', 'http://example.test', 'sample@example.test')

    def test_registration_does_not_claim_success_without_confirmation(self):
        from unittest.mock import MagicMock
        portal = self.store.prepare_account('A', 'https://one.example.test/register', 'sample@example.test')
        page = MagicMock()
        page.url = 'https://one.example.test/register'
        button = page.get_by_role.return_value
        button.count.return_value = 1
        button.is_visible.return_value = True
        result = PortalAccountAgent(self.store).submit_account_form(page, portal['id'], 'register')
        self.assertEqual(result['status'], 'verification_or_review_required')
        button.click.assert_called_once()
        page.url = 'https://other.example.test/register'
        with self.assertRaises(ValueError):
            PortalAccountAgent(self.store).fill(page, portal['id'], {})

    def test_identity_overrides_cached_and_nested_email(self):
        from submission.identity import current_profile
        with patch('submission.identity.application_email', return_value='current@example.test'):
            profile = current_profile({'email': 'old@example.test', 'personal': {'email': 'old@example.test'}})
            self.assertEqual(profile['email'], 'current@example.test')
            self.assertEqual(profile['personal']['email'], 'current@example.test')

    def test_password_bounds_and_uniqueness(self):
        self.assertNotEqual(generate_password(), generate_password())
        with self.assertRaises(ValueError):
            generate_password(8)
