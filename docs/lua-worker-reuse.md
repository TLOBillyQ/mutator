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
