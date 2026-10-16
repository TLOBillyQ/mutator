# Lua selected-spec timing

Issue: https://github.com/qinyuanj/mutator/issues/12

## Acceptance result

The real-project selected-spec acceptance completed with exit 0 in 1,847.968
seconds. All 35 sites matched the historical snapshot: 31 killed mutants, three
surviving mutants and one uncovered mutant, with no outcome differences. The
three survivors completed full-suite confirmation. No mutant timed out, and the
harness's original-source byte check passed.

The run did not meet the 5x target of 254.4 seconds. Its historical elapsed ratio
was 0.6883x against the 1,272-second snapshot interval, meaning it took about
1.45 times that reference interval. This is a one-source measurement with the
provenance limitations below, not a whole-project speedup claim.

The [raw report](lua-performance/selected-specs-result.json) preserves every
original harness field. Its `observed_provenance_annotations` object contains
coordinator observations added after completion. The old harness emitted neither
revision fields nor an actual cache-hit field; those annotations are not original
harness measurements.

## Workload and command

The source is monopoly's `src/app/cosmetics/transaction_context.lua`, the same
35-site source used in the earlier full-suite acceptance. Coverage recorded
550 spec files. The command used three workers:

```powershell
python scripts/lua-perf/benchmark.py <monopoly> --select-specs --reuse
```

The harness suppressed snapshot publication, checked original source bytes after
execution, and compared covered mutation outcomes with the existing snapshot.
It checked pristine selected commands before mutation and confirmed selected
survivors with the full suite. Exact command checks added baseline cost when
mutation lines selected different spec sets.

Although the command requested coverage reuse, the run rebuilt coverage. The
coordinator observed 194.093 seconds for fresh collection. The original JSON's
`coverage_reuse: true` records the request only; actual reuse was false, as
recorded separately in the provenance annotations. Total elapsed time includes
this fresh collection.

## Provenance and measurement limitations

The run started at 13:32:29 on 2026-10-16 using mutator revision `adc4d34`, before
the later worker-reuse and collector-memory changes. The process measured that
imported implementation even though the checkout advanced during execution.
Later source edits do not describe the modules already loaded by this process.

Monopoly's HEAD moved from `1fb3255` to `cdb6053` during execution. The coordinator
reports that `src/`, `test/`, `.busted` and `.luacov` remained unchanged, while
`tools/dry` changed. The process was not stopped or restarted. These revisions
are observed provenance, not values captured by the original harness.

Unchanged source, tests and Lua configuration narrow the known change, but do
not prove that the changed tooling had no effect. Other activity also created
concurrent load during execution; that load was not controlled or quantified.
Treat this run as a completed measurement with changing project inputs and
concurrent load, rather than a controlled comparison at one project revision.

## Measurements

| Phase | Time | Executions |
| --- | ---: | ---: |
| Fresh coverage collection | 194.1 s | 1 |
| Root and exact-subset baselines | 397.6 s total | 13 |
| Worker preparation | 0.6 s | 1 |
| Pristine worker checks | 1,054.5 s summed | 21 |
| Mutant executions | 831.3 s summed | 34 |
| Full-suite survivor confirmation | 401.2 s summed | 3 |
| Mutation wall time | 1,653.8 s | 1 |
| Total wall time | 1,847.968 s | 1 |

Phase timings above retain the precision of the harness summary. Concurrent
phase totals sum execution time and may exceed wall time; they must not be added
to derive elapsed time. The historical 1,272-second snapshot interval is a proxy
for per-file elapsed time. Its run used twelve workers and this run used three,
so the elapsed ratio describes the combined configuration and implementation.
It cannot isolate the effect of spec-file selection.

## Interpretation boundary

Spec-file selection remains opt-in and requires independent spec files. Passing
pristine subsets and confirming selected survivors does not establish independence:
a shared-state spec can make a mutant fail its subset while surviving the full
suite. Exact historical outcome parity on this source does not establish
semantic equivalence for the project.

The earlier full-suite acceptance took 545.313 seconds with coverage reuse and
three workers. The selected-spec run took longer, but the coverage costs,
revisions, test scopes, changing inputs and concurrent load prevent attributing
that difference solely to selection. See [`lua-performance.md`](lua-performance.md)
for the full-suite reference and the shared-state counterexample.

Real-project measurements for [worker reuse](lua-worker-reuse.md) and
[collector memory](lua-collector-memory.md) are separate work. Their results
cannot be inferred from this selected-spec run.
