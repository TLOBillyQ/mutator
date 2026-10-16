"""History changes Lua execution order without changing the selected mutations."""

from types import SimpleNamespace

import pytest

from mutator import engine
from mutator.model import History, PriorForm, Site


def site(name, line):
    return Site("calc.lua", "calc", "defn/" + name, name, line,
                0, 1, "1", "0", "number", name)


@pytest.mark.parametrize("mutate_all", [False, True])
@pytest.mark.parametrize("language,override", [("lua", None), ("lua", "custom tests"), ("python", None)])
def test_prior_lua_survivors_first_preserves_selection_and_stable_groups(
    tmp_path, monkeypatch, mutate_all, language, override,
):
    source = tmp_path / "calc.lua"
    source.write_bytes(b"1\n")
    names = ["unknown", "killed", "survivor-a", "changed-killed", "survivor-b",
             "uncovered-survivor", "excluded-survivor"]
    found = [site(name, line) for line, name in enumerate(names, 1)]
    history = History(
        forms={(s.namespace, s.form_id): PriorForm(s.namespace, s.form_id, "old", s.file)
               for s in found if s.name != "unknown"},
        outcomes={"killed": "killed", "changed-killed": "killed", "survivor-a": "survived",
                  "survivor-b": "survived", "uncovered-survivor": "survived",
                  "excluded-survivor": "survived"},
    )
    digests = {(s.namespace, s.form_id): "new" if s.name == "changed-killed" else "old"
               for s in found}
    monkeypatch.setattr(engine, "ensure_crapper", lambda: SimpleNamespace(
        discover=SimpleNamespace(language_of=lambda path: language)))
    monkeypatch.setattr(engine, "sites_in_file", lambda *args: found)
    monkeypatch.setattr(engine, "load_history", lambda *args: history)
    monkeypatch.setattr(engine, "form_digests", lambda *args: digests)
    monkeypatch.setattr(engine, "_forms", lambda *args: [])
    monkeypatch.setattr(engine, "write_results", lambda *args: [])
    executed = []

    def execute(*args):
        selected, outcomes = args[3], args[10]
        executed.extend(s.mutation_id for s in selected)
        outcomes.update((s.mutation_id, "survived") for s in selected)

    monkeypatch.setattr(engine, "_apply_selected", execute)
    result = engine.mutate_file(
        source, tmp_path, runner=None, covered_lines={1, 2, 3, 4, 5, 7},
        ignore_coverage=False, mutate_all=mutate_all, lines={1, 2, 3, 4, 5, 6},
        test_command=override, timeout_factor=10, mutation_warning=100, baselines={},
    )
    remaining = ["unknown", "killed", "changed-killed"] if mutate_all else ["unknown", "changed-killed"]
    if language == "lua" and override is None:
        assert executed == ["survivor-a", "survivor-b", *remaining]
    else:
        assert executed == [name for name in names[:5] if name in remaining or name.startswith("survivor")]
    assert set(executed) == {"survivor-a", "survivor-b", *remaining}
    assert result.statuses["uncovered-survivor"] == "uncovered"
    if not mutate_all:
        assert result.statuses["killed"] == "killed"
    assert source.read_bytes() == b"1\n"
