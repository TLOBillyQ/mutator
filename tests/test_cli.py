import subprocess
import sys
from pathlib import Path

import pytest

from mutator.edn import loads
from mutator.metrics import snapshot_path

from mutator.cli import parse_args, run
from mutator.runner import CommandResult


@pytest.mark.parametrize("failure", ["baseline", "worker", "publication"])
def test_later_source_failure_retains_completed_source_and_previous_results(
    tmp_path, monkeypatch, failure
):
    from mutator.runner import CommandRunner

    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
        (directory / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    def passing(self, command, cwd, timeout):
        return CommandResult(code=0, timed_out=False, seconds=0.01, output="")

    monkeypatch.setattr(CommandRunner, "run", passing)
    args = ["--no-coverage", "--mutate-all", "--root", str(tmp_path)]
    assert run(args) == 3
    a_snapshot = snapshot_path(tmp_path, "a.calc")
    b_snapshot = snapshot_path(tmp_path, "b.calc")
    previous_b = b_snapshot.read_bytes()
    replaced_a = []
    replace = Path.replace

    def replace_or_fail(self, target):
        if Path(target) == b_snapshot and failure == "publication":
            raise OSError("publication failed")
        result = replace(self, target)
        if Path(target) == a_snapshot:
            replaced_a.append(a_snapshot.read_bytes())
        return result

    def failing(self, command, cwd, timeout):
        is_b = cwd == tmp_path / "b" if timeout is None else cwd.name == "b"
        if is_b and failure == "baseline" and timeout is None:
            return CommandResult(code=1, timed_out=False, seconds=0.01, output="baseline failed")
        if is_b and failure == "worker" and timeout is not None:
            raise OSError("worker failed")
        return passing(self, command, cwd, timeout)

    monkeypatch.setattr(Path, "replace", replace_or_fail)
    monkeypatch.setattr(CommandRunner, "run", failing)
    if failure == "baseline":
        assert run(args) == 2  # A's survivors must not override B's baseline failure.
    else:
        with pytest.raises(OSError, match=f"{failure} failed"):
            run(args)
    assert len(replaced_a) == 1
    assert a_snapshot.read_bytes() == replaced_a[0]
    assert loads(a_snapshot.read_text(encoding="utf-8"))["forms"][0]["survived"] == 1
    assert b_snapshot.read_bytes() == previous_b


def test_mutant_launch_failure_preserves_snapshot_and_cleans_workers(tmp_path, monkeypatch):
    source = tmp_path / "calc.py"
    original = b"def add(a, b):\n    return a + b\n"
    source.write_bytes(original)
    command = f'"{sys.executable}" -c "raise SystemExit(0)"'
    args = ["--no-coverage", "--mutate-all", "--root", str(tmp_path),
            "--test-command", command, str(source)]
    assert run(args) == 3
    snapshot = snapshot_path(tmp_path, "calc")
    previous = snapshot.read_bytes()
    popen = subprocess.Popen

    def fail_worker_launch(command, **kwargs):
        if "mutation-workers" in Path(kwargs["cwd"]).parts:
            raise OSError("mutant process could not launch")
        return popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fail_worker_launch)
    with pytest.raises(OSError, match="mutant process could not launch"):
        run(args)
    assert snapshot.read_bytes() == previous
    assert source.read_bytes() == original
    assert not list((tmp_path / "target" / "mutation-workers").glob("run-*"))


def test_real_exit_127_keeps_its_status_and_kills_the_mutant(tmp_path, capsys):
    from mutator.runner import CommandRunner

    result = CommandRunner().run([sys.executable, "-c", "raise SystemExit(127)"], tmp_path, None)
    assert result.code == 127
    assert result.launch_error is None
    source = tmp_path / "calc.py"
    original = b"def add(a, b):\n    return a + b\n"
    source.write_bytes(original)
    command = (f'"{sys.executable}" -c "from calc import add; '
               'raise SystemExit(0 if add(2, 3) == 5 else 127)"')
    assert run(["--no-coverage", "--mutate-all", "--root", str(tmp_path),
                "--test-command", command, str(source)]) == 0
    assert "KILLED" in capsys.readouterr().out
    snapshot = loads(snapshot_path(tmp_path, "calc").read_text(encoding="utf-8"))
    assert snapshot["forms"][0]["killed"] == 1
    assert snapshot["forms"][0]["survived"] == 0
    assert source.read_bytes() == original
    assert not list((tmp_path / "target" / "mutation-workers").glob("run-*"))


def test_baseline_launch_failure_returns_2_and_preserves_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "calc.py"
    original = b"def add(a, b):\n    return a + b\n"
    source.write_bytes(original)
    command = f'"{sys.executable}" -c "raise SystemExit(0)"'
    args = ["--no-coverage", "--mutate-all", "--root", str(tmp_path),
            "--test-command", command, str(source)]
    assert run(args) == 3
    snapshot = snapshot_path(tmp_path, "calc")
    previous = snapshot.read_bytes()

    def fail_launch(command, **kwargs):
        raise OSError("baseline process could not launch")

    monkeypatch.setattr(subprocess, "Popen", fail_launch)
    assert run(args) == 2
    assert snapshot.read_bytes() == previous
    assert source.read_bytes() == original
    assert not list((tmp_path / "target" / "mutation-workers").glob("run-*"))


def test_lines_option_parses_positive_numbers():
    options = parse_args(["--lines", "2, 4", "src/demo.py"])
    assert options.lines == {2, 4}


def test_changed_selects_a_dirty_source_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "src/demo.py"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--changed", "--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "demo.py:2" in captured.out


def test_a_directory_argument_skips_a_named_test_file(tmp_path, capsys):
    src = tmp_path / "pkg" / "app.py"
    src.parent.mkdir()
    src.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    test = tmp_path / "pkg" / "test_app.py"
    test.write_text("def test_place():\n    assert True\n", encoding="utf-8")
    code = run(
        ["--scan", "--no-coverage", "--root", str(tmp_path), str(tmp_path / "pkg"), str(test)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Skipping test file" in captured.err
    assert "app.py:2" in captured.out


def test_help_and_conflicting_flags():
    help_options = parse_args(["--help"])
    assert help_options.exit_code == 0
    assert "uml-viewer" in help_options.message
    conflict = parse_args(["--mutate-all", "--since-last-run", "src/demo.py"])
    assert conflict.exit_code == 1
    assert run(["--help"]) == 0


def test_scan_lists_sites_without_writing_metrics(tmp_path, monkeypatch):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--root", str(tmp_path), "src/demo.py"])
    assert code == 0
    assert not (tmp_path / ".metrics").exists()


def test_real_python_mutants_update_the_snapshot(tmp_path, capsys):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text(
        "def add(a, b):\n"
        "    if a > 0:\n"
        "        return a + b\n"
        "    return 0\n",
        encoding="utf-8",
    )
    command = (
        f"{sys.executable} -c \"import sys; sys.path.insert(0, 'src'); "
        "from demo import add; raise SystemExit(0 if add(2, 3) == 5 else 1)\""
    )
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(tmp_path),
            "--test-command",
            command,
            "src/demo.py",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "SURVIVED" in captured.out
    assert "KILLED" in captured.out
    snapshot = tmp_path / ".metrics" / "mutate" / "demo.edn"
    assert snapshot.is_file()
    text = snapshot.read_text(encoding="utf-8")
    assert ":namespace \"demo\"" in text
    assert ":id \"defn/add\"" in text
    assert path.read_text(encoding="utf-8").startswith("def add")
