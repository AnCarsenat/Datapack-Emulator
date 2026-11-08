"""``*[custom_data~{…}]`` and the other item sub-predicates."""

from datapack_emulator.emulator import Datapack, Emulator, versions
from datapack_emulator.emulator.common import parse_snbt
from datapack_emulator.emulator.runtime.inventory import ItemStack, parse_item_predicate
from datapack_emulator.emulator.runtime.predicates import Context, item_matches

V = versions.parse("26.3")


def matches(text: str, stack: ItemStack) -> bool:
    predicate = parse_item_predicate(text)
    predicate.version = V
    assert predicate.unchecked == []
    return predicate.matches(stack)


def stack(item_id="minecraft:carrot_on_a_stick", count=1, **components) -> ItemStack:
    return ItemStack(item_id, count, {f"minecraft:{k}": v for k, v in components.items()})


def test_custom_data_is_a_partial_match():
    wrench = stack(custom_data=parse_snbt("{ra:{wrench:1b,mode:2},other:1}"))
    assert matches("*[minecraft:custom_data~{ra:{wrench:1b}}]", wrench)
    assert matches("*[custom_data~{}]", wrench)
    assert not matches("*[custom_data~{ra:{wrench:0b}}]", wrench)
    assert not matches("*[custom_data~{ra:{data_handler:1b}}]", wrench)
    assert not matches("*[custom_data~{ra:{wrench:1b}}]", stack())
    assert matches("*[!custom_data~{ra:{data_handler:1b}}]", wrench)
    assert matches("*[custom_data~{ra:{x:1b}}|custom_data~{other:1}]", wrench)
    # a list pattern: every element must be in the item's list
    tagged = stack(custom_data=parse_snbt('{tags:["a","b","c"]}'))
    assert matches('*[custom_data~{tags:["c","a"]}]', tagged)
    assert not matches('*[custom_data~{tags:["d"]}]', tagged)


def test_damage_enchantments_potions_and_count():
    sword = ItemStack(
        "minecraft:diamond_sword",
        2,
        {"minecraft:damage": 61, "minecraft:enchantments": {"minecraft:sharpness": 3}},
    )
    assert matches("*[damage~{damage:{min:60}}]", sword)
    assert matches("*[damage~{durability:1500}]", sword)  # 1561 - 61
    assert not matches("*[damage~{damage:{max:10}}]", sword)
    assert not matches("*[damage~{}]", stack("minecraft:stick"))  # no max_damage
    assert matches('*[enchantments~[{enchantments:"sharpness",levels:{min:2}}]]', sword)
    assert not matches('*[enchantments~[{enchantments:"sharpness",levels:{min:4}}]]', sword)
    assert not matches('*[enchantments~[{enchantments:["smite","bane_of_arthropods"]}]]', sword)
    assert matches("*[enchantments~[{}]]", sword) and not matches("*[enchantments~[{}]]", stack())
    assert matches("*[enchantments~[]]", stack())  # every item has the component
    assert not matches("*[stored_enchantments~[]]", stack())
    book = stack("minecraft:enchanted_book", stored_enchantments={"minecraft:mending": 1})
    assert matches('*[stored_enchantments~[{enchantments:"mending"}]]', book)
    potion = stack("minecraft:potion", potion_contents={"potion": "minecraft:swiftness"})
    assert matches('*[potion_contents~["swiftness","minecraft:leaping"]]', potion)
    assert not matches('*[potion_contents~"leaping"]', potion)
    assert matches("*[count~{min:2}]", sword) and not matches("*[count~{min:3}]", sword)
    assert matches("*[count=2]", sword) and not matches("*[!count=2]", sword)


def test_unknown_or_tag_sub_predicates_stay_unchecked():
    for text in ('*[enchantments~[{enchantments:"#minecraft:curse"}]]', "*[trim~{}]"):
        predicate = parse_item_predicate(text)
        assert predicate.unchecked and predicate.matches(stack())


def test_json_item_predicates_map():
    wrench = stack(custom_data=parse_snbt("{ra:{wrench:1b}}"))
    context = Context(version=V)
    yes = {
        "items": "minecraft:carrot_on_a_stick",
        "predicates": {"custom_data": "{ra:{wrench:1b}}"},
    }
    assert item_matches(yes, wrench, context)
    no = {"predicates": {"minecraft:custom_data": {"ra": {"wrench": 0}}}}
    assert not item_matches(no, wrench, context)
    assert not context.unchecked


TICK = (
    "execute as @a if items entity @s weapon.mainhand *[minecraft:custom_data~{ra:{wrench:1b}}] "
    "run scoreboard players set @s ra.hold.wrench 200\n"
    "execute as @a if items entity @s weapon.mainhand "
    "*[minecraft:custom_data~{ra:{data_handler:1b}}] run scoreboard players set @s ra.hold.dh 200\n"
)


def holding(make_pack, give: str | None) -> dict:
    files = {
        "data/test/function/load.mcfunction": (
            "scoreboard objectives add ra.hold.wrench dummy\n"
            "scoreboard objectives add ra.hold.dh dummy\n"
            "scoreboard players set @a ra.hold.wrench 0\n"
            "scoreboard players set @a ra.hold.dh 0\n" + (give or "")
        ),
        "data/test/function/tick.mcfunction": TICK,
        "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
        "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
    }
    pack = make_pack(
        files, mcmeta={"pack": {"description": "t", "min_format": 121, "max_format": 121}}
    )
    emulator = Emulator(Datapack.load(pack), version="26.3")
    emulator.run(ticks=1)
    scoreboard = emulator.world.scoreboard
    notes = [r.message for r in emulator.output.records if "not checked" in r.message]
    assert notes == []
    return {name: scoreboard.get("Player1", name) for name in ("ra.hold.wrench", "ra.hold.dh")}


def test_a_wrench_in_the_main_hand_sets_the_score(make_pack):
    wrench = "give @a minecraft:carrot_on_a_stick[custom_data={ra:{wrench:1b}}]\n"
    assert holding(make_pack, wrench) == {"ra.hold.wrench": 200, "ra.hold.dh": 0}
    assert holding(make_pack, None) == {"ra.hold.wrench": 0, "ra.hold.dh": 0}
    stone = "give @a minecraft:stone\n"
    assert holding(make_pack, stone) == {"ra.hold.wrench": 0, "ra.hold.dh": 0}
