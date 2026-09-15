from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.testing import CommandTest, run_tests


def _pack(make_pack):
    return Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
                "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
                "data/test/function/greet.mcfunction": "say hello\ntag @a remove never\n",
            }
        )
    )


def test_command_tests_pass_fail_and_run_at_their_tick(make_pack):
    results = run_tests(
        _pack(make_pack),
        [
            CommandTest("function test:greet", expect="hello"),
            CommandTest("say hi", expect="bye"),
            CommandTest("scoreboard players get #ticks t", at_tick=5),
            CommandTest("scoreboard players get #nobody t"),
            CommandTest("function test:missing"),
            CommandTest("# just a comment"),
            CommandTest("say disabled", enabled=False),
        ],
        version="1.21.4",
    )
    by_command = {result.test.command: result for result in results}

    # silent failures inside the called function do not fail the test
    assert by_command["function test:greet"].passed
    assert not by_command["say hi"].passed and "expected output" in by_command["say hi"].reason
    ticks = by_command["scoreboard players get #ticks t"]
    # tick 5 runs after that tick's functions: #minecraft:tick has run 6 times
    assert ticks.passed and ticks.value == 6
    assert "none is set" in by_command["scoreboard players get #nobody t"].reason
    assert "Unknown function" in by_command["function test:missing"].reason
    assert not by_command["# just a comment"].passed
    assert "say disabled" not in by_command  # disabled tests are skipped
    assert [r.test.command for r in results][0] == "function test:greet"  # input order kept


def test_command_tests_round_trip_through_dicts():
    test = CommandTest("function hat:tick", at_tick=3, expect="Hat", enabled=False)
    assert CommandTest.from_dict(test.to_dict()) == test


def test_any_hat_trigger_works_from_the_first_tick_as_a_player():
    """Reported: `trigger hat` failed with "You cannot trigger this objective yet"
    although it works in game, where chat commands run after the tick functions."""
    from pathlib import Path

    pack = Datapack.load(Path(__file__).parents[1] / "samples" / "hat")
    results = run_tests(
        pack,
        [
            CommandTest("execute as Player1 run trigger hat"),
            CommandTest("execute as Player1 run trigger hat", at_tick=3),
            CommandTest("trigger hat"),
            CommandTest("execute as Nobody run trigger hat"),
        ],
        version="26.2",
    )
    assert results[0].passed and results[1].passed, [r.reason for r in results]
    assert results[2].reason == "A player is required to run this command here"
    assert results[3].reason == "the command did not succeed"  # nobody to run as


def test_a_trigger_is_used_up_until_enabled_again(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
                "data/test/function/load.mcfunction": (
                    "scoreboard objectives add t trigger\nscoreboard players enable @a t\n"
                ),
            }
        )
    )
    results = run_tests(
        pack,
        [
            CommandTest("execute as @a run trigger t set 4"),
            CommandTest("execute as @a run trigger t"),
        ],
        version="1.21.4",
    )
    assert results[0].passed and "set value to 4" in results[0].records[-1].message
    assert results[1].reason == "You cannot trigger this objective yet"


def test_engine_runs_tests_in_every_version_and_reports_them(make_pack):
    from datapack_emulator.emulator.engine import TestEngine

    engine = TestEngine(
        _pack(make_pack),
        ticks=3,
        tests=[
            CommandTest("scoreboard players get #ticks t", at_tick=1),
            CommandTest("say late", at_tick=10),
            CommandTest("say off", enabled=False),
        ],
    )
    (run,) = engine.run(["1.21.4"])
    assert [result.passed for result in run.tests] == [True, False]
    assert run.tests[0].value == 2
    assert run.tests[1].reason == "not reached: the run ended before tick 10"
    assert run.tests_summary == "1/2" and run.status == "tests failed"
    assert "<td>1/2</td>" in TestEngine.to_html([run])
