"""Inspect routing offline, run regressions, or opt into live synthetic evaluations."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from pipeline.llm import load_config, resolve, ModelClient, ROOT

TASKS = ('profile', 'matching', 'resume', 'cover_letter', 'fields', 'assistant')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Run all offline regression tests')
    parser.add_argument('--live', action='store_true', help='Call configured providers using synthetic facts; may incur API costs')
    parser.add_argument('--output', type=Path, help='Optional JSON report')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    if args.test:
        return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT)
    config = load_config()
    rows = []
    for task in TASKS:
        settings = resolve(task, config=config)
        row = {'task': task, 'provider': settings.provider, 'model': settings.model,
               'configured': bool(settings.api_key), 'status': 'not_run'}
        if args.live:
            if not settings.api_key:
                row['status'] = 'missing_key'
            else:
                try:
                    answer = ModelClient(task, config=config).messages.create(
                        system='Return only the requested fact, no explanation.', max_tokens=512,
                        messages=[{'role': 'user', 'content': 'Synthetic candidate: Python experience is 3 years. Return the number of years as a single integer.'}],
                    ).content[0].text.strip()
                    row['status'] = 'passed' if answer == '3' else 'failed'
                except Exception:
                    row['status'] = 'provider_error'
        rows.append(row)
    report = json.dumps({'live': args.live, 'routes': rows}, indent=2)
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + '\n')
    return int(args.live and any(row['status'] != 'passed' for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
