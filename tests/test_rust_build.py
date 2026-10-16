"""A cold cargo build must not spend the mutant timeout (issue #1)."""

import threading

from mutator.engine import mutate_file
from mutator.runner import CommandResult, build_command

SOURCE = (
    "pub fn is_small(x: i64) -> bool {\n    x < 10\n}\n\n"
    "pub fn twice(x: i64) -> i64 {\n    x + x\n}\n"
)


class CargoRunner:
    """cargo test compiles first; a tree it has not built takes a cold build."""

    def __init__(self):
        self.verbose = False
        self.calls = []
        self._built = set()
        self._lock = threading.Lock()

    def run(self, command, cwd, timeout):
        text = (cwd / "src" / "lib.rs").read_text(encoding="utf-8")
        with self._lock:
            self.calls.append((command, timeout))
            cold = (str(cwd), text) not in self._built
            self._built.add((str(cwd), text))
        seconds = 30.0 if cold else 0.2
        if timeout is not None and seconds > timeout:
            return CommandResult(code=124, timed_out=True, seconds=timeout, output="")
        if "--no-run" in command:
            return CommandResult(code=0, timed_out=False, seconds=seconds, output="")
        ok = ("x < 10" in text or "x <= 10" in text) and "x + x" in text
        return CommandResult(code=0 if ok else 101, timed_out=False, seconds=seconds, output="")


def test_cargo_test_builds_before_the_timed_run():
    assert build_command(["cargo", "test"]) == ["cargo", "test", "--no-run"]
    assert build_command(["npm", "test"]) is None
    assert build_command("cargo test") is None


def test_parallel_cold_workers_report_a_surviving_mutant_as_survived(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    path = tmp_path / "src" / "lib.rs"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    runner = CargoRunner()
    # The user already built the project, so its baseline is warm.
    runner._built.add((str(tmp_path.resolve()), SOURCE))
    result = mutate_file(
        path,
        tmp_path,
        runner=runner,
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command=None,
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
        max_workers=4,
    )
    assert result.baseline_failed is False, result.baseline_message
    outcomes = {
        (site.original, site.mutant): result.statuses[site.mutation_id]
        for site in result.sites
    }
    assert outcomes[("<", "<=")] == "survived"
    assert "killed" in outcomes.values()
    assert all(timeout is None for command, timeout in runner.calls if "--no-run" in command)
