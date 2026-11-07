"""Prepare local job JSON through the separate application agents."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from agents.application_team import ApplicationTeam


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['offline', 'ai'], default='offline')
    parser.add_argument('--jobs', type=Path, required=True, help='Local JSON array of jobs')
    parser.add_argument('--profile', type=Path, default=ROOT / 'submission/profile.json')
    parser.add_argument('--cached-profile', type=Path, default=ROOT / 'data/candidate_profile.json')
    parser.add_argument('--resume', type=Path, default=ROOT / 'data/master_resume/resume.pdf')
    parser.add_argument('--threshold', type=int, default=70)
    parser.add_argument('--submit', action='store_true', help='Explicitly approve online submission of recommended jobs; works in either mode')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/application-team.json')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    try:
        jobs = json.loads(args.jobs.read_text())
        if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
            raise ValueError('Jobs file must contain a JSON array of objects')
        saved = json.loads(args.profile.read_text())
        cached = json.loads(args.cached_profile.read_text()) if args.cached_profile.exists() else None
        report = ApplicationTeam(args.mode, args.threshold).run(jobs, saved, args.resume, cached,
                    submit=args.submit, output_dir=args.output.parent / 'application-team')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        failed = sum(item.get('status') == 'failed' or item.get('submission', {}).get('status') in ('failed', 'blocked') for item in report['applications'])
        submitted = sum(item.get('submission', {}).get('submitted', False) for item in report['applications'])
        print(f'{args.mode}: {len(jobs) - failed} completed, {submitted} submitted, {failed} failed/blocked. Report: {args.output}')
        return 1 if failed else 0
    except (ValueError, OSError) as exc:
        print(f'Preparation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
