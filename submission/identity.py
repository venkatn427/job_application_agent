"""Local application identity shared by non-AI and AI workflows."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def application_email():
    path = ROOT / 'data/application_identity.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text()).get('email')
    if not isinstance(value, str) or '@' not in value:
        raise ValueError('Invalid saved application email')
    return value


def current_profile(profile):
    email = application_email()
    if not email:
        return profile
    result = {**profile, 'email': email}
    if 'personal' in profile:
        result['personal'] = {**profile['personal'], 'email': email}
    return result
