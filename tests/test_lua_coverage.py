import json
import os
import shutil
from pathlib import Path

import pytest

from mutator.crapper_link import ensure_crapper
from mutator.lua_coverage import LuaCoverageIndex, _decode, _literal, collect, load
from mutator.runner import CommandRunner


FIXTURE = Path(__file__).parent / "fixtures" / "lua_project"


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "project with spaces"
    (directory / "src").mkdir(parents=True)
    (directory / "spec").mkdir()
    (directory / ".busted").write_text(
        "return { default = {lpath = 'src/?.lua;src/?/init.lua'} }\n", encoding="utf-8"
    )
    source = directory / "src" / "calc.lua"
    source.write_text(
        "local M = {}\n"
        "function M.first()\n"
        "  return 11\n"
        "end\n"
        "function M.second()\n"
        "  return 22\n"
        "end\n"
        "return M\n", encoding="utf-8"
    )
    for name, value in (("first", 11), ("second", 22)):
        (directory / "spec" / f"{name}_spec.lua").write_text(
            "local calc = require('calc')\n"
            f"it('{name}', function() assert.equal({value}, calc.{name}()) end)\n",
            encoding="utf-8",
        )
    return directory, source


@pytest.fixture(params=["pure", "cluacov"])
def toolchain(request, monkeypatch):
    runners = ensure_crapper().runners
    lua = runners.lua_interpreter()
    if lua is None:
        pytest.skip("Lua 5.4 unavailable")
    setup = runners._lua_package_setup(lua)
    if request.param == "pure":
        setup += "package.preload['cluacov.version'] = function() error('pure LuaCov test') end; "
    else:
        # A local rock tree can provide the optional native hook without
        # changing or executing any project that owns that tree.
        tree = Path(__file__).resolve().parents[2] / "monopoly" / ".toolcache" / "luarocks"
        if tree.is_dir():
            setup += f"package.path = {json.dumps(tree.as_posix() + '/share/lua/5.4/?.lua;')} .. package.path; "
            extension = "dll" if os.name == "nt" else "so"
            setup += f"package.cpath = {json.dumps(tree.as_posix() + '/lib/lua/5.4/?.' + extension + ';')} .. package.cpath; "
    probe = CommandRunner().run(
        [lua, "-e", setup + "require('busted.runner'); require('luacov.reporter.lcov'); "
         + ("require('cluacov.hook'); " if request.param == "cluacov" else "") + "print('ready')"],
        Path(__file__).parent, 15,
    )
    if probe.code != 0:
        pytest.skip(f"Busted/LuaCov toolchain unavailable: {probe.output}")
    monkeypatch.setattr(runners, "_lua_package_setup", lambda _: setup)
    return runners


def test_real_suite_attributes_loads_and_independent_functions(project, toolchain):
    root, source = project
    index = collect(root, source)
    assert index.select(source, 3) == ["spec/first_spec.lua"]
    assert index.select(source, 6) == ["spec/second_spec.lua"]
    # Both require calls execute the module under default auto-insulation.
    assert index.select(source, 1) == ["spec/first_spec.lua", "spec/second_spec.lua"]
    assert index.select(source, 1000) is None
    assert index.select(root / "other" / "calc.lua", 3) is None
    assert set(index.specs) == {"spec/first_spec.lua", "spec/second_spec.lua"}
    assert index.seconds_for(list(index.specs)) == index.overhead + sum(index.specs.values())
    assert index.overhead > 0
    assert load(root, source) == index
    report = toolchain._coverage_report(root, root, "lua")
    coverage = toolchain._fresh_lcov(report, root)
    source_lines = next(v for k, v in coverage.items() if Path(k).resolve() == source.resolve())
    assert source_lines[3][0] > 0 and source_lines[6][0] > 0
    assert "-c" not in index.command
    assert not any("spec-coverage.pending" in arg for arg in index.command)


def test_spec_duration_includes_elapsed_sleep(project, toolchain):
    root, source = project
    spec = root / "spec" / "first_spec.lua"
    spec.write_text("require('system').sleep(0.05)\n" + spec.read_text(encoding="utf-8"), encoding="utf-8")
    index = collect(root, source)
    assert index.specs["spec/first_spec.lua"] >= 0.04


def test_helper_load_and_suite_setup_force_full_suite(project, toolchain):
    root, source = project
    (root / "helper.lua").write_text(
        "local calc = require('calc')\n"
        "require('busted').setup(function() calc.first() end)\n", encoding="utf-8"
    )
    (root / ".busted").write_text(
        "return { default = {lpath = 'src/?.lua', helper = 'helper.lua'} }\n", encoding="utf-8"
    )
    index = collect(root, source)
    assert index.select(source, 1) is None
    assert index.select(source, 3) is None
    assert index.select(source, 6) == ["spec/second_spec.lua"]


def test_exit_subscriber_coverage_is_outside_spec_context(project, toolchain):
    root, source = project
    (root / "helper.lua").write_text(
        "local calc = require('calc')\n"
        "require('busted').subscribe({'exit'}, function() calc.first(); return nil, true end)\n",
        encoding="utf-8",
    )
    (root / ".busted").write_text(
        "return { default = {lpath = 'src/?.lua', helper = 'helper.lua'} }\n", encoding="utf-8"
    )
    index = collect(root, source)
    assert index.select(source, 3) is None
    assert index.select(source, 6) == ["spec/second_spec.lua"]


def test_named_task_is_part_of_plan_and_cache_identity(project, toolchain):
    root, source = project
    (root / ".busted").write_text(
        "return { default = {lpath = 'src/?.lua'}, "
        "first = {ROOT = {'spec/first_spec.lua'}} }\n", encoding="utf-8"
    )
    index = collect(root, source, busted_task="first")
    assert index.command[-2:] == ["--run", "first"]
    assert set(index.specs) == {"spec/first_spec.lua"}
    assert load(root, source) is None
    assert load(root, source, busted_task="first") == index


def test_failed_suite_invalidates_existing_cache_and_lcov(project, toolchain):
    root, source = project
    collect(root, source)
    (root / "spec" / "first_spec.lua").write_text("it('fails', function() error('bad baseline') end)\n", encoding="utf-8")
    with pytest.raises(toolchain.CoverageError, match="Lua tests exited 1"):
        collect(root, source)
    report = toolchain._coverage_report(root, root, "lua")
    assert not report.exists()
    assert not (report.parent / "spec-coverage.json").exists()
    assert not (report.parent / "spec-coverage.pending.json").exists()
    assert load(root, source) is None


def test_corrupt_or_unsafe_cache_recollects_and_explicit_reuse_keeps_stale_source(project, toolchain):
    root, source = project
    first = collect(root, source)
    report = toolchain._coverage_report(root, root, "lua")
    cache = report.parent / "spec-coverage.json"
    source.write_text(source.read_text(encoding="utf-8") + "-- changed\n", encoding="utf-8")
    assert collect(root, source, reuse=True) == first
    for corrupt in ("{", '{"version": 1, "complete": false}'):
        cache.write_text(corrupt, encoding="utf-8")
        assert collect(root, source, reuse=True).select(source, 3) == ["spec/first_spec.lua"]
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["specs"]["../outside_spec.lua"] = 0.1
    cache.write_text(json.dumps(raw), encoding="utf-8")
    assert load(root, source) is None
    assert collect(root, source, reuse=True).select(source, 6) == ["spec/second_spec.lua"]


def test_reuse_rejects_mismatched_artifacts_and_invalid_overhead(project, toolchain):
    root, source = project
    index = collect(root, source)
    assert load(root, source).overhead == index.overhead
    report = toolchain._coverage_report(root, root, "lua")
    stats = report.parent / "luacov.stats.out"
    cache = report.parent / "spec-coverage.json"
    for artifact in (report, stats):
        original = artifact.read_bytes()
        artifact.write_bytes(original + b"\n")
        assert load(root, source) is None
        artifact.write_bytes(original)
        assert load(root, source) == index
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["overhead"] = -1
    cache.write_text(json.dumps(raw), encoding="utf-8")
    assert load(root, source) is None
    raw["overhead"] = True
    cache.write_text(json.dumps(raw), encoding="utf-8")
    assert load(root, source) is None


def test_nested_coroutines_and_explicit_stats_save_retain_aggregate(project, toolchain):
    root, source = project
    (root / "spec" / "first_spec.lua").write_text(
        "local calc = require('calc')\n"
        "it('nested coroutines', function()\n"
        "  coroutine.wrap(function()\n"
        "    coroutine.wrap(function() assert.equal(11, calc.first()) end)()\n"
        "  end)()\n"
        "  require('luacov.runner').save_stats()\n"
        "  assert.equal(11, calc.first())\n"
        "end)\n", encoding="utf-8"
    )
    index = collect(root, source)
    assert index.select(source, 3) == ["spec/first_spec.lua"]
    assert index.select(source, 6) == ["spec/second_spec.lua"]
    fresh = toolchain._fresh_lcov(toolchain._coverage_report(root, root, "lua"), root)
    lines = next(v for k, v in fresh.items() if Path(k).resolve() == source.resolve())
    assert lines[3][0] > 0
    assert lines[6][0] > 0
    stats = toolchain._coverage_report(root, root, "lua").parent / "luacov.stats.out"
    rows = stats.read_text(encoding="utf-8").splitlines()
    counts = next(rows[n + 1].split() for n, row in enumerate(rows) if row.endswith("calc.lua"))
    assert int(counts[2]) >= 2


def test_existing_real_fixture_keeps_coverage(tmp_path, toolchain):
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURE, root)
    source = root / "src" / "calc" / "init.lua"
    index = collect(root, source)
    assert index.select(source, 8) == ["spec/calc_spec.lua"]
    fresh = toolchain._fresh_lcov(toolchain._coverage_report(root, root, "lua"), root)
    assert any(Path(name).name == "util.lua" for name in fresh)


def test_index_is_exact_and_fallback_dominates(tmp_path):
    source = tmp_path / "src" / "calc.lua"
    key = os.path.normcase(str(source.resolve()))
    index = LuaCoverageIndex(tmp_path, [], {"spec/a.lua": 0.2}, {key: {3: {"spec/a.lua"}, 4: {"spec/a.lua"}}}, {key: {4}})
    assert index.select(source, 3) == ["spec/a.lua"]
    assert index.select(source, 4) is None
    assert index.select(tmp_path / "calc.lua", 3) is None
    assert index.select(source, 99) is None
    assert index.seconds_for(["spec/a.lua", "spec/a.lua"]) == 0.2
    index.overhead = 3.0
    assert index.seconds_for(["spec/a.lua"]) == 3.2


def test_unproven_attribution_forces_fallback(project):
    root, source = project
    raw = {
        "version": 1, "complete": True, "unsafe": True,
        "specs": {"spec/first_spec.lua": 0.1},
        "lines": {"src/calc.lua": {"3": {"spec/first_spec.lua": True}}},
        "fallback": {},
    }
    index = _decode(raw, root, [], published=False)
    assert index.select(source, 3) is None
    raw["unsafe"] = False
    raw["lines"]["src/calc.lua"]["3"] = {"unseen_spec.lua": True}
    with pytest.raises(ValueError, match="Invalid spec membership"):
        _decode(raw, root, [], published=False)


def test_lua_literal_handles_windows_paths_and_delimiters(project, toolchain):
    root, _source = project
    value = "C:\\project space\\some]=]file]==]name.lua"
    lua = toolchain.lua_interpreter()
    result = CommandRunner().run([lua, "-e", "io.write(" + _literal(value) + ")"], root, 5)
    assert result.code == 0
    assert result.output == value
