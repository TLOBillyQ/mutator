"""Worker acceptance tests force OS privilege denial, independent of host rights."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mutator.workers import create_workers, delete_tree, run_mutants
from mutator.model import Site
from mutator.runner import CommandRunner


def deny_links(monkeypatch, after=0):
    real = Path.symlink_to
    calls = 0

    def denied(path, target, target_is_directory=False):
        nonlocal calls
        calls += 1
        if calls <= after:
            return real(path, target, target_is_directory=target_is_directory)
        error = OSError("symlink privilege missing")
        error.winerror = 1314
        raise error

    monkeypatch.setattr(Path, "symlink_to", denied)


def project(root):
    (root / "src").mkdir()
    (root / "src" / "value.py").write_bytes(b"value = 1\n")
    (root / "assets").mkdir()
    (root / "assets" / "data.txt").write_text("original")
    (root / "pyproject.toml").write_text("[tool.demo]\n")
    return root / "src" / "value.py"


def test_privilege_denial_builds_private_worker(monkeypatch, tmp_path):
    source = project(tmp_path)
    deny_links(monkeypatch)
    base = tmp_path / "target" / "mutation-workers" / "run-test"
    worker, = create_workers(base, tmp_path, "src/value.py", source.read_bytes(), 1)
    try:
        (worker / "assets" / "data.txt").write_text("worker")
        (worker / "pyproject.toml").write_text("worker config")
        assert (tmp_path / "assets" / "data.txt").read_text() == "original"
        assert (tmp_path / "pyproject.toml").read_text() == "[tool.demo]\n"
        assert (worker / "src" / "value.py").read_bytes() == b"value = 1\n"
    finally:
        delete_tree(base)
    assert source.read_bytes() == b"value = 1\n"


@pytest.mark.parametrize("code", [5, 32, 112])
def test_unrelated_filesystem_errors_are_visible_and_cleaned(monkeypatch, tmp_path, code):
    source = project(tmp_path)
    error = OSError("unrelated filesystem failure")
    error.winerror = code

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "symlink_to", fail)
    base = tmp_path / "workers"
    with pytest.raises(OSError) as raised:
        create_workers(base, tmp_path, "src/value.py", source.read_bytes(), 2)
    assert raised.value is error
    assert not base.exists()
    assert source.read_bytes() == b"value = 1\n"


def test_forced_fallback_executes_concurrent_mutants(monkeypatch, tmp_path):
    source = project(tmp_path)
    original = source.read_bytes()
    rendezvous = tmp_path / "rendezvous"
    rendezvous.mkdir()
    # The subprocess barrier requires two workers to be alive simultaneously.
    (tmp_path / "check.py").write_text('''
import sys, time
from pathlib import Path
from src.value import value
assert sys.argv[3:] == ["argument with spaces", "a&b", "literal=3"]
assert value in (2, 3)
original = Path(sys.argv[1])
assert original.read_bytes() == b"value = 1\\n"
barrier = Path(sys.argv[2])
(barrier / str(value)).write_text("ready")
deadline = time.monotonic() + 8
while len(list(barrier.iterdir())) < 2:
    assert time.monotonic() < deadline, "workers did not run concurrently"
    assert original.read_bytes() == b"value = 1\\n"
    time.sleep(.01)
assert Path("src/value.py").read_text() == f"value = {value}\\n"
assert original.read_bytes() == b"value = 1\\n"
sys.exit(0 if value == 2 else 17)
''')
    deny_links(monkeypatch)
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    sites = [Site("src/value.py", "value", "defn/value", "value", 1,
                  8, 9, "1", str(value), "number", str(value)) for value in (2, 3)]
    outcomes = {}
    run_mutants(tmp_path, source, original, sites, 2, CommandRunner(),
                [sys.executable, "check.py", str(source), str(rendezvous),
                 "argument with spaces", "a&b", "literal=3"],
                tmp_path, 12, "src/value.py", outcomes)
    assert outcomes == {"2": "survived", "3": "killed"}
    assert {p.name for p in rendezvous.iterdir()} == {"2", "3"}
    assert source.read_bytes() == original
    assert not list((tmp_path / "target" / "mutation-workers").iterdir())


def test_recursive_copy_excludes_caches_and_destination(monkeypatch, tmp_path):
    source = project(tmp_path)
    from mutator.workers import SKIP_LINK
    for name in SKIP_LINK:
        cache = tmp_path / "assets" / name
        cache.mkdir()
        (cache / "sentinel").write_text("cache")
    deny_links(monkeypatch)
    base = tmp_path / "assets" / "custom-workers"
    worker, = create_workers(base, tmp_path, "src/value.py", source.read_bytes(), 1)
    try:
        assert {p.name for p in (worker / "assets").iterdir()} == {"data.txt"}
    finally:
        delete_tree(base)


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_partial_overlay_and_source_junctions_do_not_write_through(monkeypatch, tmp_path):
    source = project(tmp_path)
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_text("outside")
    for name, target in [("outside", external), ("cycle", tmp_path)]:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / name), str(target)],
                       check=True, capture_output=True)
    deny_links(monkeypatch, after=1)
    base = tmp_path / "target" / "mutation-workers" / "run-test"
    worker, = create_workers(base, tmp_path, "src/value.py", source.read_bytes(), 1)
    try:
        assert not (worker / "outside").exists()
        assert not (worker / "cycle").exists()
        assert not any(p.is_symlink() or p.is_junction() for p in worker.rglob("*"))
        (worker / "assets" / "data.txt").write_text("private")
        # Cleanup also handles junctions created by the test command itself.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(worker / "runtime-link"), str(external)],
                       check=True, capture_output=True)
    finally:
        delete_tree(base)
    assert sentinel.read_text() == "outside"
    assert (tmp_path / "assets" / "data.txt").read_text() == "original"
    delete_tree(tmp_path / "outside")
    delete_tree(tmp_path / "cycle")
    delete_tree(external)


def test_fallback_omits_source_symlinks(monkeypatch, tmp_path):
    source = project(tmp_path)
    try:
        (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "alias.py").symlink_to(source)
        (tmp_path / "broken").symlink_to(tmp_path / "missing")
    except OSError as error:
        pytest.skip(f"host cannot create source symlinks: {error}")
    deny_links(monkeypatch)
    base = tmp_path / "workers"
    worker, = create_workers(base, tmp_path, "src/value.py", source.read_bytes(), 1)
    try:
        assert not any((worker / name).exists() or (worker / name).is_symlink()
                       for name in ("cycle", "alias.py", "broken"))
    finally:
        delete_tree(base)


@pytest.mark.parametrize("stage", ["setup", "runtime"])
def test_failed_copy_or_runtime_removes_workers(monkeypatch, tmp_path, stage):
    source = project(tmp_path)
    deny_links(monkeypatch)
    if stage == "setup":
        def fail_copy(*args, **kwargs):
            raise OSError("copy failed")
        monkeypatch.setattr("shutil.copyfile", fail_copy)
        command = [sys.executable, "-c", "pass"]
    else:
        command = [sys.executable, "-c", "import time; time.sleep(10)"]
    site = Site("src/value.py", "value", "defn/value", "value", 1,
                8, 9, "1", "2", "number", "two")
    outcomes = {}
    if stage == "setup":
        with pytest.raises(OSError, match="copy failed"):
            run_mutants(tmp_path, source, source.read_bytes(), [site], 1,
                        CommandRunner(), command, tmp_path, .2, "src/value.py", outcomes)
    else:
        run_mutants(tmp_path, source, source.read_bytes(), [site], 1,
                    CommandRunner(), command, tmp_path, .2, "src/value.py", outcomes)
        assert outcomes == {"two": "killed"}
    assert source.read_bytes() == b"value = 1\n"
    assert (tmp_path / "assets" / "data.txt").read_text() == "original"
    assert not list((tmp_path / "target" / "mutation-workers").iterdir())


def test_copied_node_relative_importers_execute_mutant(monkeypatch, tmp_path):
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; Python forced-fallback acceptance runs unconditionally")
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    source = tmp_path / "src" / "value.mjs"
    original = b"export const value = 1;\n"
    source.write_bytes(original)
    (tmp_path / "test" / "bridge.mjs").write_text(
        "export { value } from '../src/value.mjs';\n")
    (tmp_path / "test" / "check.mjs").write_text(
        "import { value } from './bridge.mjs';\nif (value !== 2) process.exit(19);\n")
    (tmp_path / "package.json").write_text('{"type":"module"}')
    deny_links(monkeypatch)
    site = Site("src/value.mjs", "value", "defn/value", "value", 1,
                21, 22, "1", "2", "number", "two")
    outcomes = {}
    run_mutants(tmp_path, source, original, [site], 1, CommandRunner(),
                [node, "test/check.mjs"], tmp_path, 5, "src/value.mjs", outcomes)
    assert outcomes == {"two": "survived"}
    assert source.read_bytes() == original


def test_runner_exception_cleans_copied_worker(monkeypatch, tmp_path):
    source = project(tmp_path)
    deny_links(monkeypatch)

    class FailingRunner:
        verbose = False

        def run(self, command, cwd, timeout):
            assert (cwd / "src" / "value.py").read_text() == "value = 2\n"
            raise RuntimeError("runner failed")

    site = Site("src/value.py", "value", "defn/value", "value", 1,
                8, 9, "1", "2", "number", "two")
    with pytest.raises(RuntimeError, match="runner failed"):
        run_mutants(tmp_path, source, source.read_bytes(), [site], 1,
                    FailingRunner(), "unused", tmp_path, 1, "src/value.py", {})
    assert not list((tmp_path / "target" / "mutation-workers").iterdir())


@pytest.mark.parametrize("status", [0, 3, 17])
def test_custom_command_preserves_arguments_and_process_status(tmp_path, status):
    (tmp_path / "check.py").write_text(
        "import sys\nassert sys.argv[1:] == ['two words', 'a&b']\n"
        f"sys.exit({status})\n")
    result = CommandRunner().run(
        f'"{sys.executable}" check.py "two words" "a&b"', tmp_path, 5)
    assert result.code == status
    assert not result.timed_out
