# Auto-Apply Playbook

Guides `submission/auto_apply.py` for each supported ATS platform.

## Setup

```bash
pip install playwright
playwright install chromium
```

Add login credentials to `submission/.secrets.env`:
```
GREENHOUSE_EMAIL=your@email.com
GREENHOUSE_PASSWORD=yourpassword
LEVER_EMAIL=your@email.com
LEVER_PASSWORD=yourpassword
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
INDEED_EMAIL=your@email.com
INDEED_PASSWORD=yourpassword
# Fallback for unknown boards
APPLY_EMAIL=your@email.com
APPLY_PASSWORD=yourpassword
```

Set `AUTO_APPLY_DRY_RUN=true` to test without submitting.

## Usage

```python
from submission.auto_apply import AutoApplier

applier = AutoApplier(
    profile_path="data/candidate_profile.json",
    credentials_path="submission/.secrets.env",
    resume_pdf_path="output/documents/resume.pdf",
    cover_letter_path="output/documents/cover_letter.docx",
    headless=True,
    dry_run=False,
)

result = applier.apply("https://boards.greenhouse.io/company/jobs/123456")
# result = {"success": True, "platform": "greenhouse", "url": "...", "error": None}
```

## Platform strategies

### Greenhouse (`boards.greenhouse.io`)
- Fills: first_name, last_name, email, phone, LinkedIn URL
- Uploads: resume file input `input[name='resume']`
- Cover letter: `input[name='cover_letter']`
- Submit: `input[type='submit']`

### Lever (`jobs.lever.co`)
- Fills: name, email, phone, org (current title), LinkedIn
- Uploads: first `input[type='file']`
- Submit: `input[type='submit']`

### LinkedIn Easy Apply
- Requires login — uses `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`
- Walks multi-step form (up to 10 steps)
- Answers select dropdowns: sponsorship → Yes, work auth → Yes
- Uploads resume on file input steps

### Generic (all other boards)
- Scans all `input` elements by name/id/placeholder for common patterns:
  `first_name`, `last_name`, `email`, `phone`, `linkedin`, `github`, `location`
- Uploads resume to first PDF-accepting `input[type='file']`
- Submits via `button[type='submit']`

## Human-likeness features
- Random 30–120 ms delays between keystrokes
- Random 300–900 ms pauses between actions
- navigator.webdriver masked
- window.chrome spoofed
- Realistic user-agent (Chrome 122 on macOS)
- US locale + Pacific timezone

## Extending to a new platform

Add an applier class and register it:

```python
class _AshbyApplier:
    def apply(self, page, profile, resume_path, cover_letter_path, creds):
        # platform-specific logic
        ...
        return True  # or False on failure

_PLATFORM_APPLIERS["ashby"] = _AshbyApplier()
```

Add the URL patterns to `_PLATFORM_PATTERNS` in `auto_apply.py`.

## Sponsorship safety

The LinkedIn applier answers sponsorship dropdowns as `"Yes"` (candidate requires
sponsorship). For other platforms the generic form filler does not touch radio
buttons / dropdowns. For Dice, use the existing `submission/dice_playbook.md` flow
which uses the full `submitter.py` logic with explicit sponsorship-aware answers.
