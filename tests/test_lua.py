import os
import shutil
from pathlib import Path

import pytest

from mutator.crapper_link import ensure_crapper
from mutator.functions import is_private, sites_in_file
from mutator.runner import CommandRunner
from mutator.runner import test_plan as command_for


@pytest.fixture
def runners():
    return ensure_crapper().runners


def _sites(tmp_path: Path, relative: str, source: str):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return sites_in_file(source, path, tmp_path, relative)


def test_lua_operators_booleans_and_constants(tmp_path):
    source = """local M = {}

function M.place(x, ready)
  if x ~= 0 and x == 1 or not ready then
    return x + 1 // 2
  end
  return x * 2 / 3 < 0 and true
end

local function hide()
  return false
end

return M
"""
    sites = _sites(tmp_path, "src/demo/board.lua", source)
    described = {(site.form_id, site.original, site.mutant) for site in sites}
    assert described == {
        ("defn/M.place", "~=", "=="),
        ("defn/M.place", "and", "or"),
        ("defn/M.place", "==", "~="),
        ("defn/M.place", "or", "and"),
        ("defn/M.place", "not", ""),
        ("defn/M.place", "+", "-"),
        ("defn/M.place", "//", "/"),
        ("defn/M.place", "*", "/"),
        ("defn/M.place", "/", "*"),
        ("defn/M.place", "<", "<="),
        ("defn/M.place", "true", "false"),
        ("defn/M.place", "0", "1"),
        ("defn/M.place", "1", "0"),
        ("defn-/hide", "false", "true"),
    }
    assert {site.namespace for site in sites} == {"demo.board"}


def test_lua_never_mutates_concat_or_length(tmp_path):
    source = 'function label(xs)\n  return "n=" .. #xs\nend\n'
    assert _sites(tmp_path, "a.lua", source) == []


def test_python_floor_division_is_unchanged(tmp_path):
    source = "def half(x):\n    return x // 2\n"
    assert [site.original for site in _sites(tmp_path, "a.py", source)] == []


def test_lua_local_functions_are_private():
    assert is_private("lua", "hide", "local function hide() end\n", 1, 1) is True
    assert is_private("lua", "hide", "local hide = function() end\n", 1, 1) is True
    assert is_private("lua", "M.show", "function M.show() end\n", 1, 1) is False
    assert is_private("lua", "show", "show = function() end\n", 1, 1) is False


def test_lua_tests_run_busted_in_the_nearest_busted_or_rockspec_directory(tmp_path, monkeypatch, runners):
    monkeypatch.setattr(runners, "lua_interpreter", lambda: "/opt/lua 5.4/lua")
    monkeypatch.setattr(runners, "_lua_package_setup", lambda lua: "")
    root = tmp_path.resolve()
    (root / "pkg" / "src").mkdir(parents=True)
    (root / "pkg" / "demo-1.0-1.rockspec").write_text("", encoding="utf-8")
    source = root / "pkg" / "src" / "a.lua"
    source.write_text("", encoding="utf-8")
    command, directory = command_for(root, source, "lua", None)
    assert command[0] == "/opt/lua 5.4/lua"
    assert directory == root / "pkg"
    (root / "pkg" / "src" / ".busted").write_text("return {}", encoding="utf-8")
    assert command_for(root, source, "lua", None)[1] == root / "pkg" / "src"
    loose = root / "b.lua"
    loose.write_text("", encoding="utf-8")
    assert command_for(root, loose, "lua", None)[1] == root


def test_lua_plan_embeds_a_windows_interpreter_path_as_one_argument(tmp_path, monkeypatch, runners):
    monkeypatch.setattr(runners, "lua_interpreter", lambda: "C:\\Program Files\\Lua 5.4\\lua.exe")
    monkeypatch.setattr(runners, "_lua_package_setup", lambda lua: "package.path = 'wrapper'; ")
    source = tmp_path / "a.lua"
    source.write_text("", encoding="utf-8")
    command, _directory = command_for(tmp_path, source, "lua", None)
    assert command == [
        "C:\\Program Files\\Lua 5.4\\lua.exe", "-e",
        "package.path = 'wrapper'; pcall(require, 'luarocks.loader'); "
        "require('busted.runner')({standalone = false}); os.exit(0)",
        "--", "busted", "--ignore-lua",
    ]


def test_lua_falls_back_to_lua54_when_no_interpreter_is_found(tmp_path, monkeypatch, runners):
    monkeypatch.setattr(runners, "lua_interpreter", lambda: None)
    monkeypatch.setattr(runners, "_lua_package_setup", lambda lua: "")
    source = tmp_path / "a.lua"
    source.write_text("", encoding="utf-8")
    assert command_for(tmp_path, source, "lua", None)[0][0] == "lua5.4"


def test_lua_override_stays_a_shell_command_at_project_root(tmp_path, monkeypatch, runners):
    monkeypatch.setattr(runners, "lua_interpreter", lambda: pytest.fail("override must skip discovery"))
    source = tmp_path / "pkg" / "a.lua"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")
    assert command_for(tmp_path, source, "lua", "custom busted --filter=calc") == (
        "custom busted --filter=calc", tmp_path.resolve()
    )


FIXTURE = Path(__file__).parent / "fixtures" / "lua_project"


def _real_lua_interpreter() -> str | None:
    found = ensure_crapper().runners.lua_interpreter()
    if found is None:
        return None
    if os.name == "nt" and Path(found).suffix.lower() != ".exe":
        return None
    return found


def _busted_script() -> tuple[Path, Path] | None:
    """The busted runner script and its rock tree, when busted is installed.

    luarocks exposes busted on PATH only as a wrapper (a .bat on Windows),
    which the argv form cannot launch; the script inside the rock tree is
    the real entry point on every platform.
    """

    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "luarocks")
    roots += [Path.home() / ".luarocks", Path("/usr/local"), Path("/usr")]
    for root in roots:
        for script in sorted(root.glob("lib/luarocks/rocks-5.4/busted/*/bin/busted")):
            return script, root
    return None


def _busted_argv_ready() -> bool:
    return _real_lua_interpreter() is not None and _busted_script() is not None


@pytest.mark.skipif(
    not _busted_argv_ready(), reason="busted and a Lua 5.4 interpreter are not installed"
)
def test_busted_runs_with_a_spaced_interpreter_path(tmp_path, monkeypatch, runners):
    """Execute the generated plan with a spaced native interpreter, including
    Busted config discovery and its success/failure exit codes.
    """

    interpreter = Path(_real_lua_interpreter())
    spaced = tmp_path / "lua tools" / "with space"
    spaced.mkdir(parents=True)
    for artifact in interpreter.parent.iterdir():
        if artifact.suffix.lower() in (".exe", ".dll", ".so"):
            shutil.copy(artifact, spaced / artifact.name)
    lua = spaced / interpreter.name
    assert " " in str(lua)
    if os.name == "nt":
        assert "\\" in str(lua)

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / ".busted").write_text("return { default = { ROOT = {'checks'} } }\n", encoding="utf-8")
    source = project / "src" / "calc.lua"
    source.write_text("return {}\n", encoding="utf-8")
    (project / "checks").mkdir()
    spec = project / "checks" / "calc_spec.lua"
    spec.write_text(
        "describe('calc', function()\n"
        "  it('adds', function()\n"
        "    assert.are.equal(2, 1 + 1)\n"
        "  end)\n"
        "end)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(runners, "lua_interpreter", lambda: str(lua))
    command, directory = command_for(tmp_path, source, "lua", None)
    assert command[0] == str(lua)
    assert command[-3:] == ["--", "busted", "--ignore-lua"]
    assert directory == project

    script, tree = _busted_script()
    monkeypatch.setenv(
        "LUA_PATH", f"{tree}/share/lua/5.4/?.lua;{tree}/share/lua/5.4/?/init.lua;;"
    )
    library = "?.dll" if os.name == "nt" else "?.so"
    monkeypatch.setenv("LUA_CPATH", f"{tree}/lib/lua/5.4/{library};;")
    result = CommandRunner().run(command, directory, 60)
    assert result.code == 0, result.output
    assert "1 success" in result.output
    spec.write_text("describe('calc', function() it('fails', function() assert.are.equal(3, 1 + 1) end) end)\n", encoding="utf-8")
    failed = CommandRunner().run(command, directory, 60)
    assert failed.code == 1, failed.output
    assert "1 failure" in failed.output


def _toolchain_ready() -> bool:
    import shutil

    runners = ensure_crapper().runners
    return all(shutil.which(tool) for tool in ("busted", "luacov")) and (
        runners.lua_interpreter() is not None
    )


@pytest.mark.skipif(not _toolchain_ready(), reason="busted, luacov, and Lua 5.4 are not installed")
def test_fixture_project_end_to_end(tmp_path):
    import shutil
    import subprocess
    import sys

    project = tmp_path / "lua_project"
    shutil.copytree(FIXTURE, project)
    completed = subprocess.run(
        [sys.executable, "-m", "mutator"], cwd=project, capture_output=True, text=True
    )
    assert completed.returncode == 3, completed.stderr  # the fixture leaves survivors
    assert "KILLED    src/calc/init.lua:8 == -> ~=" in completed.stdout
    assert "UNCOVERED src/calc/util.lua:16 1 -> 0" in completed.stdout
    snapshot = (project / ".metrics" / "mutate" / "calc.util.edn").read_text(encoding="utf-8")
    assert '{:id "defn-/between" :kind "defn-"' in snapshot
