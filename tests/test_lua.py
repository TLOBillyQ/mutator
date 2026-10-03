from pathlib import Path

import pytest

from mutator.crapper_link import ensure_crapper
from mutator.functions import is_private, sites_in_file
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
    root = tmp_path.resolve()
    (root / "pkg" / "src").mkdir(parents=True)
    (root / "pkg" / "demo-1.0-1.rockspec").write_text("", encoding="utf-8")
    source = root / "pkg" / "src" / "a.lua"
    source.write_text("", encoding="utf-8")
    assert command_for(root, source, "lua", None) == ("busted --lua='/opt/lua 5.4/lua'", root / "pkg")
    loose = root / "b.lua"
    loose.write_text("", encoding="utf-8")
    assert command_for(root, loose, "lua", None) == ("busted --lua='/opt/lua 5.4/lua'", root)


def test_lua_falls_back_to_lua54_when_no_interpreter_is_found(tmp_path, monkeypatch, runners):
    monkeypatch.setattr(runners, "lua_interpreter", lambda: None)
    source = tmp_path / "a.lua"
    source.write_text("", encoding="utf-8")
    assert command_for(tmp_path, source, "lua", None)[0] == "busted --lua=lua5.4"


FIXTURE = Path(__file__).parent / "fixtures" / "lua_project"


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
