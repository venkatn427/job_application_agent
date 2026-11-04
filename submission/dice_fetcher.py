"""
Dice Fetcher Agent
------------------
Companion to the Playwright-MCP-driven submitter. The live page scraping is done
by the agent (Claude) via the Playwright MCP browser tools; this module provides
the deterministic logic around it:

  • build the Dice search URL (Data Engineer, posted today, easy-apply)
  • score how well a job title matches the candidate's target titles
  • a sponsorship heuristic (flag jobs whose text rules out visa sponsorship)
  • rank + take the top-N matches
  • write/read the jobs CSV that records the run

The agent calls `rank` after scraping listings into a JSON file, gets the ranked
CSV + the top-5 apply list, then drives Easy Apply for those URLs.

CLI:
    python -m submission.dice_fetcher url            # print the search URL
    python -m submission.dice_fetcher rank jobs.json # -> writes dice_jobs.csv, prints top 5
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Candidate's target titles (from profile / resume). Title match is scored against these.
TARGET_TITLES = [
    "senior data engineer",
    "data engineer",
    "staff data engineer",
    "lead data engineer",
    "data platform engineer",
    "big data engineer",
    "senior software engineer",
    "software engineer",
]

# Phrases in a posting that indicate sponsorship is NOT available -> exclude for an H-1B candidate.
NO_SPONSORSHIP_PATTERNS = [
    r"no\s+(visa\s+)?sponsorship",
    r"not\s+able\s+to\s+sponsor",
    r"unable\s+to\s+sponsor",
    r"without\s+(visa\s+)?sponsorship",
    r"will\s+not\s+sponsor",
    r"do(es)?\s+not\s+(provide|offer|support)\s+sponsorship",
    r"do\s+not\s+sponsor\s+visas?",
    r"does\s+not\s+sponsor\s+visas?",
    r"cannot\s+sponsor",
    r"no\s+h-?1b",
    r"h-?1b\s+(candidates?\s+)?(will\s+not|are\s+not|not)\s+(be\s+)?consider",
    r"us\s+citizen(ship)?\s+(required|only)",
    r"must\s+be\s+a?\s*(us|u\.s\.)\s+citizen",
    r"(us|u\.s\.)\s+citizens?\s+only",
    r"must\s+be\s+a?\s*citizen\s+of\s+the\s+(us|united\s+states)",
    r"green\s+card\s+(required|holders?\s+only)",
    r"requires?\s+(active\s+)?(security\s+)?clearance",
    r"unable\s+to\s+provide\s+sponsorship",
    r"not\s+sponsoring",
    r"w2\s+only.*no\s+c2c",  # often implies no sponsorship contracts
]

# Phrases that explicitly welcome sponsorship -> strong positive signal.
SPONSORSHIP_OK_PATTERNS = [
    r"sponsorship\s+(is\s+)?available",
    r"will\s+sponsor",
    r"h-?1b\s+(transfer|sponsorship|welcome)",
    r"open\s+to\s+sponsorship",
    r"visa\s+sponsorship\s+(available|provided|offered)",
]


def search_url(query: str = "Data Engineer", location: str = "", easy_apply: bool = True) -> str:
    """Dice job-search URL filtered to jobs posted in the last 24h (postedDate=ONE)."""
    from urllib.parse import urlencode
    params = {
        "q": query,
        "countryCode": "US",
        "radius": "30",
        "radiusUnit": "mi",
        "page": "1",
        "pageSize": "50",
        "filters.postedDate": "ONE",   # ONE = today / last 24 hours
        "language": "en",
    }
    if location:
        params["location"] = location
    if easy_apply:
        params["filters.easyApply"] = "true"
    return "https://www.dice.com/jobs?" + urlencode(params)


def title_match_score(title: str) -> int:
    """0-100 how well a job title matches the candidate's target titles."""
    t = (title or "").lower().strip()
    if not t:
        return 0  # empty title has no meaningful match
    best = 0
    for i, target in enumerate(TARGET_TITLES):
        if target == t:
            best = max(best, 100 - i)            # exact match, ranked by priority
        elif target in t or t in target:
            best = max(best, 85 - i)             # substring match
        else:
            # token overlap
            tt, tg = set(t.split()), set(target.split())
            if tt and tg:
                overlap = len(tt & tg) / len(tg)
                best = max(best, int(overlap * 70))
    # bonus for seniority alignment
    if "senior" in t or "sr" in t.split():
        best += 5
    return min(best, 100)


def sponsorship_status(description: str) -> str:
    """Return 'ok' | 'blocked' | 'unknown' based on the posting text."""
    d = (description or "").lower()
    if any(re.search(p, d) for p in NO_SPONSORSHIP_PATTERNS):
        return "blocked"
    if any(re.search(p, d) for p in SPONSORSHIP_OK_PATTERNS):
        return "ok"
    return "unknown"


def rank_jobs(
    jobs: list[dict[str, Any]],
    exclude_titles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Annotate jobs with title_score + sponsorship, drop blocked/excluded, sort best-first."""
    _exclude = [e.lower() for e in (exclude_titles or [])]
    ranked = []
    for j in jobs:
        title = j.get("title", "")
        tl = title.lower()
        # Drop if any exclude_titles word appears in this title
        if any(excl in tl for excl in _exclude):
            continue
        score = title_match_score(title)
        spon = sponsorship_status(j.get("description", ""))
        if spon == "blocked":
            continue                  # exclude roles that rule out sponsorship
        if score < 40:
            continue                  # exclude clear title mismatches
        ranked.append({**j, "title_score": score, "sponsorship": spon})
    # best title match first; among ties prefer explicit sponsorship 'ok' over 'unknown'
    ranked.sort(key=lambda j: (j["title_score"], j["sponsorship"] == "ok"), reverse=True)
    return ranked


CSV_FIELDS = ["rank", "title", "company", "location", "posted", "sponsorship",
              "title_score", "easy_apply", "job_url", "applied", "applied_at"]


def write_csv(ranked: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for i, j in enumerate(ranked, 1):
            w.writerow({
                "rank": i,
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "posted": j.get("posted", ""),
                "sponsorship": j.get("sponsorship", "unknown"),
                "title_score": j.get("title_score", ""),
                "easy_apply": j.get("easy_apply", ""),
                "job_url": j.get("job_url", ""),
                "applied": j.get("applied", "no"),
                "applied_at": j.get("applied_at", ""),
            })


def _cmd_rank(jobs_json: str) -> int:
    jobs = json.loads(Path(jobs_json).read_text())
    ranked = rank_jobs(jobs)
    out = HERE / f"dice_jobs_{date.today().isoformat()}.csv"
    write_csv(ranked, out)
    print(f"Wrote {len(ranked)} ranked jobs -> {out}\n")
    print("Top 5 to apply:")
    for i, j in enumerate(ranked[:5], 1):
        print(f"  {i}. [{j['title_score']:>3}] {j['title']} @ {j['company']} "
              f"({j['sponsorship']})  {j['job_url']}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__); return 0
    cmd, *rest = argv
    if cmd == "url":
        print(search_url()); return 0
    if cmd == "rank" and rest:
        return _cmd_rank(rest[0])
    print(f"unknown command: {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
