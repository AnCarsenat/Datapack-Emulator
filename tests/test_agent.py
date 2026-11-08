"""The JSON API for scripts and AI agents (datapack_emulator.agent) and the
``--json`` output of run, matrix and test."""

from __future__ import annotations

import json

import pytest

from datapack_emulator import agent
from datapack_emulator.cli import main
from datapack_emulator.settings import PATHS

COUNTER = {
    "data/test/function/load.mcfunction": (
        "scoreboard objectives add t dummy\nscoreboard players set #g t 0\n"
        'tellraw @a {"text":"loaded"}\n'
    ),
    "data/test/function/tick.mcfunction": "scoreboard players add #g t 1\n",
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
}


@pytest.fixture(autouse=True)
def no_jars(tmp_path, monkeypatch):
    """No client jar is found or downloaded; caches stay in a throwaway folder."""
    from datapack_emulator.cli import common
    from datapack_emulator.emulator.vanilla import VanillaLibrary

    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    library = lambda: VanillaLibrary(tmp_path / "jars", search_dirs=[])  # noqa: E731
    monkeypatch.setattr(common, "default_library", library)
    monkeypatch.setattr(agent, "default_library", library)


def test_results_are_plain_json(make_pack):
    pack = make_pack(COUNTER)
    for result in (
        agent.run(pack, "1.21.4", ticks=3),
        agent.check(pack, "1.21.4"),
        agent.test(pack, ["2:scoreboard players get #g t"]),
        agent.matrix(pack, ["1.21", "1.21.4"]),
        agent.versions(),
        agent.explain(pack, "say hi", "1.21.4"),
    ):
        assert json.loads(json.dumps(result)) == result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["summary"], str)


def test_run_reports_world_chat_and_console_answers(make_pack):
    pack = make_pack(COUNTER)
    result = agent.run(pack, "1.21.4", ticks=5, commands=["scoreboard players get #g t"])
    assert result["ok"]
    assert result["version"] == "1.21.4"
    assert result["world"]["scores"]["#g"]["t"] == 5
    assert any("loaded" in line for line in result["chat"])
    (answer,) = result["commands"]
    assert answer["success"] and answer["value"] == 5
    assert result["call_graph"]["missing_functions"] == []
    assert result["profile"]["estimated"] is True


def test_run_flags_errors_and_missing_functions(make_pack):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": "function test:nowhere\nfrobnicate now\n",
        }
    )
    result = agent.run(pack, "1.21.4", ticks=1)
    assert result["ok"] is False
    assert "test:nowhere" in result["call_graph"]["missing_functions"]


def test_run_does_not_count_macro_calls_as_missing(make_pack):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": 'function test:pick {f:"test:dust/red"}\n',
            "data/test/function/pick.mcfunction": (
                "$function $(f)\n$function test:dust/$(f)\nfunction test:gone\n"
            ),
            "data/test/function/dust/red.mcfunction": "say red\n",
        }
    )
    result = agent.run(pack, "1.21.4", ticks=1)
    graph = result["call_graph"]
    # only the static call to a function that does not exist is missing
    assert graph["missing_functions"] == ["test:gone"]
    assert graph["dynamic_calls"] == ["minecraft:$(f)", "test:dust/$(f)"]
    assert result["ok"] is False
    fixed = make_pack(
        {
            "data/test/function/tick.mcfunction": 'function test:pick {f:"test:dust/red"}\n',
            "data/test/function/pick.mcfunction": "$function $(f)\n",
            "data/test/function/dust/red.mcfunction": "say red\n",
        }
    )
    assert agent.run(fixed, "1.21.4", ticks=1)["ok"] is True


def test_run_caps_records_but_keeps_errors(make_pack):
    lines = "".join(f'tellraw @a {{"text":"line {n}"}}\n' for n in range(30))
    pack = make_pack({"data/test/function/tick.mcfunction": lines})
    result = agent.run(pack, "1.21.4", ticks=2, max_records=10)
    assert result["truncated"] is True
    assert len(result["records"]) == 10
    assert result["records_total"] > 10


def test_test_passes_and_fails_with_checks(make_pack):
    pack = make_pack(COUNTER)
    good = agent.test(pack, ["3:"], checks=["score #g t = 4"])
    assert good["ok"], good
    bad = agent.test(pack, [{"command": "", "at_tick": 3, "checks": ["score #g t = 99"]}])
    assert bad["ok"] is False
    assert bad["results"][0]["status"] == "fail"


def test_test_rejects_malformed_input(make_pack):
    pack = make_pack(COUNTER)
    with pytest.raises(agent.AgentError):
        agent.test(pack, [])
    with pytest.raises(agent.AgentError):
        agent.test(pack, ["1:say a", "2:say b"], checks=["score #g t = 1"])
    with pytest.raises(agent.AgentError):
        agent.test(pack, ["1:say a"], expect_value="lots")


def test_bad_paths_and_versions_raise_agent_error(make_pack, tmp_path):
    with pytest.raises(agent.AgentError, match="no such"):
        agent.run(tmp_path / "missing")
    with pytest.raises(agent.AgentError, match="unknown Minecraft version"):
        agent.run(make_pack(COUNTER), "9.9.9")
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "pack.mcmeta").write_text('{"pack":{"pack_format":61,"description":""}}')
    with pytest.raises(agent.AgentError, match="nothing to emulate"):
        agent.check(empty)


def test_check_sees_plural_folders_on_1_21(make_pack):
    pack = make_pack(
        {
            "data/test/functions/tick.mcfunction": "say old folder\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        },
        mcmeta={"pack": {"pack_format": 48, "description": ""}},
    )
    result = agent.run(pack, "1.21", ticks=1)
    # a 1.21 server reads neither the plural folder nor the plural tag: nothing runs
    assert result["profile"]["functions"] == [] or all(
        row["function"] != "test:tick" for row in result["profile"]["functions"]
    )


def test_explain_reports_when_a_feature_arrived(make_pack):
    result = agent.explain(None, "execute if function test:x run say hi", "1.20.2")
    assert result["ok"] is False
    assert result["missing_features"][0]["since"] == "1.20.3"
    assert agent.explain(None, "execute if function test:x run say hi", "1.20.3")["ok"]


def test_matrix_rows_per_version(make_pack):
    pack = make_pack(COUNTER)
    result = agent.matrix(pack, ["1.21", "1.21.4"], ticks=2)
    assert [row["version"] for row in result["results"]] == ["1.21", "1.21.4"]
    assert result["ok"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "{pack}", "--version", "1.21.4", "--ticks", "3", "--json"],
        # the debugger prints its breakpoints and stops: not on standard output
        [
            "run",
            "{pack}",
            "--version",
            "1.21.4",
            "--ticks",
            "3",
            "--json",
            "--break",
            "test:tick:1",
        ],
        ["matrix", "{pack}", "--versions", "1.21", "1.21.4", "--ticks", "2", "--json"],
        [
            "test",
            "{pack}",
            "--version",
            "1.21.4",
            "--test",
            "2:",
            "--check",
            "score #g t = 3",
            "--json",
        ],
    ],
)
def test_cli_json_keeps_standard_output_for_the_json(make_pack, capsys, arguments):
    pack = make_pack(COUNTER)
    code = main([str(pack) if item == "{pack}" else item for item in arguments])
    out = capsys.readouterr().out
    data = json.loads(out)  # nothing but the JSON document
    assert code == 0 and data["ok"] and data["exit_code"] == 0
    if arguments[0] == "run":
        assert any("loaded" in line for line in data["chat"])  # the shape of agent.run


def test_cli_test_json_failure_sets_exit_code(make_pack, capsys):
    pack = make_pack(COUNTER)
    code = main(
        [
            "test",
            str(pack),
            "--version",
            "1.21.4",
            "--test",
            "2:",
            "--check",
            "score #g t = 9",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["ok"] is False and data["exit_code"] == 1


def test_plural_folders_on_1_21_are_not_ok(make_pack):
    """The pack loads, logs nothing and does nothing in game; the agent API
    must not call that a success."""
    pack = make_pack(
        {
            "data/test/functions/tick.mcfunction": "say old folder\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        },
        mcmeta={"pack": {"pack_format": 48, "description": ""}},
    )
    result = agent.run(pack, "1.21", ticks=1)
    assert result["ok"] is False
    assert result["ignored_folders"]
    codes = {p["code"] for p in agent.check(pack, "1.21")["results"][0]["problems"]}
    assert "pack" in codes and "ignored-folder" in codes
    assert agent.check(pack, "1.21")["ok"] is False
    # the same pack is fine where plural folders are read
    assert agent.run(pack, "1.20.4", ticks=1)["ignored_folders"] == []


def test_singular_function_folder_with_plural_tags_is_caught(make_pack):
    """function/ is right on 1.21 but tags/functions/ is not: #tick is empty."""
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": "say hi\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        },
        mcmeta={"pack": {"pack_format": 48, "description": ""}},
    )
    result = agent.check(pack, "1.21")
    problems = result["results"][0]["problems"]
    assert any(p["code"] == "ignored-folder" and p["severity"] == "error" for p in problems)
