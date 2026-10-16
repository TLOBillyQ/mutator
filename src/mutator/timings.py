"""Wall-clock totals for each phase of a run, printed by --timings."""

from __future__ import annotations

import time
from contextlib import contextmanager
from threading import Lock

_lock = Lock()
_totals: dict[str, list[float]] = {}


def record(phase: str, seconds: float) -> None:
    with _lock:
        entry = _totals.setdefault(phase, [0.0, 0])
        entry[0] += seconds
        entry[1] += 1


@contextmanager
def measure(phase: str):
    started = time.monotonic()
    try:
        yield
    finally:
        record(phase, time.monotonic() - started)


def reset() -> None:
    with _lock:
        _totals.clear()


def summary() -> str:
    """One line per phase, in the order the phases first ran.

    Worker phases run in parallel, so their totals add up thread time and can
    exceed the wall clock of the run.
    """

    with _lock:
        items = list(_totals.items())
    lines = []
    for phase, (seconds, count) in items:
        average = seconds / count if count else 0.0
        lines.append(f"timing {phase}: {seconds:.1f}s total, {count} run(s), {average:.2f}s each\n")
    return "".join(lines)
