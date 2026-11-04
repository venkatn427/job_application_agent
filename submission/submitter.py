"""
Dice Submission Helper
----------------------
Support code for the Playwright-MCP-driven Dice Easy Apply flow.

This module does NOT drive the browser itself — the agent (Claude) does that via
the Playwright MCP tools, following submission/dice_playbook.md. This helper:

  • loads + validates profile.json and settings.yaml
  • resolves screening-question answers from the profile
  • picks the next batch of jobs to apply to
  • appends structured records to submissions.json (the audit log)

CLI:
    python -m submission.submitter check          # validate config, print summary
    python -m submission.submitter answer "<question text>"
    python -m submission.submitter log '<json record>'
    python -m submission.submitter history         # show past submissions
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROFILE_PATH = HERE / "profile.json"
SETTINGS_PATH = HERE / "settings.yaml"


# ── Loading ──────────────────────────────────────────────────────────────────
def load_profile() -> dict[str, Any]:
    data = json.loads(PROFILE_PATH.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_settings() -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml not installed. `pip install pyyaml`")
    return yaml.safe_load(SETTINGS_PATH.read_text())


# ── Validation ───────────────────────────────────────────────────────────────
REQUIRED_PERSONAL = ["first_name", "last_name", "email", "phone", "city", "state"]


def validate(profile: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means ready to run."""
    problems: list[str] = []
    personal = profile.get("personal", {})
    for field in REQUIRED_PERSONAL:
        if not str(personal.get(field, "")).strip():
            problems.append(f"profile.personal.{field} is empty")

    resume = profile.get("documents", {}).get("resume_path", "")
    resume_abs = (ROOT / resume) if resume and not Path(resume).is_absolute() else Path(resume)
    if not resume or not resume_abs.exists():
        problems.append(f"resume not found at '{resume}' (resolved: {resume_abs})")

    if settings.get("dry_run") and settings.get("pause_before_submit") is False:
        problems.append("note: dry_run=true means no application will be sent")
    return problems


# ── Screening answer resolution ──────────────────────────────────────────────
def resolve_reference(value: str, profile: dict[str, Any]) -> str:
    """Resolve a 'use professional.years_experience' style pointer into a value."""
    if isinstance(value, str) and value.startswith("use "):
        path = value[len("use "):].strip()
        node: Any = profile
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return "" if node is None else str(node)
    return value


def _is_sponsorship_question(q: str) -> bool:
    """True when the question is specifically asking about sponsorship need."""
    patterns = [
        "require sponsorship",
        "require visa",
        "require work authorization",
        "need sponsorship",
        "need visa sponsorship",
        "will you now or in the future require",
        "currently require",
        "do you require",
        "h-1b",
        "h1b",
        "work visa",
        "visa transfer",
    ]
    return any(p in q for p in patterns)


def _is_work_authorization_question(q: str) -> bool:
    """True when the question asks if the candidate CAN legally work (not whether sponsorship is needed)."""
    patterns = [
        "authorized to work",
        "legally authorized",
        "eligible to work",
        "legally eligible",
        "right to work",
        "work in the us",
        "work in the united states",
    ]
    # Must NOT also be a sponsorship question (those are handled separately)
    return any(p in q for p in patterns) and not _is_sponsorship_question(q)


def _skill_yoe_question(q: str) -> str | None:
    """Return skill name if question asks years of experience WITH a specific technology/skill."""
    import re as _re
    # "years of experience with X", "how many years X experience"
    m = _re.search(
        r"(?:years?\s+(?:of\s+)?(?:experience|exp)(?:\s+with\s+|\s+in\s+)(.+)|"
        r"(?:experience|exp)\s+with\s+(.+)\s*\?)",
        q,
        _re.IGNORECASE,
    )
    if m:
        skill = (m.group(1) or m.group(2) or "").strip().rstrip("?").strip()
        return skill if skill else None
    return None


def answer_for(question: str, profile: dict[str, Any]) -> str | None:
    """Best-effort answer for a screening question using screening_defaults.

    Priority:
      1. Skill-specific YOE questions → check professional.skills_experience map
      2. Sponsorship questions → work_authorization.require_sponsorship
      3. Work authorization questions → work_authorization.authorized_to_work_us
      4. screening_defaults substring match (longest key wins to avoid partial matches)

    Returns None when no confident match — caller (agent) should ask the user.
    """
    q = question.lower().strip()
    auth = profile.get("work_authorization", {})

    # 1. Skill-specific YOE
    skill_name = _skill_yoe_question(q)
    if skill_name:
        skills_exp = profile.get("professional", {}).get("skills_experience", {})
        for sk, yrs in skills_exp.items():
            if sk.lower() in skill_name or skill_name in sk.lower():
                return str(yrs)
        # No specific entry → return None so the agent asks rather than giving total YOE
        return None

    # 2. Sponsorship need
    if _is_sponsorship_question(q):
        return "Yes" if auth.get("require_sponsorship") else "No"

    # 3. Work authorization (not sponsorship)
    if _is_work_authorization_question(q):
        return "Yes" if auth.get("authorized_to_work_us") else "No"

    # 4. screening_defaults — match by longest key to avoid "years of experience" matching
    #    more specific keys like "years of experience with python"
    defaults = {k: v for k, v in profile.get("screening_defaults", {}).items()
                if not k.startswith("_")}
    best_key, best_val = "", None
    for key, val in defaults.items():
        kl = key.lower()
        if kl in q and len(kl) > len(best_key):
            best_key, best_val = kl, val
    if best_val is not None:
        return resolve_reference(best_val, profile)

    return None


# ── Submission log ───────────────────────────────────────────────────────────
def log_path(settings: dict[str, Any]) -> Path:
    rel = settings.get("submissions_log", "submission/submissions.json")
    return ROOT / rel


def append_log(record: dict[str, Any], settings: dict[str, Any]) -> None:
    path = log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(path.read_text()) if path.exists() else []
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    history.append(record)
    path.write_text(json.dumps(history, indent=2))


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cmd_check() -> int:
    profile = load_profile()
    settings = load_settings()
    problems = validate(profile, settings)
    p = profile.get("personal", {})
    print(f"Candidate : {p.get('first_name','?')} {p.get('last_name','?')} <{p.get('email','?')}>")
    print(f"Platform  : {settings.get('platform')}  |  source: {settings.get('job_source')}")
    print(f"Safety    : dry_run={settings.get('dry_run')} "
          f"pause_before_submit={settings.get('pause_before_submit')} "
          f"cap={settings.get('max_applications_per_run')}")
    if problems:
        print("\nNOT READY — fix these first:")
        for pr in problems:
            print(f"  • {pr}")
        return 1
    print("\nReady to run. ✅")
    return 0


def _cmd_answer(question: str) -> int:
    profile = load_profile()
    ans = answer_for(question, profile)
    print(ans if ans is not None else "(no confident match — agent should ask the user)")
    return 0


def _cmd_log(raw: str) -> int:
    record = json.loads(raw)
    append_log(record, load_settings())
    print("logged ✓")
    return 0


def _cmd_history() -> int:
    settings = load_settings()
    path = log_path(settings)
    if not path.exists():
        print("(no submissions yet)")
        return 0
    for r in json.loads(path.read_text()):
        print(f"{r.get('timestamp','?')}  {r.get('status','?'):20s}  "
              f"{r.get('title','?')} @ {r.get('company','?')}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd, *rest = argv
    if cmd == "check":
        return _cmd_check()
    if cmd == "answer" and rest:
        return _cmd_answer(rest[0])
    if cmd == "log" and rest:
        return _cmd_log(rest[0])
    if cmd == "history":
        return _cmd_history()
    print(f"unknown command: {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
