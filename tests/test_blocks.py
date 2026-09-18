"""Blocks: setblock, fill, clone, block conditions, block data and containers."""

from __future__ import annotations

import pytest
from conftest import chat, visible_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime.blocks import (
    Block,
    container_size,
    parse_block,
    parse_block_predicate,
    world_height,
)
from datapack_emulator.emulator.versions import parse


@pytest.fixture
def world(make_pack):
    """An emulator on 1.21.4 whose typed commands return (result, records)."""
    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "\n"}))

    def make(version: str = "1.21.4", vanilla=None):
        emulator = Emulator(pack, version=version, vanilla=vanilla)
        emulator.start()
        emulator.run_tick()

        def typed(line: str):
            before = len(emulator.output.records)
            result, _ = emulator.run_typed(line)
            return result, emulator.output.records[before:]

        emulator.typed = typed
        return emulator

    return make


def test_block_states_and_predicates_parse():
    block = parse_block('minecraft:chest[facing=north]{Items:[{Slot:1b,id:"stone",count:2}]}')
    assert block.id == "minecraft:chest" and block.properties == {"facing": "north"}
    assert block.items[1].id == "minecraft:stone" and block.items[1].count == 2
    assert parse_block("stone[bad]") is None and parse_block("#minecraft:logs") is None
    predicate = parse_block_predicate("#minecraft:logs[axis=y]")
    assert predicate.is_tag and predicate.properties == {"axis": "y"}
    assert container_size("minecraft:red_shulker_box") == 27 and container_size("stone") == 0
    assert world_height("minecraft:overworld", parse("1.17.1")) == (0, 255)
    assert world_height("minecraft:overworld", parse("1.18")) == (-64, 319)


def test_setblock_places_replaces_and_keeps(world):
    emulator = world()
    result, records = emulator.typed("setblock 1 2 3 stone")
    assert result.success and chat(records) == ["Changed the block at 1, 2, 3"]
    assert emulator.world.blocks.get("minecraft:overworld", (1, 2, 3)).id == "minecraft:stone"
    result, records = emulator.typed("setblock 1 2 3 stone")
    assert not result.success and visible_errors(records) == ["Could not set the block"]
    result, _ = emulator.typed("setblock 1 2 3 dirt keep")
    assert not result.success
    assert emulator.typed("setblock ~ ~ ~1 oak_stairs[facing=east]")[0].success
    block = emulator.world.blocks.get("minecraft:overworld", (0, 0, 1))
    assert block.state == "minecraft:oak_stairs[facing=east]"
    result, records = emulator.typed("setblock 0 400 0 stone")
    assert visible_errors(records) == ["That position is out of this world!"]
    assert emulator.typed("setblock 1 2 3 air")[0].success
    assert len(emulator.world.blocks) == 1  # air is not stored


def test_fill_modes_filters_and_limit(world):
    emulator = world()
    result, records = emulator.typed("fill 0 0 0 2 2 2 stone")
    assert result.value == 27 and chat(records) == ["Successfully filled 27 block(s)"]
    assert emulator.typed("fill 0 0 0 2 2 2 dirt hollow")[0].value == 27
    blocks = emulator.world.blocks
    assert blocks.get("minecraft:overworld", (1, 1, 1)).is_air
    assert blocks.get("minecraft:overworld", (0, 1, 1)).id == "minecraft:dirt"
    assert emulator.typed("fill 0 0 0 2 2 2 glass outline")[0].value == 26
    assert emulator.typed("fill 0 0 0 2 2 2 gold_block replace glass")[0].value == 26
    assert emulator.typed("fill 0 0 0 2 2 2 iron_block keep")[0].value == 1
    result, records = emulator.typed("fill 0 0 0 2 2 2 iron_block keep")
    assert visible_errors(records) == ["No blocks were filled"]
    result, records = emulator.typed("fill 0 0 0 40 40 40 stone")
    assert visible_errors(records) == [
        "Too many blocks in the specified area (maximum 32768, specified 68921)"
    ]
    emulator.typed("gamerule commandModificationBlockLimit 100000")
    assert emulator.typed("fill 0 0 0 40 40 40 stone")[0].value == 68921


def test_clone_copies_moves_and_refuses_overlaps(world):
    emulator = world()
    emulator.typed('setblock 0 0 0 chest{Items:[{Slot:0b,id:"diamond",count:3}]}')
    emulator.typed("setblock 1 0 0 stone")
    result, records = emulator.typed("clone 0 0 0 1 0 0 10 0 0")
    assert result.value == 2 and chat(records) == ["Successfully cloned 2 block(s)"]
    copied = emulator.world.blocks.get("minecraft:overworld", (10, 0, 0))
    assert copied.items[0].count == 3
    result, records = emulator.typed("clone 0 0 0 1 0 0 1 0 0")
    assert visible_errors(records) == ["The source and destination areas cannot overlap"]
    assert emulator.typed("clone 0 0 0 1 0 0 20 0 0 masked move")[0].value == 2
    assert emulator.world.blocks.get("minecraft:overworld", (0, 0, 0)).is_air
    assert emulator.typed("clone 20 0 0 21 0 0 30 0 0 filtered stone")[0].value == 1
    assert emulator.typed(
        "clone from minecraft:overworld 30 0 0 31 0 0 to minecraft:the_nether 0 10 0"
    )[0].success
    assert emulator.world.blocks.get("minecraft:the_nether", (1, 10, 0)).id == "minecraft:stone"


def test_execute_if_block_and_blocks(world):
    emulator = world()
    emulator.typed("setblock 0 0 0 oak_log[axis=y]")
    assert emulator.typed("execute if block 0 0 0 oak_log")[0].value == 1
    assert emulator.typed("execute if block 0 0 0 oak_log[axis=y]")[0].success
    assert not emulator.typed("execute if block 0 0 0 oak_log[axis=x]")[0].success
    assert emulator.typed("execute unless block 0 0 0 stone")[0].success
    assert emulator.typed("execute if block 5 5 5 air")[0].success
    emulator.typed("setblock 10 0 0 oak_log[axis=y]")
    assert emulator.typed("execute if blocks 0 0 0 0 0 0 10 0 0 all")[0].value == 1
    emulator.typed("setblock 1 0 0 dirt")
    assert emulator.typed("execute if blocks 0 0 0 1 0 0 10 0 0 all")[0].value == 0
    assert emulator.typed("execute if blocks 0 0 0 0 0 0 10 0 0 masked")[0].value == 1
    # a property the block was never given does not match, and says why
    emulator.typed("setblock 0 1 0 oak_stairs")
    _, records = emulator.typed("execute if block 0 1 0 oak_stairs[facing=north]")
    assert any("default block states are not" in r.message for r in records)


def test_block_tags_come_from_the_pack(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/tags/block/soft.json": {"values": ["minecraft:dirt", "#test:sand"]},
                "data/test/tags/block/sand.json": {"values": ["minecraft:sand"]},
                "data/test/function/tick.mcfunction": "\n",
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed("setblock 0 0 0 sand")
    assert emulator.run_typed("execute if block 0 0 0 #test:soft")[0].success
    emulator.run_typed("setblock 0 0 0 stone")
    assert not emulator.run_typed("execute if block 0 0 0 #test:soft")[0].success
    assert emulator.run_typed("fill 0 0 0 0 0 0 glass replace #test:soft")[0].value == 0


def test_data_on_block_entities(world):
    emulator = world()
    emulator.typed("setblock 0 0 0 stone")
    result, records = emulator.typed("data get block 0 0 0")
    assert visible_errors(records) == ["The target block is not a block entity"]
    emulator.typed('setblock 0 0 0 barrel{CustomName:"box"}')
    result, records = emulator.typed("data get block 0 0 0")
    assert chat(records) == [
        '0, 0, 0 has the following block data: {x: 0, y: 0, z: 0, id: "minecraft:barrel", '
        'CustomName: "box", Items: []}'
    ]
    assert emulator.typed('data merge block 0 0 0 {Items:[{Slot:4b,id:"apple",count:5}]}')[
        0
    ].success
    block = emulator.world.blocks.get("minecraft:overworld", (0, 0, 0))
    assert block.items[4].count == 5
    result, records = emulator.typed("data get block 0 0 0 Items[0].count 2")
    assert result.value == 10
    assert chat(records) == ["Items[0].count in block 0, 0, 0 after scale factor of 2 is 10"]
    assert emulator.typed("data modify block 0 0 0 Items[0].count set value 7")[0].success
    assert block.items[4].count == 7
    assert emulator.typed("data modify storage t:s v set from block 0 0 0 Items[0].id")[0].success
    assert emulator.world.storage["t:s"] == {"v": "minecraft:apple"}
    assert emulator.typed("data modify storage t:s n set string block 0 0 0 id 10")[0].success
    assert emulator.world.storage["t:s"]["n"] == "barrel"
    assert emulator.typed("data remove block 0 0 0 CustomName")[0].success
    assert "CustomName" not in block.nbt
    assert emulator.typed("execute if data block 0 0 0 Items[0]")[0].success
    assert not emulator.typed("execute if data block 0 0 1 Items")[0].success
    assert emulator.typed(
        "execute store result block 0 0 0 Items[0].count byte 1 run scoreboard objectives list"
    )[0].success
    assert 4 not in block.items  # a stack of 0 is gone


def test_items_in_containers(world):
    emulator = world()
    emulator.typed("setblock 0 0 0 chest")
    result, records = emulator.typed("item replace block 0 0 0 container.3 with diamond 4")
    assert chat(records) == ["Replaced a slot at 0, 0, 0 with [Diamond]"]
    assert emulator.typed("execute if items block 0 0 0 container.* diamond")[0].value == 4
    assert emulator.typed("item replace entity Player1 weapon from block 0 0 0 container.3")[
        0
    ].success
    assert emulator.world.players[0].inventory.get("container.0").id == "minecraft:diamond"
    result, records = emulator.typed("item replace block 0 0 0 container.30 with stone")
    assert visible_errors(records) == ["The target does not have slot 30"]
    result, records = emulator.typed("item replace block 5 5 5 container.0 with stone")
    assert visible_errors(records) == ["Target position 5, 5, 5 is not a container"]
    assert emulator.typed("replaceitem block 0 0 0 container.1 stone 2")[0].success is False  # 1.21
    old = world("1.16.5")
    old.typed("setblock 0 0 0 chest")
    assert old.typed("replaceitem block 0 0 0 container.1 stone 2")[0].success
    assert old.world.blocks.get("minecraft:overworld", (0, 0, 0)).items[1].count == 2


def test_loot_into_and_out_of_blocks(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/loot_table/two.json": {
                    "pools": [{"rolls": 1, "entries": [{"type": "item", "name": "apple"}]}],
                    "functions": [{"function": "set_count", "count": 2}],
                },
                "data/minecraft/loot_table/blocks/stone.json": {
                    "pools": [{"rolls": 1, "entries": [{"type": "item", "name": "cobblestone"}]}]
                },
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed("setblock 0 0 0 hopper")
    assert emulator.run_typed("loot insert 0 0 0 loot test:two")[0].value == 1
    hopper = emulator.world.blocks.get("minecraft:overworld", (0, 0, 0))
    assert hopper.items[0].count == 2
    assert emulator.run_typed("loot replace block 0 0 0 container.2 loot test:two")[0].success
    assert hopper.items[2].count == 2
    emulator.run_typed("setblock 1 0 0 stone")
    assert emulator.run_typed("loot give Player1 mine 1 0 0 mainhand")[0].success
    assert emulator.world.players[0].inventory.get("container.0").id == "minecraft:cobblestone"
    assert emulator.run_typed("loot spawn 0 0 0 kill Player1")[0].success is False  # no table
    assert not emulator.run_typed("loot insert 9 9 9 loot test:two")[0].success


def test_macros_and_text_read_blocks(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/function/show.mcfunction": "$say $(CustomName)\n",
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed('setblock 0 0 0 barrel{CustomName:"crate"}')
    before = len(emulator.output.records)
    emulator.run_typed("function test:show with block 0 0 0")
    emulator.run_typed('tellraw @a {"nbt":"CustomName","block":"0 0 0"}')
    assert chat(emulator.output.records[before:]) == [
        "[Server] crate",
        "to Player1: crate",
    ]
    emulator.run_typed("function test:show with block 3 3 3")
    assert "The target block is not a block entity" in [
        r.message for r in emulator.output.records[before:]
    ]


def test_block_ids_are_checked_and_unlisted_properties_noted(world, fake_jar):
    from datapack_emulator.emulator.vanilla import VanillaAssets

    assets = VanillaAssets.from_jar(fake_jar)
    assets._block_properties["minecraft:stone"] = {"variant": {"smooth"}}
    emulator = world(vanilla=assets)
    _, records = emulator.typed("setblock 0 0 0 dirt")
    assert visible_errors(records) == ["Unknown block type 'minecraft:dirt'"]
    # blockstates files leave out properties like waterlogged: only a note
    result, records = emulator.typed("setblock 0 0 0 stone[waterlogged=true]")
    assert result.success
    assert any("not in the client jar's blockstates" in r.message for r in records)
    assert emulator.typed("setblock 0 0 0 stone[variant=smooth]")[0].success


def test_block_properties_are_read_from_blockstates(tmp_path):
    import json
    import zipfile

    from datapack_emulator.emulator.vanilla import VanillaAssets

    jar = tmp_path / "client.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": "9.9"}))
        archive.writestr(
            "assets/minecraft/blockstates/oak_stairs.json",
            json.dumps({"variants": {"facing=east,half=top": {}, "facing=west,half=top": {}}}),
        )
        archive.writestr(
            "assets/minecraft/blockstates/fence.json",
            json.dumps(
                {
                    "multipart": [
                        {"when": {"north": "true"}},
                        {"when": {"OR": [{"east": "true|low"}]}},
                    ]
                }
            ),
        )
    assets = VanillaAssets.from_jar(jar)
    assert assets.block_properties("oak_stairs") == {"facing": {"east", "west"}, "half": {"top"}}
    assert assets.block_properties("fence") == {"north": {"true"}, "east": {"true", "low"}}
    assert assets.block_properties("missing") is None


def test_block_entities_render_items_in_the_version_format():
    block = Block("minecraft:chest")
    block.apply_data({"Items": [{"Slot": 0, "id": "minecraft:stone", "Count": 3}]})
    assert block.data(parse("1.20.4"))["Items"] == [
        {"Slot": 0, "id": "minecraft:stone", "Count": 3}
    ]
    assert block.data(parse("1.21.4"))["Items"] == [
        {"Slot": 0, "id": "minecraft:stone", "count": 3}
    ]


def test_execute_align_floors_the_position(world):
    emulator = world()
    assert emulator.typed("execute positioned 1.7 -0.5 2.2 align xz run setblock ~ ~ ~ stone")[
        0
    ].success
    assert emulator.world.blocks.get("minecraft:overworld", (1, -1, 2)).id == "minecraft:stone"


def test_air_empties_slots_and_mining_drops_container_contents(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/minecraft/loot_table/blocks/shulker_box.json": {
                    "pools": [
                        {
                            "rolls": 1,
                            "entries": [{"type": "dynamic", "name": "minecraft:contents"}],
                        }
                    ]
                },
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed("item replace entity Player1 armor.head with diamond_helmet")
    emulator.run_typed("item replace entity Player1 armor.head with air")
    assert emulator.world.players[0].inventory.get("head") is None
    # an item without a Slot goes to slot 0, like NBT's getByte
    emulator.run_typed("setblock 0 0 0 shulker_box")
    emulator.run_typed('data modify block 0 0 0 Items append value {id:"minecraft:apple",count:3}')
    result, _ = emulator.run_typed("loot give Player1 mine 0 0 0 minecraft:stick")
    assert result.value == 1
    assert emulator.world.players[0].inventory.get("container.0").count == 3


def test_coordinates_are_checked_like_vanilla(world):
    emulator = world()
    for line in ("setblock nan 0 0 stone", "setblock inf 0 0 stone", "setblock 1.5 0 0 stone"):
        _, records = emulator.typed(line)
        assert visible_errors(records) == ["Incorrect argument for command"]
    _, records = emulator.typed("setblock ~ ~ ^ stone")
    assert visible_errors(records) == [
        "Cannot mix world & local coordinates (everything must either use ^ or not)"
    ]
    _, records = emulator.typed("setblock 30000000 0 0 stone")
    assert visible_errors(records) == ["That position is out of this world!"]
    assert emulator.typed("setblock ~1.5 ~ ~-.5 stone")[0].success
    _, records = emulator.typed('tellraw @a {"nbt":"x","block":"nan 0 0"}')
    assert chat(records) == ["to Player1: "]
    # a pack's own dimension is overworld-like
    assert emulator.typed("execute in test:dim run setblock 0 -60 0 stone")[0].success
    assert not emulator.typed("execute in minecraft:the_nether run setblock 0 -60 0 stone")[
        0
    ].success


def test_setblock_compares_states_and_empties_containers(world):
    emulator = world()
    emulator.typed('setblock 0 0 0 chest{Items:[{Slot:0b,id:"stone",count:1}]}')
    _, records = emulator.typed('setblock 0 0 0 chest{Items:[{Slot:1b,id:"dirt",count:1}]}')
    assert visible_errors(records) == ["Could not set the block"]
    # the old items are gone even though the command failed, as in vanilla
    assert emulator.world.blocks.get("minecraft:overworld", (0, 0, 0)).items == {}


def test_clone_counts_every_block_placed(world):
    emulator = world()
    emulator.typed("fill 0 0 0 1 0 0 stone")
    assert emulator.typed("clone 0 0 0 1 0 0 10 0 0")[0].value == 2
    assert emulator.typed("clone 0 0 0 1 0 0 10 0 0")[0].value == 2
    assert emulator.typed("clone 0 0 0 1 0 0 1 0 0 replace force")[0].value == 2
    _, records = emulator.typed("clone 0 0 0 1 0 0 20 0 0 filtered")
    assert visible_errors(records) == ["Unknown or incomplete command. See below for error"]


def test_loot_insert_fills_like_vanilla(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/loot_table/one.json": {
                    "pools": [{"rolls": 1, "entries": [{"type": "item", "name": "stone"}]}]
                },
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed('setblock 0 0 0 hopper{Items:[{Slot:1b,id:"stone",count:1}]}')
    assert emulator.run_typed("loot insert 0 0 0 loot test:one")[0].value == 1
    hopper = emulator.world.blocks.get("minecraft:overworld", (0, 0, 0))
    assert hopper.items[0].count == 1 and hopper.items[1].count == 1
    for slot in range(5):
        emulator.run_typed(f"item replace block 0 0 0 container.{slot} with dirt 64")
    assert emulator.run_typed("loot insert 0 0 0 loot test:one")[0].value == 0


def test_block_entity_types_and_shapes(world):
    emulator = world()
    emulator.typed("setblock 0 0 0 oak_sign")
    _, records = emulator.typed("data get block 0 0 0 id")
    assert chat(records) == ['0, 0, 0 has the following block data: "minecraft:sign"']
    emulator.typed("setblock 1 0 0 red_shulker_box")
    data = emulator.world.blocks.get("minecraft:overworld", (1, 0, 0)).data(None)
    assert data == {"id": "minecraft:shulker_box"}  # no empty Items for shulker boxes
    emulator.typed("setblock 2 0 0 jukebox")
    assert emulator.typed("item replace block 2 0 0 container.0 with music_disc_cat")[0].success
    assert "RecordItem" in emulator.world.blocks.get("minecraft:overworld", (2, 0, 0)).data(None)
    for block in ("lectern", "campfire"):
        emulator.typed(f"setblock 3 0 0 {block}")
        _, records = emulator.typed("item replace block 3 0 0 container.0 with stone")
        assert visible_errors(records) == ["Target position 3, 0, 0 is not a container"]
        assert emulator.typed("data get block 3 0 0")[0].success
    emulator.typed("setblock 4 0 0 piston_head")
    assert not emulator.typed("data get block 4 0 0")[0].success
    assert emulator.typed('execute if block 0 0 0 oak_sign{id:"minecraft:sign"}')[0].success


def test_strict_mode_and_the_limit_rule_follow_the_version(world):
    old = world("1.21.4")
    assert not old.typed("setblock 0 0 0 stone strict")[0].success
    assert not old.typed("fill 0 0 0 1 1 1 stone replace air hollow")[0].success
    new = world("1.21.5")
    assert new.typed("setblock 0 0 0 stone strict")[0].success
    assert new.typed("fill 0 0 0 2 2 2 dirt replace air hollow")[0].value == 25
    assert new.typed("clone 0 0 0 2 2 2 10 0 0 strict masked")[0].value == 27 - 1
    legacy = world("1.16.5")
    legacy.typed("gamerule commandModificationBlockLimit 10")
    assert legacy.typed("fill 0 0 0 9 9 9 stone")[0].value == 1000


def test_block_conditions_and_stores_report_errors(world):
    emulator = world()
    _, records = emulator.typed("execute store result block 9 9 9 Foo int 1 run say hi")
    assert visible_errors(records) == ["The target block is not a block entity"]
    assert "[Server] hi" not in chat(records)
    _, records = emulator.typed("execute if data block 9 9 9 Items")
    assert "The target block is not a block entity" in visible_errors(records)
    _, records = emulator.typed("execute if items block 9 9 9 container.* stone")
    assert "Source position 9, 9, 9 is not a container" in visible_errors(records)
    _, records = emulator.typed("execute if block 1 2 3")
    assert "Unknown or incomplete command. See below for error" in visible_errors(records)
    emulator.typed("setblock 0 0 0 chest")
    assert emulator.typed("item modify block 0 0 0 container.0 test:missing")[0].success is False
