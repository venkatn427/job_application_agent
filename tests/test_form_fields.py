import unittest
from submission.form_fields import answer_for


class LocalFormAnswerTests(unittest.TestCase):
    def test_known_facts_only_and_no_consent_guesses(self):
        profile = {'personal': {'country': 'US', 'city': 'Dallas'},
                   'professional': {'years_experience': 7},
                   'work_authorization': {'authorized_to_work_us': True, 'require_sponsorship': False}}
        self.assertEqual(answer_for('Please select the country where you currently reside. *', profile), 'United States')
        self.assertEqual(answer_for('Do you have more than 6 years of industry experience? Not including internship, co-op or academic.', profile), 'Yes')
        self.assertEqual(answer_for('Are you authorized to work in the location(s) you selected in your previous response?', profile), 'Yes')
        self.assertEqual(answer_for('Will you require Stripe to sponsor you for a work permit now or in the future?', profile), 'No')
        self.assertIsNone(answer_for('Do you consent to interview recording?', profile))
        self.assertIsNone(answer_for('Have you ever been employed by Stripe?', profile))
