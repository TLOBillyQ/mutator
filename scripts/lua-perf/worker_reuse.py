"""Compare per-file and reused workers on a small multi-file full-suite sample.

Usage: python scripts/lua-perf/worker_reuse.py ROOT SOURCE SOURCE [--max-workers 2]
Runs the first N covered mutation sites per source, where N is max-workers.
Never publishes snapshots. All suites use default full-suite semantics.
"""
import argparse
import hashlib
import json
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from mutator import timings
from mutator.coverage import covered_lines
from mutator.engine import mutate_file
from mutator.functions import sites_in_file
from mutator.runner import CommandRunner
from mutator.workers import WorkerPool, _worker_state

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
parser.add_argument("sources", nargs="+", help="at least two root-relative Lua paths")
parser.add_argument("--max-workers", type=int, default=2)
args = parser.parse_args()
assert len(args.sources) >= 2
assert args.max_workers > 0
root = args.root.resolve()
sources = [root / name for name in args.sources]
assert all(path.suffix == ".lua" for path in sources)
originals = {path: path.read_bytes() for path in sources}

def snapshots():
    directory = root / ".metrics" / "mutate"
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}

before = snapshots()
project_inputs = _worker_state(root, project=True)
selections = {}
for path in sources:
    coverage = covered_lines(root, path, "lua")
    assert coverage, f"No existing coverage for {path}"
    found = sites_in_file(originals[path].decode("utf-8"), path, root,
                          path.relative_to(root).as_posix())
    chosen = [site for site in found if site.line in coverage][:args.max_workers]
    assert chosen, f"No covered sites in {path}"
    selections[path.resolve()] = chosen
selected_ids = {site.mutation_id for sites in selections.values() for site in sites}
checkout = Path(__file__).resolve().parents[2]
report = {
    "project_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
    "mutator_commit": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
    "sources": args.sources, "test_scope": "full-suite", "max_workers": args.max_workers,
    "source_sha256": {str(path.relative_to(root)): hashlib.sha256(data).hexdigest()
                      for path, data in originals.items()},
    "selected_ids": {str(path.relative_to(root)): [site.mutation_id for site in sites]
                     for path, sites in selections.items()},
}
try:
    for mode in ("per-file", "reused"):
        assert _worker_state(root, project=True) == project_inputs, "project inputs changed"
        timings.reset()
        started = time.monotonic()
        results = {}
        baselines = {}
        pool_context = WorkerPool(root, sources) if mode == "reused" else nullcontext(None)
        with pool_context as pool, patch("mutator.engine.write_results", return_value=[]), patch(
                "mutator.engine._select", side_effect=lambda found, *rest:
                [site for site in found if site.mutation_id in selected_ids]):
            for path in sources:
                result = mutate_file(
                    path, root, runner=CommandRunner(verbose=True),
                    covered_lines=covered_lines(root, path, "lua"), ignore_coverage=False,
                    mutate_all=True, lines=None, test_command=None, timeout_factor=10,
                    mutation_warning=50, baselines=baselines, max_workers=args.max_workers,
                    worker_pool=pool if mode == "reused" else None,
                )
                assert not result.baseline_failed, result.baseline_message
                results[path.relative_to(root).as_posix()] = {
                    site.mutation_id: result.statuses[site.mutation_id]
                    for site in selections[path.resolve()]}
        report[mode] = {"elapsed": time.monotonic() - started,
                        "outcomes": results, "phases": timings.summary()}
        print(mode, json.dumps(report[mode], indent=2), flush=True)
    assert report["per-file"]["outcomes"] == report["reused"]["outcomes"]
    report["parity"] = True
finally:
    assert all(path.read_bytes() == data for path, data in originals.items()), "source bytes changed"
    assert snapshots() == before, "snapshots changed"
    assert _worker_state(root, project=True) == project_inputs, "project inputs changed"
    end_commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    assert end_commit == report["project_commit"], "project commit changed"
    report["project_inputs_unchanged"] = True
    report["source_bytes_unchanged"] = True
    report["snapshots_unchanged"] = True
    artifacts = Path(__file__).resolve().parents[2] / "target" / "lua-perf"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "worker-reuse-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
