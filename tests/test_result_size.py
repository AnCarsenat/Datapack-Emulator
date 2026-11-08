"""``agent.run`` and ``run --json`` stay small: ``level`` filters each
console command's output, outputs are capped, and ``max_result_chars``
trims what is still too big, saying so."""

from __future__ import annotations

import json

import pytest

from datapack_emulator import agent
from datapack_emulator.cli import main
from datapack_emulator.settings import PATHS


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


#: a function whose every line logs a debug line ("Set [t] for #hN to N")
NOISY = {
    "data/test/function/noisy.mcfunction": "scoreboard objectives add t dummy\n"
    + "".join(f"scoreboard players set #h{n % 50} t {n}\n" for n in range(3000)),
    "data/test/function/tick.mcfunction": "",
}


def size(result) -> int:
    return len(json.dumps(result))


def test_level_filters_each_console_commands_output(make_pack):
    pack = make_pack(NOISY)
    result = agent.run(
        pack,
        "1.21.4",
        ticks=1,
        level="warning",
        max_records=40,
        commands=["function test:noisy", "scoreboard players get #h1 t"],
    )
    assert json.loads(json.dumps(result)) == result
    assert size(result) < 20_000
    noisy, answer = result["commands"]
    assert all(line["level"] != "debug" for line in noisy["output"])
    assert noisy["output_total"] == len(noisy["output"]) and not noisy["truncated"]
    # the command's own answer is kept whatever the level
    assert answer["value"] == 2951
    assert [line["message"] for line in answer["output"]] == ["#h1 has 2951 [t]"]
    assert result["trimmed"] == []


def test_output_is_capped_per_command_errors_first(make_pack):
    pack = make_pack(NOISY)
    result = agent.run(
        pack,
        "1.21.4",
        ticks=1,
        level="debug",
        max_output_per_command=5,
        commands=["function test:noisy"],
    )
    (noisy,) = result["commands"]
    assert noisy["truncated"] is True
    assert len(noisy["output"]) == 5
    assert noisy["output_total"] > 3000
    assert noisy["output"][-1]["message"] == "Set [t] for #h49 to 2999"  # the newest


def test_command_output_keeps_errors_then_the_answer_then_the_newest():
    from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource

    def line(text, level=LogLevel.DEBUG, function="test:f"):
        return LogRecord(LogSource.GAME, level, text, function=function, line=1)

    records = [line(f"debug {n}") for n in range(100)]
    records.insert(10, line("broken", LogLevel.ERROR))
    records.insert(20, line("the answer", LogLevel.INFO, function=agent.CONSOLE))
    shown = agent.command_output(records, "warning", 3)
    assert [item["message"] for item in shown["output"]] == ["broken", "the answer"]
    assert shown["output_total"] == 2 and not shown["truncated"]
    shown = agent.command_output(records, "debug", 3)
    assert [item["message"] for item in shown["output"]] == ["broken", "the answer", "debug 99"]
    assert shown["output_total"] == 102 and shown["truncated"]


def test_max_result_chars_trims_the_biggest_parts(make_pack):
    storage = "".join(
        f'data modify storage test:s deep.list append value {{n:{n},text:"padding {n}"}}\n'
        for n in range(400)
    )
    pack = make_pack(
        {
            **NOISY,
            "data/test/function/fill.mcfunction": storage,
        }
    )
    result = agent.run(
        pack,
        "1.21.4",
        ticks=1,
        level="debug",
        max_records=5000,
        max_output_per_command=5000,
        max_result_chars=15_000,
        commands=["function test:noisy", "function test:fill", "data get storage test:s"],
    )
    assert json.loads(json.dumps(result)) == result
    assert size(result) <= 15_000
    trimmed = "\n".join(result["trimmed"])
    assert "commands[0].output" in trimmed
    assert "world.storage.test:s.deep.list" in trimmed
    assert "max_result_chars is 15000" in trimmed
    assert result["commands"][0]["truncated"] is True
    # a long message is cut, and says so
    assert "more characters]" in result["commands"][2]["output"][0]["message"]
    untrimmed = agent.run(
        pack,
        "1.21.4",
        ticks=1,
        level="debug",
        max_records=5000,
        max_output_per_command=5000,
        max_result_chars=None,
        commands=["function test:noisy", "function test:fill", "data get storage test:s"],
    )
    assert size(untrimmed) > 100_000 and "trimmed" not in untrimmed


def test_records_and_chat_are_bounded_by_size(make_pack):
    long = "x" * 5000
    pack = make_pack(
        {"data/test/function/tick.mcfunction": "".join(f"say {long}{n}\n" for n in range(30))}
    )
    result = agent.run(pack, "1.21.4", ticks=3, max_result_chars=20_000)
    assert size(result) <= 20_000
    assert result["chat_total"] == 90
    assert result["chat_truncated"] is True or result["truncated"] is True
    assert result["trimmed"]


def test_cli_run_json_keeps_under_max_result_chars(make_pack, capsys):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": "".join(
                f"scoreboard players set #holder{n} t {n}\n" for n in range(2000)
            ),
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
        }
    )
    arguments = ["run", str(pack), "--version", "1.21.4", "--ticks", "1", "--json"]
    code = main([*arguments, "--max-result-chars", "10000"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(json.dumps(data)) <= 10_000
    assert any(line.startswith("world.scores") for line in data["trimmed"])
    main([*arguments, "--max-result-chars", "0"])
    whole = json.loads(capsys.readouterr().out)
    assert len(whole["world"]["scores"]) == 2000 and "trimmed" not in whole
