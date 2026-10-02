import sys

from mutator.runner import CommandRunner, _clojure_command, _python_command, nearest


def test_nearest_walks_up_to_the_marker(tmp_path):
    root = tmp_path / "proj"
    nested = root / "src" / "demo"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    source = nested / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert nearest(source, "pyproject.toml", root) == root
    assert nearest(nested, "pyproject.toml", root) == root
    assert nearest(source, "missing.toml", root) is None


def test_clojure_commands(tmp_path):
    bb = tmp_path / "bb-spec"
    bb.mkdir()
    (bb / "bb.edn").write_text('{:tasks {spec "spec"}}', encoding="utf-8")
    assert _clojure_command(bb) == "bb spec --tag ~no-mutate"

    bb_test = tmp_path / "bb-test"
    bb_test.mkdir()
    (bb_test / "bb.edn").write_text("{:tasks {test (clojure \"-M:test\")}}", encoding="utf-8")
    assert _clojure_command(bb_test) == "bb test"

    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "deps.edn").write_text(
        "{:aliases {:spec {:extra-deps {speclj/speclj {}}}}}", encoding="utf-8"
    )
    assert _clojure_command(spec) == "clj -M:spec --tag ~no-mutate"

    spec_only = tmp_path / "spec-only"
    spec_only.mkdir()
    (spec_only / "deps.edn").write_text("{:aliases {:spec {}}}", encoding="utf-8")
    assert _clojure_command(spec_only) == "clj -M:spec"

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "deps.edn").write_text("{:deps {}}", encoding="utf-8")
    assert _clojure_command(plain) == "clj -M:test"


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def test_python_command_uses_an_absolute_project_interpreter(tmp_path):
    project = tmp_path / "proj"
    dot = project / ".venv" / "bin" / "python"
    _executable(dot)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(project) == f"{dot.absolute()} -m pytest"

    other = tmp_path / "other"
    plain = other / "venv" / "bin" / "python"
    _executable(plain)
    assert _python_command(other) == f"{plain.absolute()} -m unittest discover"

    _executable(other / ".venv" / "bin" / "python")
    assert _python_command(other) == f"{(other / '.venv' / 'bin' / 'python').absolute()} -m unittest discover"


def test_python_command_keeps_the_virtualenv_symlink(tmp_path):
    project = tmp_path / "proj"
    target = project / "real-python"
    _executable(target)
    link = project / ".venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(project) == f"{link.absolute()} -m pytest"


def test_python_command_falls_back_when_the_virtualenv_cannot_run(tmp_path):
    project = tmp_path / "proj"
    binary = project / ".venv" / "bin" / "python"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    assert _python_command(project) == f"{sys.executable} -m unittest discover"

    missing = tmp_path / "missing"
    missing.mkdir()
    assert _python_command(missing) == f"{sys.executable} -m unittest discover"


def test_python_commands(tmp_path):
    pytest_dir = tmp_path / "py"
    pytest_dir.mkdir()
    (pytest_dir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(pytest_dir).endswith("-m pytest")

    unit = tmp_path / "unit"
    unit.mkdir()
    assert _python_command(unit).endswith("-m unittest discover")

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "setup.cfg").write_text("[tool:pytest]\n", encoding="utf-8")
    assert _python_command(cfg).endswith("-m pytest")

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\ndependencies=['pytest']\n", encoding="utf-8")
    assert _python_command(project).endswith("-m pytest")

    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "conftest.py").write_text("", encoding="utf-8")
    assert _python_command(conf).endswith("-m pytest")


def test_a_worker_overlay_is_imported_ahead_of_the_environment(tmp_path):
    worker = tmp_path / "target" / "mutation-workers" / "run-1" / "worker-0"
    (worker / "src").mkdir(parents=True)
    (worker / "src" / "demo.py").write_text("VALUE = 'worker'\n", encoding="utf-8")
    command = f"{sys.executable} -c 'import demo; print(demo.VALUE)'"
    result = CommandRunner().run(command, worker, 5)
    assert result.code == 0
    assert result.output.strip() == "worker"
