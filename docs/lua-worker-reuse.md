# Lua workers across source files

Default Lua mutation runs retain workers across files for the lifetime of one
CLI invocation. Custom test commands and other languages keep per-file workers.
The root baseline and each worker's pristine check remain separate checks.

Every selected Lua source is copied into each worker before its first pristine
check. Switching files changes which private copy receives a mutant; it does
not change the overlay layout. Workers added for a larger later selection get
the same bindings and their own pristine checks. A check is cached only for the
same worker, exact command and mapped working directory.

Before executing a mutant, after restoring it, and before releasing a source's
outcomes, the pool verifies private file contents, directory entries and link
targets. Between sources it also checks project input contents, excluding the
existing build/cache/dependency exclusions and agent directories. A changed,
removed or replaced source binding, an unexpected private file, a changed
project input, or a failed command invalidates the pool. The engine stages all
outcomes for the current source and keeps its previous snapshot on failure.
Previously completed sources retain their successful snapshots.

This validation deliberately rejects generated private files and modified
private configs rather than guessing whether they affect later executions.
Projects whose tests leave such state must clean it before the command exits.
Project directory links outside excluded dependencies are rejected because the
input inventory does not follow arbitrary directory links. Linked files and
original project inputs must remain stable during the run. Excluded external
dependencies and the inherited process environment are assumed stable, as with
the existing baseline cache. Worker directories are removed when the run ends
or the pool fails.

## Acceptance command

`scripts/lua-perf/worker_reuse.py` compares per-file workers with reused workers
using a small multi-file sample and full-suite execution. It selects the first
`--max-workers` covered mutation sites in each named source, shares the root
baseline within each mode, and records exact mutation IDs and outcomes.
Snapshot publication is suppressed. Source bytes and existing mutation
snapshots are checked after execution, including the failure path.

```powershell
python scripts/lua-perf/worker_reuse.py <monopoly> src/app/cosmetics/transaction_context.lua src/app/cosmetics/transaction_result.lua --max-workers 2
```

Use the native Lua interpreter and the project's pinned LuaRocks module paths,
as described in `lua-performance.md`. The report is written to
`target/lua-perf/worker-reuse-result.json`. Its elapsed times describe only the
selected sites and exclude coverage collection; they cannot establish a
whole-project speedup.

## Measured acceptance

The native Windows comparison ran against an isolated monopoly worktree pinned
to `cdb605398d0ce73862959bc1376503ab01e14925`, using mutator
`5663c9e1d5566d54f0479138a8fe921e6635f67a`. Its private `.toolcache` supplied
LuaRocks dependencies; `LUA_PATH` and `LUA_CPATH` contained only fixture paths,
without default-path expansion. Normal coverage collection completed before the
comparison and is excluded from these timings.

The sample used the two sources above, with the first two covered mutation
sites from each. All four mutants were killed in both modes. Exact mutation
IDs, source hashes, outcomes and phase totals are recorded in
[`worker-reuse-result.json`](lua-performance/worker-reuse-result.json).
Source bytes, mutation snapshots, ordinary project inputs and HEAD remained
unchanged across both modes. Snapshot publication was suppressed.

| Measurement | Per-file workers | Reused workers |
| --- | ---: | ---: |
| Elapsed time | 382.109 s | 290.187 s |
| Pristine worker checks | 4 | 2 |
| Summed worker-check time | 560.1 s | 341.6 s |
| Summed worker-check time per source | 280.05 s | 170.8 s |
| Root baseline | 94.67 s | 109.30 s |
| Mean pristine worker check | 140.04 s | 170.82 s |

Summed worker-check time per source decreased by **39.0%**, and elapsed time
decreased by **24.1%** for this sample. Reuse eliminated the second source's
pristine worker executions. Phase totals sum concurrent worker time and must
not be added as elapsed time.

This was one sequential comparison, per-file mode first, under uncontrolled
concurrent machine load. Root baseline and mean worker duration were higher in
the reused mode. These measurements establish the observed reduction for this
small sample; they do not isolate a general throughput improvement or establish
a whole-project speedup. The sample contains no surviving mutants. Separate
regressions cover real Lua survivor/kill parity, worker corruption, source
binding removal, command/cwd identity, pool growth, cleanup, and retention of
previous snapshots on worker failure.
