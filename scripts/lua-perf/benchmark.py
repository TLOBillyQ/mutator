"""Single-file Lua mutation acceptance; never publishes snapshots or edits source.

Usage:
  python scripts/lua-perf/benchmark.py <project-root> [--source PATH]
      [--full-suite | --select-specs] [--reuse] [--max-workers N]
      [--historical-seconds S]

Outcomes are compared with the source's existing snapshot. The report is written
to target/lua-perf/ in this checkout. Exit 0 when outcomes match, 1 when they
differ, 2 when the baseline fails.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from mutator import timings
from mutator.coverage import covered_lines
from mutator.engine import mutate_file
from mutator.lua_coverage import collect, load
from mutator.metrics import load_history
from mutator.runner import CommandRunner

parser = argparse.ArgumentParser()
parser.add_argument('root', type=Path)
parser.add_argument('--source', default='src/app/cosmetics/transaction_context.lua')
scope = parser.add_mutually_exclusive_group()
scope.add_argument('--full-suite', action='store_true', default=True)
scope.add_argument('--select-specs', dest='full_suite', action='store_false')
parser.add_argument('--reuse', action='store_true', help='reuse a valid spec index')
parser.add_argument('--max-workers', type=int, default=3)
parser.add_argument('--historical-seconds', type=float, default=1272,
                    help='reference elapsed time; a snapshot interval is only a proxy')
args = parser.parse_args()


def revision(directory):
    result = subprocess.run(
        ['git', '-C', str(directory), 'rev-parse', 'HEAD'],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


checkout = Path(__file__).resolve().parents[2]
root = args.root.resolve()
source = root / args.source
revisions = {'mutator_commit': revision(checkout), 'project_commit': revision(root)}
artifacts = checkout / 'target' / 'lua-perf'
artifacts.mkdir(parents=True, exist_ok=True)
original = source.read_bytes()
key = source.relative_to(root).as_posix()
previous_outcomes = dict(load_history(root, key).outcomes)
started = time.monotonic()
timings.reset()
with timings.measure('coverage'):
    cached = load(root, source) if args.reuse else None
    index = cached if cached is not None else collect(root, source, verbose=False)
print('COLLECTED', len(index.specs), 'specs', f'{time.monotonic()-started:.3f}s', flush=True)
memberships = {}
for line in index.lines.get(os.path.normcase(str(source)), {}):
    specs = index.select(source, line)
    memberships[str(line)] = None if specs is None else len(specs)
print('MEMBERSHIP_COUNTS', json.dumps(memberships), flush=True)
# Acceptance must not overwrite the historical reference snapshot.
with timings.measure('mutation-run'), patch('mutator.engine.write_results', return_value=[]):
    result = mutate_file(
        source, root, runner=CommandRunner(verbose=True),
        covered_lines=covered_lines(root, source, 'lua'), ignore_coverage=False,
        mutate_all=True, lines=None, test_command=None, timeout_factor=10,
        mutation_warning=50, baselines={}, max_workers=args.max_workers,
        lua_coverage=None if args.full_suite else index,
    )
elapsed = time.monotonic() - started
assert source.read_bytes() == original, 'original source changed'
print(timings.summary(), flush=True)
if result.baseline_failed:
    print(result.baseline_message, flush=True)
    raise SystemExit(2)
actual = {k: v for k, v in result.statuses.items() if v != 'uncovered'}
expected = {k: v for k, v in previous_outcomes.items() if v != 'uncovered'}
changes = {k: [expected.get(k), actual.get(k)] for k in expected.keys() | actual.keys()
           if expected.get(k) != actual.get(k)}
report = {
    **revisions,
    'source': key,
    'elapsed': elapsed,
    'counts': {status: list(result.statuses.values()).count(status)
               for status in ('killed', 'survived', 'uncovered')},
    'matches_previous': not changes,
    'changes': changes,
    'phases': timings.summary(),
    'spec_count': len(index.specs),
    'test_scope': 'full-suite' if args.full_suite else 'selected-specs',
    'max_workers': args.max_workers,
    'coverage_reuse': args.reuse,
    'coverage_reused': cached is not None,
    'historical_file_seconds': args.historical_seconds,
    'speedup_vs_historical_file': args.historical_seconds / elapsed,
}
report_name = ('full-suite' if args.full_suite else 'selected-specs') + '-result.json'
(artifacts / report_name).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2), flush=True)
raise SystemExit(0 if not changes else 1)
