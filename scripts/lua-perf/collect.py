"""Fresh Lua spec-coverage collection with comparable acceptance output.

Usage:
  python scripts/lua-perf/collect.py <project-root> [--source PATH]

Compares memberships with any previously published index for the same task and
command. The report is written to target/lua-perf/ in this checkout.
"""
import argparse
import json
import os
import time
from pathlib import Path

from mutator.lua_coverage import collect, load

parser = argparse.ArgumentParser()
parser.add_argument('root', type=Path)
parser.add_argument('--source', default='src/app/cosmetics/transaction_context.lua')
args = parser.parse_args()

root = args.root.resolve()
source = root / args.source
artifacts = Path(__file__).resolve().parents[2] / 'target' / 'lua-perf'
artifacts.mkdir(parents=True, exist_ok=True)
previous = load(root, source)
started = time.monotonic()
index = collect(root, source)
elapsed = time.monotonic() - started
artifact = index.directory / 'target/coverage/lua/spec-coverage.json'
report = {
    'source': source.relative_to(root).as_posix(),
    'elapsed': elapsed,
    'specs': len(index.specs),
    'overhead': index.overhead,
    'spec_seconds': sum(index.specs.values()),
    'artifact_bytes': artifact.stat().st_size,
    'source_memberships': {
        str(line): None if index.select(source, line) is None else len(index.select(source, line))
        for line in sorted(index.lines.get(os.path.normcase(str(source)), {}))
    },
}
if previous is not None:
    report['same_memberships'] = previous.lines == index.lines
    report['same_fallback'] = previous.fallback == index.fallback
(artifacts / 'collection-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2), flush=True)
