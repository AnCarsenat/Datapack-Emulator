"""World snapshots: keeping a world, putting it back, and comparing two."""

from __future__ import annotations

import pytest

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime.snapshot import capture, compare, restore

PACK = {
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
    "data/test/function/load.mcfunction": "scoreboard objectives add n dummy\n",
    "data/test/function/tick.mcfunction": "scoreboard players add #t n 1\n",
}


@pytest.fixture
def emulator(make_pack):
    emulator = Emulator(Datapack.load(make_pack(PACK)), version="1.21.4")
    emulator.start()
    emulator.run_tick()
    return emulator


def test_a_snapshot_is_put_back_and_the_world_goes_on(emulator):
    emulator.run_typed('summon pig 1 2 3 {Tags:["a"]}')
    emulator.run_typed("data modify storage test:s x set value 1")
    emulator.run_typed("setblock 0 1 0 stone")
    saved = capture(emulator, "before")
    emulator.run_typed("schedule function test:tick 5t")
    for _ in range(3):
        emulator.run_tick()
    emulator.run_typed("kill @e[type=pig]")
    emulator.run_typed("data modify storage test:s x set value 9")
    assert emulator.world.scoreboard.get("#t", "n") == 4
    assert not [e for e in emulator.world.entities if e.type == "minecraft:pig"]

    restore(emulator, saved)
    world = emulator.world
    assert world.tick == saved.tick and world.scoreboard.get("#t", "n") == 1
    assert [e.type for e in world.entities if not e.is_player] == ["minecraft:pig"]
    assert world.storage["test:s"] == {"x": 1}
    assert emulator.schedules == []
    # the restored world keeps ticking, and its scoreboard history follows it
    emulator.run_tick()
    assert world.scoreboard.get("#t", "n") == 2 and world.tick == saved.tick + 1
    # advancements still work after a restore (the tree is re-attached)
    assert world.advancements.tree is emulator._advancement_tree
    # the snapshot itself did not move
    assert saved.world.scoreboard.get("#t", "n") == 1
    restore(emulator, saved)
    assert emulator.world.scoreboard.get("#t", "n") == 1
    assert emulator.world is not saved.world


def test_comparing_two_snapshots(emulator):
    first = capture(emulator, "start")
    emulator.run_typed("summon pig 1 2 3")
    emulator.run_typed("data modify storage test:s x set value 5")
    emulator.run_typed("setblock 0 1 0 minecraft:chest[facing=north]")
    emulator.run_typed("gamerule doDaylightCycle false")
    emulator.run_typed("scoreboard objectives add other dummy")
    emulator.run_tick()
    second = capture(emulator, "later")
    changes = compare(first, second, emulator.version)
    shown = [change.format() for change in changes]
    assert "~ state game time: 1 -> 2" in shown
    assert "~ score #t n: 1 -> 2" in shown
    assert any(line.startswith("+ entity ") and "minecraft:pig" in line for line in shown)
    assert "+ storage test:s x: 5" in shown
    assert "+ block minecraft:overworld 0 1 0: minecraft:chest[facing=north] {Items: []}" in shown
    assert "+ objective other: dummy" in shown
    assert any("gamerule doDaylightCycle" in line for line in shown)
    assert compare(first, first, emulator.version) == []
    # the other way round reads as removals
    back = [change.format() for change in compare(second, first, emulator.version)]
    assert "- storage test:s x: 5" in back


def test_a_snapshot_does_not_drag_the_emulator_in(emulator):
    """The advancement callback is a bound method: copying it would copy the
    whole emulator (and its pack) with every snapshot."""
    import gc

    def emulators() -> int:
        gc.collect()
        return sum(1 for obj in gc.get_objects() if isinstance(obj, Emulator))

    before = emulators()
    saved = capture(emulator, "start")
    assert emulators() == before  # no second emulator was copied
    assert saved.world.advancements.on_complete is None
    restore(emulator, saved)
    assert emulators() == before


def test_an_absent_value_is_not_an_empty_one(emulator):
    emulator.run_typed('data modify storage test:s name set value ""')
    first = capture(emulator, "with")
    emulator.run_typed("data remove storage test:s name")
    second = capture(emulator, "without")
    shown = [change.format() for change in compare(first, second, emulator.version)]
    assert "- storage test:s name: " in shown  # removed, not added
    back = [change.format() for change in compare(second, first, emulator.version)]
    assert "+ storage test:s name: " in back


def test_comparing_sees_block_nbt_and_flattens_the_state(emulator):
    emulator.run_typed("setblock 0 1 0 chest")
    emulator.run_typed("team add red")
    first = capture(emulator, "first")
    emulator.run_typed("item replace block 0 1 0 container.0 with minecraft:diamond 5")
    emulator.run_typed("defaultgamemode creative")
    emulator.run_typed("team join red Player1")
    second = capture(emulator, "second")
    shown = [change.format() for change in compare(first, second, emulator.version)]
    assert any("block minecraft:overworld 0 1 0" in line and "diamond" in line for line in shown)
    assert "~ state default_game_mode: survival -> creative" in shown
    assert "~ state teams.red.members: [] -> ['Player1']" in shown


def test_a_rewind_puts_back_the_notes_and_the_profiler(emulator):
    saved = capture(emulator, "start")
    ticks, noted = emulator.profiler.ticks, set(emulator.noted)
    emulator.noted.add("test:something")
    for _ in range(3):
        emulator.run_tick()
    assert emulator.profiler.ticks == ticks + 3
    restore(emulator, saved)
    assert emulator.profiler.ticks == ticks  # the undone ticks are not counted
    assert emulator.noted == noted  # a diagnostic printed after it is said again


def test_comparing_with_a_live_world_does_not_copy_it(emulator):
    first = capture(emulator, "start")
    emulator.run_typed("scoreboard players set #x n 7")
    changes = compare(first, emulator.world, emulator.version)
    assert "+ score #x n: 7" in [change.format() for change in changes]


def test_a_rewind_and_a_replay_reproduce_the_run(emulator):
    saved = capture(emulator, "start")
    for _ in range(3):
        emulator.run_typed("summon pig ~ ~ ~")
        emulator.run_typed("execute store result score #r n run random value 1..1000")
        emulator.run_tick()
    first = capture(emulator, "after")

    restore(emulator, saved)
    for _ in range(3):
        emulator.run_typed("summon pig ~ ~ ~")
        emulator.run_typed("execute store result score #r n run random value 1..1000")
        emulator.run_tick()
    # the same uuids and the same random values: nothing comes from the process
    assert compare(first, emulator.world, emulator.version) == []
