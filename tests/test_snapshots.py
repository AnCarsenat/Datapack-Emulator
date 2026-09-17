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
    assert "+ block minecraft:overworld 0 1 0: minecraft:chest[facing=north] +nbt" in shown
    assert "+ objective other: dummy" in shown
    assert any("gamerule doDaylightCycle" in line for line in shown)
    assert compare(first, first, emulator.version) == []
    # the other way round reads as removals
    back = [change.format() for change in compare(second, first, emulator.version)]
    assert "- storage test:s x: 5" in back
