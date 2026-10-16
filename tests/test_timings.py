from concurrent.futures import ThreadPoolExecutor

from mutator import timings


def test_parallel_phase_totals_and_reset():
    timings.reset()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: timings.record("mutant", 0.25), range(40)))
    assert "timing mutant: 10.0s total, 40 run(s), 0.25s each" in timings.summary()
    timings.reset()
    assert timings.summary() == ""


def test_phase_records_failed_work():
    timings.reset()
    try:
        with timings.measure("worker-check"):
            raise RuntimeError("baseline failed")
    except RuntimeError:
        pass
    assert "worker-check:" in timings.summary()
    assert "1 run(s)" in timings.summary()
