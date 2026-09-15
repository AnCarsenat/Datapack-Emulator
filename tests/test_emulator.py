from conftest import chat, game_errors

from src.emulator import Datapack, Emulator
from src.emulator.runtime.output import LogLevel, LogSource


def run(make_pack, body: str, version: str = "1.21.4", ticks: int = 1, extra=None, **kwargs):
    files = {"data/test/function/tick.mcfunction": body, **(extra or {})}
    pack = Datapack.load(make_pack(files))
    emulator = Emulator(pack, version=version, **kwargs)
    emulator.run(ticks=ticks)
    return emulator


def test_scoreboard_and_execute_store(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add x dummy\n"
        "scoreboard players add #g x 5\n"
        "execute store result score #copy x run scoreboard players get #g x\n"
        "scoreboard players operation #copy x *= #g x\n",
    )
    board = emulator.world.scoreboard
    assert board.get("#g", "x") == 5
    assert board.get("#copy", "x") == 25


def test_execute_as_and_conditions(make_pack):
    emulator = run(
        make_pack,
        "tag @a add seen\n"
        "execute as @a[tag=seen] run say tagged\n"
        "execute unless entity @e[type=minecraft:pig] run say no pigs\n",
        players=2,
    )
    messages = chat(emulator.output.records)
    assert messages.count("[Player1] tagged") == 1 and messages.count("[Player2] tagged") == 1
    assert "[Server] no pigs" in messages


def test_unknown_objective_uses_vanilla_wording(make_pack):
    emulator = run(make_pack, "scoreboard players set @a missing 1\n")
    assert game_errors(emulator.output.records) == ["Unknown scoreboard objective 'missing'"]


def test_return_stops_the_function(make_pack):
    emulator = run(make_pack, "say before\nreturn 1\nsay after\n")
    messages = chat(emulator.output.records)
    assert "[Server] before" in messages and "[Server] after" not in messages


def test_schedule_replace_postpones_and_append_fires(make_pack):
    replace = run(
        make_pack,
        "schedule function test:later 2t\n",
        ticks=4,
        extra={"data/test/function/later.mcfunction": "say fired\n"},
    )
    assert "[Server] fired" not in chat(replace.output.records)

    append = run(
        make_pack,
        "execute unless score #once x matches 1 run schedule function test:later 2t\n"
        "scoreboard objectives add x dummy\n"
        "scoreboard players set #once x 1\n",
        ticks=4,
        extra={"data/test/function/later.mcfunction": "say fired\n"},
    )
    assert chat(append.output.records).count("[Server] fired") == 1


def test_macro_with_storage_and_missing_argument(make_pack):
    emulator = run(
        make_pack,
        'data modify storage test:mem name set value "Alex"\n'
        "function test:greet with storage test:mem\n"
        "function test:greet\n",
        extra={"data/test/function/greet.mcfunction": "$say hello $(name)\n"},
    )
    assert "[Server] hello Alex" in chat(emulator.output.records)
    assert any("Missing argument name" in error for error in game_errors(emulator.output.records))


def test_command_missing_from_version_gets_unknown_command(make_pack):
    emulator = run(
        make_pack,
        "",
        version="1.19.4",
        extra={  # 1.19.4 reads the plural folders
            "data/test/functions/tick.mcfunction": "return 1\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        },
    )
    errors = game_errors(emulator.output.records)
    assert errors and errors[0].startswith("Unknown or incomplete command")
    assert errors[0].endswith("return<--[HERE]")
    notes = [
        r.message
        for r in emulator.output.records
        if r.source is LogSource.EMULATOR and r.level >= LogLevel.WARNING
    ]
    assert any("added in 1.20" in note for note in notes)


def test_recursion_is_capped(make_pack):
    emulator = run(
        make_pack,
        "function test:loop\n",
        extra={"data/test/function/loop.mcfunction": "function test:loop\n"},
    )
    assert emulator.profiler.entries["test:loop"]["calls"] == Emulator.MAX_DEPTH - 1
    notes = [r.message for r in emulator.output.records if "depth limit" in r.message]
    assert notes and "1024" in notes[0]


def test_tag_remove_without_tag_reports_failure(make_pack):
    emulator = run(make_pack, "tag @a remove nothing\n")
    assert game_errors(emulator.output.records) == ["Target does not have this tag"]


def test_trigger_needs_enable(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add t trigger\n"
        "execute as @a run trigger t\n"
        "scoreboard players enable @a t\n"
        "execute as @a run trigger t\n",
    )
    assert game_errors(emulator.output.records) == ["You cannot trigger this objective yet"]
    assert emulator.world.scoreboard.get("Player1", "t") == 1


def test_profiler_charges_time(make_pack):
    emulator = run(make_pack, "say hi\n", ticks=3)
    entry = emulator.profiler.entries["test:tick"]
    assert entry["calls"] == 3 and entry["total_us"] > 0
    assert len(emulator.profiler.tick_times) == 3


def test_trigger_on_missing_or_wrong_objective(make_pack):
    emulator = run(
        make_pack,
        "execute as @a run trigger nope\n"
        "scoreboard objectives add plain dummy\n"
        "execute as @a run trigger plain\n",
    )
    assert game_errors(emulator.output.records) == [
        "Unknown scoreboard objective 'nope'",
        "You can only trigger objectives that are 'trigger' type",
    ]
