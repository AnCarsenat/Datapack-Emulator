"""Chunks declared unloaded for a run: ``execute if loaded``, selectors and
block commands there (every other chunk counts as loaded)."""

from __future__ import annotations

import json

import pytest

from datapack_emulator import agent
from datapack_emulator.cli import main
from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime.world import parse_chunks
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


@pytest.fixture
def emulator(make_pack):
    """A started 1.21.4 world in which chunk 2,0 (blocks x 32..47, z 0..15) is unloaded."""
    pack = make_pack({"data/test/function/tick.mcfunction": ""})
    world = Emulator(Datapack.load(pack), version="1.21.4", unloaded_chunks=[[2, 0]])
    world.start()
    world.run_tick()
    return world


def typed(emulator, line):
    result, _ = emulator.run_typed(line)
    return result


def test_parse_chunks_forms():
    assert parse_chunks([[1, -2], "3,4", "0,0,the_nether", (5, 6, "minecraft:the_end")]) == {
        "minecraft:overworld": {(1, -2), (3, 4)},
        "minecraft:the_nether": {(0, 0)},
        "minecraft:the_end": {(5, 6)},
    }
    for bad in (["1"], [[1, 2, 3, 4]], [["a", 1]], [[1.5, 2]], ["1,2,3,4"]):
        with pytest.raises(ValueError):
            parse_chunks(bad)


def test_every_chunk_is_loaded_by_default(make_pack):
    pack = make_pack({"data/test/function/tick.mcfunction": ""})
    world = Emulator(Datapack.load(pack), version="1.21.4")
    assert typed(world, "execute if loaded 1000000 64 -1000000").success


def test_if_loaded_is_false_in_an_unloaded_chunk(emulator):
    assert not typed(emulator, "execute if loaded 40 64 8").success
    assert typed(emulator, "execute unless loaded 47 -100 15").success
    assert typed(emulator, "execute if loaded 48 64 8").success  # chunk 3,0
    assert typed(emulator, "execute if loaded 31 64 8").success  # chunk 1,0
    # another dimension is not affected
    assert typed(emulator, "execute in minecraft:the_nether if loaded 40 64 8").success
    assert not typed(emulator, "execute positioned 40 64 8 if loaded ~ ~ ~").success


def test_force_loading_loads_the_chunk_again(emulator):
    assert typed(emulator, "forceload add 40 8").success
    assert typed(emulator, "execute if loaded 40 64 8").success


def test_entities_in_unloaded_chunks_are_out_of_reach(emulator):
    typed(emulator, "summon minecraft:pig 40 64 8 {Tags:[far]}")
    typed(emulator, "summon minecraft:pig 0 64 0 {Tags:[near]}")
    assert typed(emulator, "execute if entity @e[type=pig,tag=near]").success
    assert not typed(emulator, "execute if entity @e[type=pig,tag=far]").success
    assert typed(emulator, "execute if entity @e[type=pig]").value == 1
    # players are never hidden: a player keeps its own chunk loaded
    typed(emulator, "tp @a 40 64 8")
    assert typed(emulator, "execute if entity @a").success
    # loading the chunk brings the pig back
    typed(emulator, "forceload add 40 8")
    assert typed(emulator, "execute if entity @e[type=pig,tag=far]").success


def test_block_commands_fail_in_unloaded_chunks(emulator):
    for line in (
        "setblock 40 64 8 minecraft:stone",
        "fill 40 64 8 41 64 8 minecraft:stone",
        "fill 0 64 0 40 64 0 minecraft:stone",  # a corner in the chunk
        "execute if block 40 64 8 minecraft:air",
        "data get block 40 64 8",
        "clone 0 64 0 1 64 0 40 64 8",
        "clone 30 64 0 34 64 0 0 70 0",  # the source reaches into the chunk
        "execute if blocks 0 64 0 0 64 0 40 64 8 all",
        "item replace block 40 64 8 container.0 with minecraft:stone",
        "loot spawn 0 64 0 mine 40 64 8",
        "execute store result block 40 64 8 Items[0].count int 1 run seed",
    ):
        emulator.run_tick()  # the same error twice in a tick is only shown once
        before = len(emulator.output.records)
        assert not typed(emulator, line).success, line
        said = emulator.output.records[before:]
        assert said[0].key == "argument.pos.unloaded", line
        assert said[0].message.startswith("That position is not loaded")
    assert emulator.world.blocks.get("minecraft:overworld", (40, 64, 8)).is_air
    # outside it, everything works as before; a spawn point needs no loaded chunk
    assert typed(emulator, "setblock 48 64 8 minecraft:stone").success
    assert typed(emulator, "setworldspawn 40 64 8").success


def test_agent_run_and_test_take_unloaded_chunks(make_pack):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": (
                "scoreboard objectives add t dummy\n"
                "execute store success score #loaded t if loaded 40 64 8\n"
            ),
        }
    )
    result = agent.run(
        pack,
        "1.21.4",
        ticks=1,
        unloaded_chunks=[[2, 0]],
        commands=["scoreboard players get #loaded t"],
    )
    assert result["commands"][0]["value"] == 0
    assert result["unloaded_chunks"] == [[2, 0, "minecraft:overworld"]]
    assert agent.run(pack, "1.21.4", ticks=1)["world"]["scores"]["#loaded"]["t"] == 1
    tested = agent.test(pack, ["1:"], checks=["score #loaded t = 0"], unloaded_chunks=["2,0"])
    assert tested["ok"], tested
    with pytest.raises(agent.AgentError):
        agent.run(pack, "1.21.4", unloaded_chunks=[["x", 0]])
    with pytest.raises(agent.AgentError):
        agent.run(pack, "1.21.4", unloaded_chunks="2,0")


def test_cli_unloaded_chunk_option(make_pack, capsys):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": (
                "scoreboard objectives add t dummy\n"
                "execute store success score #loaded t if loaded -8 64 8\n"
            ),
        }
    )
    code = main(
        ["run", str(pack), "--version", "1.21.4", "--ticks", "1", "--unloaded-chunk=-1,0", "--json"]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["world"]["scores"]["#loaded"]["t"] == 0
    assert data["unloaded_chunks"] == [[-1, 0, "minecraft:overworld"]]
    code = main(
        [
            "test",
            str(pack),
            "--version",
            "1.21.4",
            "--unloaded-chunk=-1,0",
            "--test",
            "1:",
            "--check",
            "score #loaded t = 0",
            "--json",
        ]
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["ok"]
    with pytest.raises(SystemExit):
        main(["run", str(pack), "--unloaded-chunk", "1;2"])
