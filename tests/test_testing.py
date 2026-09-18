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


def test_expected_values_are_ranges(make_pack):
    results = run_tests(
        _pack(make_pack),
        [
            CommandTest("scoreboard players get #ticks t", at_tick=2, expect_value="3"),
            CommandTest("scoreboard players get #ticks t", at_tick=2, expect_value="4.."),
            CommandTest("scoreboard players get #ticks t", at_tick=2, expect_value="1..3"),
            CommandTest("scoreboard players get #ticks t", at_tick=2, expect_value="x"),
        ],
        version="1.21.4",
    )
    assert [r.passed for r in results] == [True, False, True, False]
    assert results[1].reason == "value 3 is not 4 or more"
    assert results[3].reason == "invalid expected value 'x'"
    assert (
        CommandTest.from_dict(CommandTest("a", expect_value="1..").to_dict()).expect_value == "1.."
    )


def test_checks_look_at_the_world_and_explain_failures(make_pack):
    from datapack_emulator.emulator.testing import valid_check

    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "scoreboard players add #t n 1\n",
                "data/test/function/load.mcfunction": (
                    "scoreboard objectives add n dummy\n"
                    'summon pig 0 0 0 {Tags:["a"]}\n'
                    "data modify storage test:mem x set value 1b\n"
                    "data modify storage test:mem obj set value {a:1,b:{c:2}}\n"
                    "setblock 0 1 0 minecraft:oak_log[axis=x]\n"
                ),
                "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            }
        )
    )
    passing = CommandTest(
        "",
        at_tick=2,
        checks=[
            "score #t n = 3",
            "score #t n != 0",
            "storage test:mem x = 1",
            "storage test:mem obj = {a:1b,b:{c:2}}",
            'entity @e[type=pig,limit=1] Tags = ["a"]',
            "@e[type=pig] = 1",
            "@e[type=cow] = 0",
            "block 0 1 0 = oak_log[axis=x]",
            "block 0 2 0 != oak_log",
            "if entity @e[type=pig]",
            "unless entity @e[type=cow]",
            "entity @e[type=cow] Tags != 1",
        ],
    )
    failing = [
        CommandTest("", checks=["score #t n = 5"]),
        CommandTest("", checks=["storage test:mem obj = {a:2,b:{c:3},d:1}"]),
        CommandTest("", checks=["block 0 1 0 = oak_log[axis=y]"]),
        CommandTest("", checks=["@e[type=pig] = 2.."]),
        CommandTest("", checks=["if entity @e[type=cow]"]),
        CommandTest("", checks=["score #nobody n = 1"]),
        CommandTest("", checks=["nonsense"]),
        CommandTest("say hi", checks=["score #t n != 1"]),
    ]
    results = run_tests(pack, [passing, *failing], version="1.21.4")
    assert results[0].passed, results[0].reason
    assert results[0].reason == "passed (checks only), 12 check(s) held"
    reasons = [result.reason for result in results[1:]]
    assert not any(result.passed for result in results[1:])
    assert reasons[0] == "score #t n: expected exactly 5, got 1"
    assert reasons[1] == (
        "storage test:mem obj: expected {a: 2, b: {c: 3}, d: 1}, got {a: 1, b: {c: 2}} "
        "(a is 1; b.c is 2; missing d)"
    )
    assert reasons[2] == "block 0 1 0: expected oak_log[axis=y], got minecraft:oak_log[axis=x]"
    assert reasons[3] == "@e[type=pig] count: expected 2 or more, got 1"
    assert reasons[4] == "check failed: if entity @e[type=cow]"
    assert reasons[5] == "score #nobody n: expected exactly 1, but it is not set"
    assert reasons[6] == "check 'nonsense' needs ' = ' or ' != ' with spaces around it"
    assert reasons[7] == "score #t n: expected not exactly 1, got 1"
    assert valid_check("score a = 1") == "check 'score a = 1': score HOLDER OBJECTIVE = RANGE"
    assert "not a whole-number range" in valid_check("score a b = x")
    assert valid_check("if entity @a") == ""
    assert "@s is nobody here" in valid_check("if entity @s")
    round_trip = CommandTest.from_dict(passing.to_dict())
    assert round_trip.checks == passing.checks
    assert CommandTest.from_dict({"command": "x", "checks": "a = 1"}).checks == ["a = 1"]
    assert CommandTest.from_dict({"command": "x", "checks": [None, 5, " b "]}).checks == ["b"]
    assert CommandTest.from_dict({"command": "x", "checks": 3}).checks == []


def test_check_parsing_edge_cases(make_pack):
    from datapack_emulator.emulator import Emulator
    from datapack_emulator.emulator.testing import check_world, snbt_value, valid_check

    pack = Datapack.load(
        make_pack(
            {
                "data/test/tags/block/mine.json": {"values": ["minecraft:stone"]},
                "data/test/function/tick.mcfunction": "\n",
                "data/test/function/side.mcfunction": "scoreboard players add #calls n 1\n",
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4", players=2)
    emulator.start()
    emulator.run_tick()
    for line in (
        "scoreboard objectives add n dummy",
        "scoreboard players set Player1 n 1",
        "data modify storage test:m big set value 1000000",
        'data modify storage test:m "k k" set value 7',
        'data modify storage test:m items set value [{id:"a b",Count:1b}]',
        "data modify storage test:m half set value -0.5d",
        'summon pig 0 0 0 {Tags:["p"],CustomName:"a = b"}',
        "setblock 0 5 0 stone",
    ):
        emulator.run_typed(line)

    def holds(line):
        return check_world(emulator, line)

    assert holds("block 0 5 0 = #test:mine") == (True, "")
    assert holds("block 0 6 0 = #test:mine")[0] is False
    assert "block tag" in holds("block 0 5 0 = #test:nothing")[1]
    assert holds("storage test:m big = 1000001")[0] is False
    assert holds("storage test:m big == 1000000") == (True, "")
    assert holds('storage test:m "k k" = 7') == (True, "")
    assert holds('storage test:m items[{id:"a b"}].Count = 1b') == (True, "")
    assert holds("storage test:m half = -.5") == (True, "")
    assert holds('entity @e[type=pig, limit=1] Tags = ["p"]') == (True, "")
    assert holds("@e[type=pig, tag=p] = 1") == (True, "")
    assert valid_check('@e[type=pig,name="a = b"] = 0') == ""
    assert "is 2 holders" in holds("score @a n = 1")[1]
    assert "cannot be a condition" in holds("if function test:side")[1]
    assert emulator.world.scoreboard.get("#calls", "n") is None
    assert "not an NBT value" in valid_check("storage test:m obj = {a:1")
    assert "whole-number range" in valid_check("score #x n = 1.5")
    assert "@s is nobody" in valid_check("@s = 1")
    assert "with spaces" in valid_check("score #x n=1")
    assert valid_check("if\tentity @a") == ""
    assert snbt_value("hello") == "hello" and snbt_value("[I;1,2]") == [1, 2]
