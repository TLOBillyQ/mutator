# Lua collector memory

Issue: https://github.com/qinyuanj/mutator/issues/11

The collector previously retained a Lua hash table of integer spec IDs for each
covered line. At shutdown it expanded every membership into an array, then
encoded the complete publication into one string. Serialized schema-2 size did
not measure these allocations or the project's own retained heap.

The collector now stores immutable byte strings with one bit per spec ID.
A segment-local transition cache shares each updated membership between lines
with the same previous membership. Discarding this cache after each segment
allows obsolete histories to be collected. Repeated saves within a spec and
repeated execution of a spec preserve IDs and do not duplicate memberships.

Publication streams the line map and caches encoded membership arrays within
each source file. It preserves schema 2, sorted integer memberships, the spec-ID
table, and separate outside-spec fallback entries. Unknown lines remain absent.
The aggregate LuaCov counters and hook handling are unchanged.

## Synthetic isolation

A standalone Lua fixture replaces Busted and LuaCov counters with deterministic
segments, isolating membership storage and publication from the real project's
heap. On Windows Lua 5.4, 4,096 source lines and 160 spec IDs produced:

| Collector | Heap before publication (KiB) | Sampled heap peak (KiB) |
| --- | ---: | ---: |
| Baseline `adc4d34` | 11,245.0 | 25,017.8 |
| Packed and streamed | 729.8 | 1,163.5 |

The baseline and candidate publications matched exactly for version, completion,
unsafe status, spec IDs, line memberships and outside-spec fallback. Spec elapsed
times were excluded. These are sampled Lua heap measurements without forced GC,
not process RSS or proof of real-project peak memory reduction.

The fixture also tests IDs through 79, sparse memberships with gaps larger than
a byte, repeated spec IDs and saves, and unknown versus outside-spec attribution.
Collector integration tests run with both pure LuaCov and native cluacov.

## Real-project harness

`scripts/lua-perf/collector_memory.py` runs the baseline collector from a fixed
Git commit followed by the current collector. It records process peak working
set on Windows (sampled every 20 ms and queried again after process exit before
closing its handle), and Lua heap at segment boundaries, JSON
encode returns and publication writes. On Linux the RSS field is explicitly a
sampled lower bound; unsupported platforms fail before launching a child, and
zero RSS measurements fail rather than being reported as success. The heap sampling does not force GC and
may miss allocations between sampling points; OS working set includes the
project, Lua runtime and native modules.

Both runs use private helper, stats, configuration, JSON and log files under
`target/lua-perf/collector-memory/`, leaving the project's coverage cache alone.
The harness verifies source bytes before and after, probes native cluacov when
requested, and compares all raw memberships, fallback entries and spec IDs.
Run it only after other scheduled memory-heavy benchmarks have finished.
