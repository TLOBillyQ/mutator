# Lua mutation performance investigation

Measured on native Windows in the sibling monopoly project, at source commit
`38df17c`, using Lua 5.4, Busted 2.3.0, LuaCov 0.17.0 and cluacov 1.0.0.
The machine has a Ryzen 5 5500 (6 cores / 12 logical processors).

## Workload and historical reference

The stopped full mutation run processed 44 snapshot files in approximately
11 hours 39 minutes. The project has 570 Lua source files and 11,494 mutation
sites. Its mutation suite contains 550 spec files and 6,358 tests.

A single full-suite baseline took approximately 80–96 seconds. Twelve concurrent
full suites increased individual duration substantially. Native interpreter
startup was approximately 13 milliseconds; keeping an interpreter alive would
not address the dominant observed cost.

The acceptance source is `src/app/cosmetics/transaction_context.lua`, with
35 mutation sites: historically 31 killed, 3 survived and 1 uncovered. Its
historical snapshot interval was 1,272 seconds. This interval is a proxy for
per-file elapsed time, not an independently timed command. A 5× reduction
against that reference would require at most 254.4 seconds.

## Changes

- Lua mutant commands use Busted's built-in `--no-keep-going`. Busted discovers
  and compiles all spec files first, but after a failure it skips later files'
  execution and insulation. This preserves file order; reordering spec files
  could change shared-state behavior and is not part of this optimization.
- Previously surviving Lua mutants are scheduled first, with stable ordering for
  the remaining selection. History changes execution order only; every selected
  site still runs and receives a fresh outcome.
- Provably unchanged, fully killed differential selections skip coverage and
  retain their existing snapshots. Survivors and coverage gaps are not cached
  as killed.
- `--timings` reports coverage, baseline, worker preparation, worker checks,
  mutants, full-suite survivor confirmation, and mutation wall time. Concurrent
  phase totals sum worker time and may exceed elapsed time.
- Optional `--select-specs` collects line coverage and spec membership in one
  suite run. It preserves auto-insulation and falls back to the full suite for
  unknown or outside-spec attribution.
- Each exact subset is checked on pristine source. Every worker reserves and
  checks its first exact command before any worker mutates source; later new
  commands are checked before use. This removes an additional union check.
- Selected survivors are confirmed with the full suite. Source results are
  staged until all worker checks and executions complete successfully.
- The coverage collector consumes per-spec counter segments and suspends its
  hook during bookkeeping. Schema 2 uses integer spec IDs rather than repeating
  paths in every line membership. Old indexes trigger fresh collection.

## Correctness boundary

Spec selection is explicitly optional. Full-suite execution remains the default.

Passing pristine subsets does not establish equivalence for suites with shared
state. A real Busted regression demonstrates a mutant that survives the full
suite but fails its selected subset because an omitted spec changes a shared
value. Confirming selected survivors only addresses the opposite direction.
`--select-specs` therefore requires independent spec-file behavior; it is not
an unconditional optimization for monopoly. `--full-suite` explicitly selects
the default behavior. When changing between selected and full-suite scopes,
`--mutate-all` is required to recompute prior killed outcomes; differential
history otherwise retains them.

## Measurements so far

Before segment consumption and compact IDs, coverage collection took
297.109 seconds. The published spec index was 164,069,087 bytes; loading it for
explicit reuse took 2.469 seconds. Recorded shared overhead was 97.844 seconds,
and summed spec durations were 188.249 seconds. These values describe the first
spec collector, rather than the legacy aggregate-only coverage command.

The acceptance source has broad execution coverage: many mutation lines belong
to 285–288 specs, while other lines belong to 2–10 specs. A same-named test file
has a different identity; results must use the exact canonical source path.

The initial selection benchmark was stopped after more than twelve minutes,
before publication, after the shared-state counterexample invalidated its use
as a default-semantics acceptance run. No result parity or final speedup was
obtained. It exceeded the 254.4-second target before mutant execution completed.
Its partial results cannot be extrapolated to default full-suite mode or the
entire project. All benchmark processes were confirmed exited and monopoly's
git working tree remained clean.

The compact segmented collector completed the same 550-spec workload in
107.641 seconds, approximately 2.76× faster than the preceding collector. Its
published index is 13,686,319 bytes, approximately 91.7% smaller. Shared overhead
was 4.330 seconds and summed spec durations were 93.233 seconds. These are
coverage-collection improvements, not end-to-end mutation speedups. Lua process
RSS was still approximately 900 MB midway through this standalone run; the
serialized size reduction does not establish a reduction in peak memory.

The three-worker full-suite acceptance completed in 545.313 seconds with
explicit coverage reuse, approximately 2.33× faster than the historical
1,272-second snapshot interval. It did **not** meet the 5× goal. All 35 sites
matched history exactly: 31 killed, 3 survived and one uncovered. No mutant timed
out. Original source bytes and the historical snapshot remained unchanged.

| Phase | Measurement |
| --- | ---: |
| Coverage reuse | 2.016 s |
| Root full-suite baseline | 79.563 s |
| Worker preparation | 0.261 s |
| Three pristine worker checks | 336.532 s summed; 112.18 s mean |
| 34 mutant executions | 1,049.202 s summed; 30.86 s mean |
| Mutation wall time | 543.281 s |
| Total wall time | 545.313 s |

Concurrent phase totals sum execution time and must not be added as elapsed
wall time. This one-file acceptance pays a cold root baseline; multi-file runs
reuse that root baseline, while worker checks remain per file. Collection from
scratch adds a separate cost; the reuse result is not a first-run result.

The run showed both short and long failing paths: several killed mutants
completed in approximately two seconds, while others required approximately
106–108 seconds. All three historical survivors completed in approximately
116 seconds each. Fail-fast benefits depend on where failure occurs in the
existing suite order.

The reserved-first worker change requires separate real-project timing for
opted-in spec selection; the interrupted selection benchmark imported the
preceding version.

## Reproduction

Use the mutator checkout's virtual environment, with monopoly's pinned LuaRocks
module paths and native Lua interpreter on `PATH`. The public CLI equivalent of
the full-suite acceptance configuration is:

```powershell
python -m mutator --root <monopoly> --mutate-all --reuse-coverage --full-suite --max-workers 3 --timings src/app/cosmetics/transaction_context.lua
```

This public command publishes snapshots. The diagnostic acceptance harness,
`scripts/lua-perf/benchmark.py <monopoly> --reuse`, instead suppresses snapshot
publication, checks original source bytes, and compares every outcome with the
historical snapshot. `scripts/lua-perf/collect.py <monopoly>` measures fresh
collection. Both write reports to `target/lua-perf/`. Exit 3 from the public
command is a valid survivor result; it is not a reason to retry.

The raw reports behind the measurements above are in
[`docs/lua-performance/`](lua-performance/). They predate the harness's
`source`, `test_scope`, `max_workers` and `coverage_reuse` fields; the
full-suite report used three workers and coverage reuse.

Compare first-run coverage cost separately from explicit reuse. The historical
reference used twelve workers; the new full-suite acceptance uses three, so its
elapsed ratio describes the combined configuration and implementation change.
It does not isolate the effect of fail-fast or worker count.

## Remaining work

Tracked as qinyuanj/mutator#10 (worker reuse), #11 (collector memory) and
#12 (selected-spec timing).

A 5× end-to-end improvement has not been demonstrated. Preserve full-suite
semantics when investigating Busted's spec loading and auto-insulation costs;
changing isolation is a behavioral change. Larger worker counts are not assumed
to improve throughput on the measured six-core host.

The root baseline is cached across files within a run. Default Lua runs now
also retain workers across files, with fixed private source bindings and
restoration validation before reuse and publication. See
[`lua-worker-reuse.md`](lua-worker-reuse.md) for the validation boundary and the
multi-file acceptance command. The measurements above predate worker reuse and
remain a one-file reference.

Profile collector memory before changing its representation again. Integer
membership bitsets or immutable interned memberships may reduce overhead, but
must preserve exact memberships, a stable spec-ID table and distinct fallback
states. No memory savings from these possibilities have been measured.

## Validation

The full regression suite after these changes passed with 193 tests and one
skip. Collector tests exercised both pure LuaCov and native cluacov, including
preloaded functions, coroutines across segment resets, explicit and tick saves,
cache validation, malformed IDs, and aggregate coverage. Real Busted tests cover
both directions of shared-state divergence. The final wheel build succeeded;
its bundled Lua collector was checked for the schema-2 implementation.

No changes were made to monopoly's installed `.uml-viewer/mutator` clone or to
its source files. No uml-viewer source, policy or generated diagram changed.
