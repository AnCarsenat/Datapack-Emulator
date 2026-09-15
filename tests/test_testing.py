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
    assert ticks.passed and ticks.value == 5
    assert "none is set" in by_command["scoreboard players get #nobody t"].reason
    assert "Unknown function" in by_command["function test:missing"].reason
    assert not by_command["# just a comment"].passed
    assert "say disabled" not in by_command  # disabled tests are skipped
    assert [r.test.command for r in results][0] == "function test:greet"  # input order kept


def test_command_tests_round_trip_through_dicts():
    test = CommandTest("function hat:tick", at_tick=3, expect="Hat", enabled=False)
    assert CommandTest.from_dict(test.to_dict()) == test
