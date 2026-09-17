"""The function debugger: breakpoints, stepping, watches, stopping."""

from __future__ import annotations

import pytest

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime.debugger import (
    Action,
    Debugger,
    DebugStopped,
    Trace,
    parse_location,
    watch_value,
)

PACK = {
    "data/test/function/tick.mcfunction": (
        "scoreboard players add #t n 1\n"  # 1
        "function test:inner\n"  # 2
        "scoreboard players add #t n 10\n"  # 3
    ),
    "data/test/function/inner.mcfunction": (
        "# a comment\n"  # 1
        "scoreboard players add #i n 1\n"  # 2
        "data modify storage test:s x set value 5b\n"  # 3
    ),
    "data/test/function/load.mcfunction": "scoreboard objectives add n dummy\n",
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
}


@pytest.fixture
def emulator(make_pack):
    emulator = Emulator(Datapack.load(make_pack(PACK)), version="1.21.4")
    emulator.start()
    emulator.run_tick()
    return emulator


def scripted(actions: list[Action], seen: list):
    def on_pause(pause):
        seen.append((pause.function_id, pause.line, pause.reason, len(pause.stack)))
        return actions.pop(0) if actions else Action.CONTINUE

    return on_pause


def test_breakpoints_and_stepping(emulator):
    seen: list = []
    debugger = Debugger(
        scripted([Action.STEP_INTO, Action.STEP_INTO, Action.STEP_OVER, Action.STEP_OVER], seen)
    )
    debugger.add("test:tick", 2)
    emulator.debugger = debugger
    emulator.run_tick()
    assert seen == [
        ("test:tick", 2, "breakpoint", 1),
        ("test:inner", 2, "step", 2),  # into the call; the comment is not a line
        ("test:inner", 3, "step", 2),
        ("test:tick", 3, "step", 1),  # stepping over the end returns to the caller
    ]
    assert emulator.world.scoreboard.get("#t", "n") == 22


def test_step_over_and_out(emulator):
    seen: list = []
    debugger = Debugger(scripted([Action.STEP_OVER, Action.CONTINUE], seen))
    debugger.add("test:tick", 1)
    emulator.debugger = debugger
    emulator.run_tick()
    assert seen == [("test:tick", 1, "breakpoint", 1), ("test:tick", 2, "step", 1)]

    seen.clear()
    debugger.clear()
    debugger.add("test:inner", 2)
    debugger.on_pause = scripted([Action.STEP_OUT], seen)
    emulator.run_tick()
    assert seen == [("test:inner", 2, "breakpoint", 2), ("test:tick", 3, "step", 1)]


def test_conditions_pause_requests_and_stop(emulator):
    seen: list = []
    debugger = Debugger(scripted([], seen))
    point = debugger.add("test:inner", 2, "score #i n matches 3")
    emulator.debugger = debugger
    for _ in range(3):
        emulator.run_tick()
    # #i was 1 after the first tick: the condition is checked before the line
    assert seen == [("test:inner", 2, "breakpoint", 2)]
    assert point.hits == 1
    commands = emulator.commands_run

    debugger.clear()
    debugger.request_pause()
    emulator.run_tick()
    assert seen[-1] == ("test:tick", 1, "pause", 1)
    assert emulator.commands_run >= commands

    debugger.add("test:tick", 3)
    debugger.on_pause = lambda pause: Action.STOP
    with pytest.raises(DebugStopped):
        emulator.run_tick()
    assert debugger.stack == []  # frames are left on the way out
    emulator.debugger = None
    emulator.run_tick()


def test_watches(emulator):
    context = emulator.root_context()
    assert watch_value("score #t n", context) == "11"
    assert watch_value("score #t missing", context) == "unset"
    assert watch_value("storage test:s x", context) == "5"
    assert watch_value("storage test:s", context) == "{x: 5}"
    assert watch_value("entity @p Health", context) == "20.0d"
    assert watch_value("if score #t n matches 11", context) == "passes"
    assert watch_value("unless score #t n matches 11", context) == "fails"
    assert watch_value("executor", context) == "the server"
    assert watch_value("block 0 0 0", context) == "minecraft:air"
    assert watch_value("dimension", context) == "minecraft:overworld"
    assert watch_value("nonsense", context).startswith("unknown watch")

    debugger = Debugger()
    debugger.watches = ["score #t n", "executor"]
    trace = Trace(debugger=debugger)
    debugger.on_pause = trace
    debugger.add("test:tick", 3)
    emulator.debugger = debugger
    emulator.run_tick()
    assert trace.lines == [
        "test:tick:3 (breakpoint test:tick:3) at tick 1: scoreboard players add #t n 10\n"
        "    score #t n = 12\n"
        "    executor = the server"
    ]


def test_locations_and_toggling():
    assert parse_location("test:a/b:12") == ("test:a/b", 12)
    assert parse_location("tick:3") == ("minecraft:tick", 3)
    for bad in ("test:a", "test:a:0", "test:a:x", ":3"):
        with pytest.raises(ValueError):
            parse_location(bad)
    debugger = Debugger()
    assert debugger.toggle("test:a", 2) is True
    assert debugger.lines_of("test:a") == {2}
    assert debugger.toggle("test:a", 2) is False
    assert debugger.lines_of("test:a") == set()
