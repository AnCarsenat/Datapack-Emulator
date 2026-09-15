from pathlib import Path

from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.runtime.inventory import (
    Inventory,
    ItemStack,
    parse_item,
    parse_item_predicate,
)

SAMPLES = Path(__file__).parents[1] / "samples"


def world(make_pack, body: str, version: str = "1.21.4", ticks: int = 1, extra=None, **kwargs):
    # both folder spellings, so versions before 1.21 run the function too
    files = {
        "data/test/function/tick.mcfunction": body,
        "data/test/functions/tick.mcfunction": body,
        "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
        "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        **(extra or {}),
    }
    emulator = Emulator(Datapack.load(make_pack(files)), version=version, **kwargs)
    emulator.run(ticks=ticks)
    return emulator


def typed(emulator, line: str):
    return emulator.run_command(Command.parse(line), emulator.root_context())


def feedback(emulator) -> list[str]:
    return [r.message for r in emulator.output.records if r.key.startswith("commands.")]


def test_parse_items_and_predicates():
    sword = parse_item("diamond_sword[enchantments={levels:{sharpness:2}},!food]{Damage:3}")
    assert sword.id == "minecraft:diamond_sword" and sword.max_count == 1
    assert sword.components == {"minecraft:enchantments": {"levels": {"sharpness": 2}}}
    assert sword.tag == {"Damage": 3}
    assert parse_item("air") is None and parse_item("stone").max_count == 64
    assert parse_item("ender_pearl").max_count == 16
    predicate = parse_item_predicate("#minecraft:logs[custom_data,count>1]")
    assert predicate.id == "#minecraft:logs" and predicate.present == {"minecraft:custom_data"}
    assert predicate.unchecked == ["count>1"]


def test_give_stacks_fills_then_drops_the_rest(make_pack):
    emulator = world(make_pack, "give @a minecraft:stone 100\ngive @a diamond_sword 2\n")
    player = emulator.world.players[0]
    inventory = player.inventory
    assert inventory.get("container.0").count == 64 and inventory.get("container.1").count == 36
    assert inventory.get("container.2").id == "minecraft:diamond_sword"
    assert inventory.get("container.3").id == "minecraft:diamond_sword"
    assert "Gave 100 [Stone] to Player1" in feedback(emulator)

    for _ in range(40):
        typed(emulator, "give Player1 minecraft:dirt 64")
    items = [e for e in emulator.world.entities if e.type == "minecraft:item"]
    assert items and items[0].nbt["Item"]["id"] == "minecraft:dirt"  # did not fit: dropped
    assert typed(emulator, "give Player1 stone 6401").success is False
    assert game_errors(emulator.output.records)[-1] == "Can't give more than 6400 of [Stone]"


def test_clear_counts_removes_and_reports(make_pack):
    emulator = world(
        make_pack,
        "give @a stone 70\ngive @a dirt 3\n",
        players=2,
    )
    assert typed(emulator, "clear Player1 stone 0").value == 70  # count only
    assert typed(emulator, "clear @a stone 5").value == 10
    assert typed(emulator, "clear Player1").value == 65 + 3
    assert emulator.world.players[0].inventory.slots == {}
    assert not typed(emulator, "clear Player1 stone").success
    messages = feedback(emulator)
    assert "Found 70 matching item(s) on player Player1" in messages
    assert "Removed 10 item(s) from 2 players" in messages
    assert "No items were found on player Player1" in game_errors(emulator.output.records)
    assert (
        game_errors(emulator.output.records)[0] != "A player is required to run this command here"
    )
    assert typed(emulator, "clear").success is False  # the console has no inventory


def test_item_replace_and_the_nbt_of_each_version(make_pack):
    body = (
        'summon minecraft:armor_stand 0 0 0 {Tags:["stand"]}\n'
        "give @a golden_helmet\n"
        "item replace entity @e[tag=stand] armor.head with minecraft:carved_pumpkin\n"
        "item replace entity @e[tag=stand] weapon.mainhand from entity Player1 hotbar.0\n"
        "item replace entity @a armor.chest with minecraft:elytra\n"
    )
    old = world(make_pack, body, version="1.20.4")
    stand = next(e for e in old.world.entities if "stand" in e.tags)
    data = stand.data(old.version)
    assert data["ArmorItems"][3] == {"id": "minecraft:carved_pumpkin", "Count": 1}
    assert data["HandItems"][0] == {"id": "minecraft:golden_helmet", "Count": 1}
    player = old.world.players[0].data(old.version)
    assert {"Slot": 102, "id": "minecraft:elytra", "Count": 1} in player["Inventory"]
    assert player["SelectedItem"] == {"id": "minecraft:golden_helmet", "Count": 1}

    middle = world(make_pack, body, version="1.21.4")
    stand = next(e for e in middle.world.entities if "stand" in e.tags)
    assert stand.data(middle.version)["HandItems"][0] == {
        "id": "minecraft:golden_helmet",
        "count": 1,
    }

    new = world(make_pack, body, version="1.21.5")
    stand = next(e for e in new.world.entities if "stand" in e.tags)
    equipment = stand.data(new.version)["equipment"]
    assert equipment["head"] == {"id": "minecraft:carved_pumpkin", "count": 1}
    assert new.world.players[0].data(new.version)["equipment"]["chest"]["id"] == "minecraft:elytra"


def test_selectors_conditions_and_data_see_items(make_pack):
    emulator = world(
        make_pack,
        "give @a diamond 3\n"
        'execute if entity @a[nbt={SelectedItem:{id:"minecraft:diamond"}}] run say holding\n'
        "execute if items entity @a weapon.mainhand minecraft:diamond run say mainhand\n"
        "execute store result score #n c run execute if items entity @a container.* diamond\n"
        'summon zombie 0 0 0 {Tags:["z"]}\n'
        'data merge entity @e[tag=z,limit=1] {HandItems:[{id:"minecraft:stick",count:1},{}]}\n'
        "execute if items entity @e[tag=z] weapon.* stick run say zombie\n",
        extra={"data/test/function/load.mcfunction": ""},
    )
    messages = chat(emulator.output.records)
    assert {"[Server] holding", "[Server] mainhand", "[Server] zombie"} <= set(messages)
    zombie = next(e for e in emulator.world.entities if "z" in e.tags)
    assert zombie.inventory.get("mainhand").id == "minecraft:stick"


def test_enchant_writes_each_versions_format(make_pack):
    for version, check in (
        (
            "1.20.4",
            lambda stack: stack.tag["Enchantments"] == [{"id": "minecraft:sharpness", "lvl": 2}],
        ),
        (
            "1.21.4",
            lambda stack: (
                stack.components["minecraft:enchantments"] == {"levels": {"minecraft:sharpness": 2}}
            ),
        ),
        (
            "1.21.5",
            lambda stack: stack.components["minecraft:enchantments"] == {"minecraft:sharpness": 2},
        ),
    ):
        emulator = world(make_pack, "give @a iron_sword\nenchant @a sharpness 2\n", version=version)
        assert check(emulator.world.players[0].inventory.get("container.0")), version
    empty = world(make_pack, "enchant Player1 sharpness\n")
    assert game_errors(empty.output.records) == ["Player1 is not holding any item"]


def test_loot_from_a_pack_table_and_killed_players_drop_items(make_pack):
    table = {
        "pools": [
            {
                "rolls": 1,
                "entries": [
                    {
                        "type": "minecraft:item",
                        "name": "minecraft:emerald",
                        "functions": [{"function": "minecraft:set_count", "count": 5}],
                    }
                ],
            }
        ]
    }
    emulator = world(
        make_pack,
        "loot give @a loot test:reward\nloot spawn 1 2 3 loot test:reward\n",
        extra={"data/test/loot_table/reward.json": table},
    )
    player = emulator.world.players[0]
    assert player.inventory.get("container.0").count == 5
    spawned = [e for e in emulator.world.entities if e.type == "minecraft:item"]
    assert spawned[0].position == [1.0, 2.0, 3.0] and spawned[0].nbt["Item"]["count"] == 5

    typed(emulator, "kill Player1")
    assert player.inventory.slots == {}
    assert len([e for e in emulator.world.entities if e.type == "minecraft:item"]) == 2
    typed(emulator, "gamerule keepInventory true")
    typed(emulator, "give Player1 stone")
    typed(emulator, "kill Player1")
    assert player.inventory.get("container.0").id == "minecraft:stone"


def test_any_hat_swaps_the_held_item_onto_the_head():
    """The sample pack's whole point: /trigger hat puts the held item on your head."""
    emulator = Emulator(Datapack.load(SAMPLES / "hat"), version="1.21.4")
    emulator.run(ticks=2)
    typed(emulator, "give Player1 minecraft:diamond_block")
    typed(emulator, "execute as Player1 run trigger hat")
    emulator.run_tick()
    player = emulator.world.players[0].inventory
    assert player.get("head").id == "minecraft:diamond_block"
    assert player.get("container.0") is None


def test_inventory_slot_names():
    player, mob = Inventory(player=True), Inventory(player=False)
    assert player.keys_for("hotbar.3") == ["container.3"]
    assert player.keys_for("inventory.0") == ["container.9"]
    assert player.keys_for("weapon") == ["container.0"] and mob.keys_for("weapon") == ["mainhand"]
    assert player.keys_for("inventory.27") is None and mob.keys_for("container.0") is None
    assert mob.keys_for("armor.body") == ["body"] and player.keys_for("armor.body") is None
    assert len(player.keys_for("container.*")) == 36
    stack = ItemStack.from_nbt({"id": "stone", "Count": 3, "tag": {"a": 1}})
    assert stack.count == 3 and stack.tag == {"a": 1} and ItemStack.from_nbt({}) is None


def test_nbt_paths_filter_list_elements(make_pack):
    from datapack_emulator.emulator.common import nbt_get, nbt_remove, nbt_set, split_path

    assert split_path("Inventory[{Slot:103b,tag:{a:[1,2]}}].id") == [
        "Inventory",
        "{Slot:103b,tag:{a:[1,2]}}",
        "id",
    ]
    data = {
        "Inventory": [{"Slot": 0, "id": "a"}, {"Slot": 103, "id": "b"}, {"Slot": 103, "id": "c"}]
    }
    assert nbt_get(data, "Inventory[{Slot:103b}].id") == "b"
    assert nbt_set(data, "Inventory[{Slot:0b}].id", "z") and data["Inventory"][0]["id"] == "z"
    assert nbt_remove(data, "Inventory[{Slot:103b}]") and len(data["Inventory"]) == 1

    emulator = world(
        make_pack,
        "item replace entity @a armor.head with minecraft:leather_helmet\n"
        "data modify storage hat:swap head set from entity Player1 Inventory[{Slot:103b}]\n",
        version="1.20.4",
    )
    assert emulator.world.storage["hat:swap"]["head"]["id"] == "minecraft:leather_helmet"
