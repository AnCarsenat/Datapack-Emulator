"""Loot conditions, item functions and loot tables in the 26.3 format
(``type`` instead of ``condition``/``function``, single ``condition`` and
``modifier`` fields, references as plain ids), next to the older format."""

from __future__ import annotations

import random

import pytest

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.analysis.problems import find_problems
from datapack_emulator.emulator.runtime.loot import evaluate
from datapack_emulator.emulator.runtime.predicates import Context, check
from datapack_emulator.emulator.versions import parse


def _multiblock(key: str) -> dict:
    """An ``all_of`` of offset ``location_check``s, as a multiblock check is written."""

    def at(dx: int, dy: int, dz: int, block: dict) -> dict:
        check = {key: "minecraft:location_check", "predicate": {"block": block}}
        for axis, value in zip("XYZ", (dx, dy, dz), strict=True):
            if value:
                check[f"offset{axis}"] = value
        return check

    return {
        key: "minecraft:all_of",
        "terms": [
            at(
                0,
                0,
                0,
                {"blocks": "minecraft:furnace", "state": {"facing": "north", "lit": "false"}},
            ),
            at(0, 1, 0, {"blocks": "#test:frames"}),
            at(1, 0, 0, {"blocks": ["minecraft:hopper", "minecraft:dropper"]}),
            at(0, -1, 0, {"blocks": "minecraft:oak_log", "state": {"axis": "y"}}),
        ],
    }


def _multiblock_pack(make_pack, key: str, pack_format: int):
    return Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/predicate/drill.json": _multiblock(key),
                "data/test/tags/block/frames.json": {
                    "values": ["minecraft:iron_block", "minecraft:gold_block"]
                },
            },
            mcmeta={"pack": {"description": "t", "pack_format": pack_format}},
        )
    )


@pytest.mark.parametrize(
    ("version", "key", "pack_format"), [("1.21.4", "condition", 61), ("26.3", "type", 121)]
)
def test_multiblock_location_checks(make_pack, version, key, pack_format):
    emulator = Emulator(_multiblock_pack(make_pack, key, pack_format), version=version)
    emulator.start()

    def holds() -> bool:
        return emulator.run_typed("execute positioned 5 64 5 if predicate test:drill")[0].success

    emulator.run_typed("setblock 5 64 5 minecraft:furnace[facing=north,lit=false]")
    emulator.run_typed("setblock 5 65 5 minecraft:gold_block")
    emulator.run_typed("setblock 6 64 5 minecraft:dropper")
    emulator.run_typed("setblock 5 63 5 minecraft:oak_log[axis=y]")
    assert holds()
    emulator.run_typed("setblock 5 64 5 minecraft:furnace[facing=south]")
    assert not holds()  # a string state value
    emulator.run_typed("setblock 5 64 5 minecraft:furnace[facing=north,lit=false]")
    emulator.run_typed("setblock 5 65 5 minecraft:stone")
    assert not holds()  # a block tag
    emulator.run_typed("setblock 5 65 5 minecraft:iron_block")
    emulator.run_typed("setblock 6 64 5 minecraft:chest")
    assert not holds()  # an offset and a list of blocks
    emulator.run_typed("setblock 6 64 5 minecraft:hopper")
    assert holds()
    emulator.run_typed("setblock 5 63 5 minecraft:oak_log[axis=x]")
    assert not holds()


def test_a_missing_default_state_is_noted(make_pack):
    emulator = Emulator(_multiblock_pack(make_pack, "type", 121), version="26.3")
    emulator.start()
    emulator.run_typed("setblock 5 64 5 minecraft:furnace")  # no states given
    emulator.run_typed("setblock 5 65 5 minecraft:gold_block")
    emulator.run_typed("setblock 6 64 5 minecraft:dropper")
    emulator.run_typed("setblock 5 63 5 minecraft:oak_log[axis=y]")
    before = len(emulator.output.records)
    result, _ = emulator.run_typed("execute positioned 5 64 5 if predicate test:drill")
    assert not result.success
    records = emulator.output.records[before:]
    assert any("default state facing of minecraft:furnace" in r.message for r in records)


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------


def _ctx(version: str, **kwargs) -> Context:
    return Context(version=parse(version), rng=random.Random(1), **kwargs)


def test_the_type_key_follows_the_version():
    typed = {"type": "minecraft:random_chance", "chance": 1}
    legacy = {"condition": "minecraft:random_chance", "chance": 1}
    assert check(typed, _ctx("26.3"))
    assert not check(legacy, _ctx("26.3"))
    assert check(legacy, _ctx("26.2"))
    assert check(legacy, _ctx("1.21.4"))
    assert not check(typed, _ctx("26.2"))
    # no version known: either spelling
    assert check(typed, Context()) and check(legacy, Context())


def test_value_checks_and_removed_types():
    def value_check(kind: str, key: str, version: str, low: int) -> bool:
        condition = {key: kind, "value": 5, ("test" if "_value" in kind else "range"): {"min": low}}
        return check(condition, _ctx(version))

    assert value_check("int_value_check", "type", "26.3", 5)
    assert not value_check("int_value_check", "type", "26.3", 6)
    assert value_check("float_value_check", "type", "26.3", 4)
    assert value_check("value_check", "condition", "1.21.4", 5)
    # renamed in 26.3, and the new names do not exist before
    context = _ctx("26.3")
    assert not check({"type": "value_check", "value": 5, "range": 5}, context)
    assert "minecraft:value_check" in context.unchecked
    assert not check({"condition": "int_value_check", "value": 5, "test": 5}, _ctx("26.2"))


def test_references_are_ids_from_26_3():
    files = {
        "test:yes": {"type": "minecraft:random_chance", "chance": 1},
        "test:no": {"type": "minecraft:random_chance", "chance": 0},
        "test:old": {"condition": "minecraft:random_chance", "chance": 1},
    }
    tags = {"#test:all_yes": {"test:yes"}, "#test:mixed": {"test:yes", "test:no"}}

    def ctx(version: str) -> Context:
        return _ctx(
            version,
            lookup=files.get,
            tags=lambda registry, tag: tags.get(tag) if registry == "predicate" else None,
        )

    assert check("test:yes", ctx("26.3"))
    assert not check("test:no", ctx("26.3"))
    assert check(
        {"type": "all_of", "terms": ["test:yes", {"type": "random_chance", "chance": 1}]},
        ctx("26.3"),
    )
    assert check({"type": "all_of", "terms": ["#test:all_yes"]}, ctx("26.3"))
    assert not check({"type": "all_of", "terms": ["#test:mixed"]}, ctx("26.3"))
    assert check({"type": "any_of", "terms": ["#test:mixed"]}, ctx("26.3"))
    assert check({"type": "inverted", "term": "test:no"}, ctx("26.3"))
    missing = ctx("26.3")
    assert not check("test:missing", missing)
    assert "missing predicate test:missing" in missing.unchecked
    # the reference type is gone in 26.3, and a string is no condition before
    assert not check({"type": "reference", "name": "test:yes"}, ctx("26.3"))
    assert check({"condition": "reference", "name": "test:old"}, ctx("1.21.4"))
    assert not check("test:old", ctx("1.21.4"))


def test_match_block_and_block_state_property(make_pack):
    files = {
        "data/test/function/tick.mcfunction": "\n",
        "data/minecraft/loot_table/blocks/wheat.json": {},
    }
    typed_table = {
        "type": "minecraft:block",
        "pools": [
            {
                "rolls": 1,
                "condition": {
                    "type": "minecraft:match_block",
                    "blocks": "minecraft:wheat",
                    "state": {"age": "7"},
                },
                "entries": [{"type": "minecraft:item", "name": "minecraft:wheat"}],
            }
        ],
    }
    legacy_table = {
        "type": "minecraft:block",
        "pools": [
            {
                "rolls": 1,
                "conditions": [
                    {
                        "condition": "minecraft:block_state_property",
                        "block": "minecraft:wheat",
                        "properties": {"age": "7"},
                    }
                ],
                "entries": [{"type": "minecraft:item", "name": "minecraft:wheat"}],
            }
        ],
    }
    for version, pack_format, table in (("26.3", 121, typed_table), ("1.21.4", 61, legacy_table)):
        files["data/minecraft/loot_table/blocks/wheat.json"] = table
        pack = Datapack.load(
            make_pack(files, mcmeta={"pack": {"description": "t", "pack_format": pack_format}})
        )
        emulator = Emulator(pack, version=version)
        emulator.run_typed("setblock 0 0 0 wheat[age=3]")
        emulator.run_typed("loot spawn 0 1 0 mine 0 0 0")
        emulator.run_typed("setblock 0 0 0 wheat[age=7]")
        emulator.run_typed("loot spawn 0 1 0 mine 0 0 0")
        items = [e for e in emulator.world.entities if e.type == "minecraft:item"]
        assert len(items) == 1, version


# ---------------------------------------------------------------------------
# loot tables and item modifiers
# ---------------------------------------------------------------------------


def _roll(table: dict, version: str, modifiers: dict | None = None, tags: dict | None = None):
    context = _ctx(
        version,
        modifiers=(modifiers or {}).get,
        tags=lambda registry, tag: (tags or {}).get(tag),
    )
    return evaluate(table, lambda _id: None, context=context)


def _table(typed: bool) -> dict:
    """The same table in both formats."""
    if typed:
        never = {"type": "minecraft:random_chance", "chance": 0}
        return {
            "modifier": {"type": "minecraft:set_name", "name": "table"},
            "pools": [
                {
                    "rolls": 1,
                    "modifier": [{"type": "minecraft:set_count", "count": 3}, "test:plus_one"],
                    "entries": [
                        {
                            "type": "minecraft:alternatives",
                            "modifier": {"type": "minecraft:set_custom_data", "tag": "{a:1b}"},
                            "children": [
                                {
                                    "type": "minecraft:item",
                                    "name": "minecraft:stone",
                                    "condition": never,
                                },
                                {"type": "minecraft:item", "name": "minecraft:dirt"},
                            ],
                        }
                    ],
                },
                {
                    "rolls": 1,
                    "condition": never,
                    "entries": [{"type": "item", "name": "minecraft:diamond"}],
                },
                {
                    "rolls": 1,
                    "entries": [{"type": "minecraft:tag", "items": "#test:gems", "expand": False}],
                },
            ],
        }
    never = {"condition": "minecraft:random_chance", "chance": 0}
    return {
        "functions": [{"function": "minecraft:set_name", "name": "table"}],
        "pools": [
            {
                "rolls": 1,
                "functions": [
                    {"function": "minecraft:set_count", "count": 3},
                    {"function": "minecraft:reference", "name": "test:plus_one"},
                ],
                "entries": [
                    {
                        "type": "minecraft:alternatives",
                        "children": [
                            {
                                "type": "minecraft:item",
                                "name": "minecraft:stone",
                                "conditions": [never],
                            },
                            {
                                "type": "minecraft:item",
                                "name": "minecraft:dirt",
                                "functions": [
                                    {"function": "minecraft:set_custom_data", "tag": "{a:1b}"}
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "rolls": 1,
                "conditions": [never],
                "entries": [{"type": "item", "name": "minecraft:diamond"}],
            },
            {
                "rolls": 1,
                "entries": [{"type": "minecraft:tag", "name": "test:gems", "expand": False}],
            },
        ],
    }


@pytest.mark.parametrize(("version", "typed"), [("26.3", True), ("26.2", False), ("1.21.4", False)])
def test_loot_tables_in_each_format(version, typed):
    plus_one = {
        True: {"type": "minecraft:set_count", "count": 1, "add": True},
        False: {"function": "minecraft:set_count", "count": 1, "add": True},
    }[typed]
    result = _roll(
        _table(typed),
        version,
        modifiers={"test:plus_one": plus_one},
        tags={"#test:gems": {"minecraft:emerald", "minecraft:diamond"}},
    )
    assert not result.skipped, result.skipped
    by_id = {stack.id: stack for stack in result.items}
    assert sorted(by_id) == ["minecraft:diamond", "minecraft:dirt", "minecraft:emerald"]
    dirt = by_id["minecraft:dirt"]
    assert dirt.count == 4
    assert dirt.components["minecraft:custom_data"] == {"a": 1}
    assert dirt.components["minecraft:custom_name"]  # the table's own modifier
    # the other format's fields are not read
    other = _roll(_table(not typed), version, modifiers={"test:plus_one": plus_one})
    assert {stack.count for stack in other.items} <= {1}


def test_item_modifier_files_and_ids(make_pack):
    def emulator_for(version: str, pack_format: int, files: dict) -> Emulator:
        files = {"data/test/function/tick.mcfunction": "\n", **files}
        pack = Datapack.load(
            make_pack(files, mcmeta={"pack": {"description": "t", "pack_format": pack_format}})
        )
        emulator = Emulator(pack, version=version)
        emulator.run_typed("item replace entity Player1 weapon.mainhand with stick 10")
        return emulator

    def count(emulator: Emulator) -> int:
        return emulator.world.players[0].inventory.get("container.0").count

    typed = emulator_for(
        "26.3",
        121,
        {
            "data/test/item_modifier/take_one.json": {
                "type": "set_count",
                "count": -1,
                "add": True,
            },
            "data/test/item_modifier/take_two.json": ["test:take_one", "test:take_one"],
            "data/test/item_modifier/old.json": {"function": "set_count", "count": 1},
        },
    )
    typed.run_typed("item modify entity Player1 weapon.mainhand test:take_one")
    assert count(typed) == 9
    typed.run_typed("item modify entity Player1 weapon.mainhand test:take_two")
    assert count(typed) == 7
    typed.run_typed("item modify entity Player1 weapon.mainhand test:old")
    assert count(typed) == 7  # `function` is not read in 26.3
    legacy = emulator_for(
        "1.21.4",
        61,
        {
            "data/test/item_modifier/take_one.json": {
                "function": "set_count",
                "count": -1,
                "add": True,
            },
            "data/test/item_modifier/take_two.json": [
                {"function": "reference", "name": "test:take_one"},
                {"function": "reference", "name": "test:take_one"},
            ],
        },
    )
    legacy.run_typed("item modify entity Player1 weapon.mainhand test:take_two")
    assert count(legacy) == 8


# ---------------------------------------------------------------------------
# advancements and entity predicates
# ---------------------------------------------------------------------------


def test_a_26_3_criterion_takes_one_player_condition(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/advancement/rich.json": {
                    "criteria": {
                        "money": {
                            "trigger": "minecraft:tick",
                            "conditions": {
                                "player": {
                                    "type": "minecraft:entity_scores",
                                    "entity": "this",
                                    "scores": {"money": {"min": 100}},
                                }
                            },
                        }
                    }
                },
            },
            mcmeta={"pack": {"description": "t", "pack_format": 121}},
        )
    )
    emulator = Emulator(pack, version="26.3")
    emulator.start()
    emulator.run_typed("scoreboard objectives add money dummy")
    emulator.run_tick()
    assert not emulator.run_typed("execute if entity @a[advancements={test:rich=true}]")[0].success
    emulator.run_typed("scoreboard players set Player1 money 150")
    emulator.run_tick()
    assert emulator.run_typed("execute if entity @a[advancements={test:rich=true}]")[0].success


def test_entity_predicates_as_a_map_from_26_2(make_pack):
    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "\n"}))
    for version in ("26.2", "26.3"):
        emulator = Emulator(pack, version=version)
        emulator.run_typed("summon pig 0 0 0 {Tags:[p]}")
        key = "type" if version == "26.3" else "condition"

        def matches(predicate: str, emulator: Emulator = emulator, key: str = key) -> bool:
            inline = f'{{{key}:"entity_properties",entity:"this",predicate:{predicate}}}'
            return emulator.run_typed(f"execute as @e[type=pig] if predicate {inline}")[0].success

        assert matches('{"minecraft:entity_type":"minecraft:pig"}')
        assert not matches('{"minecraft:entity_type":"minecraft:cow"}')
        assert not matches('{"minecraft:flags":{is_on_fire:true}}')
        assert matches('{"minecraft:nbt":"{Tags:[\\"p\\"]}"}')
        assert not matches('{"minecraft:type_specific/sheep":{sheared:false}}')
    # before 26.2 such keys are not read (the game ignores them too)
    old = Emulator(pack, version="1.21.4")
    old.run_typed("summon pig 0 0 0")
    inline = '{condition:"entity_properties",entity:"this",predicate:{"minecraft:entity_type":"minecraft:cow"}}'
    assert old.run_typed(f"execute as @e[type=pig] if predicate {inline}")[0].success


# ---------------------------------------------------------------------------
# static checks
# ---------------------------------------------------------------------------

TYPED_FILES = {
    "data/test/function/tick.mcfunction": "\n",
    "data/test/predicate/sneaking.json": {
        "type": "minecraft:entity_properties",
        "entity": "this",
        "predicate": {"minecraft:flags": {"is_sneaking": True}},
    },
    "data/test/predicate/both.json": {
        "type": "minecraft:all_of",
        "terms": [
            "test:sneaking",
            "#test:checks",
            {"type": "minecraft:match_block", "blocks": "stone"},
        ],
    },
    "data/test/tags/predicate/checks.json": {"values": ["test:sneaking"]},
    "data/test/item_modifier/take_one.json": {
        "type": "minecraft:set_count",
        "count": -1,
        "add": True,
    },
    "data/test/item_modifier/two.json": ["test:take_one", {"type": "set_count", "count": 1}],
    "data/test/loot_table/drop.json": {
        "modifier": "test:take_one",
        "pools": [
            {
                "rolls": 1,
                "condition": "test:sneaking",
                "modifier": [{"type": "set_count", "count": 2}],
                "entries": [
                    {
                        "type": "minecraft:group",
                        "modifier": "test:two",
                        "children": [
                            {
                                "type": "item",
                                "name": "stone",
                                "condition": {"type": "survives_explosion"},
                            },
                            {"type": "tag", "items": "#minecraft:logs", "expand": True},
                        ],
                    }
                ],
            }
        ],
    },
}


def _loot_problems(pack, version: str) -> list[tuple[str, str, str]]:
    codes = ("condition", "item-function", "loot-table", "loot-entry")
    return sorted(
        (p.severity, p.resource, p.message) for p in find_problems(pack, version) if p.code in codes
    )


def test_26_3_files_check_clean_and_old_ones_do_not(make_pack):
    typed = Datapack.load(
        make_pack(TYPED_FILES, mcmeta={"pack": {"description": "t", "pack_format": 121}})
    )
    assert _loot_problems(typed, "26.3") == []
    # the same files in 26.2: every type key is missing, ids are no conditions
    old = _loot_problems(typed, "26.2")
    assert (
        "error",
        "test:sneaking",
        "predicate: condition needs a type id: 26.2 reads condition (type is 26.3)",
    ) in old
    assert any(
        m == "item modifier: function needs a type id: 26.2 reads function (type is 26.3)"
        for _, _, m in old
    )
    assert any("does not read condition" in m for _, _, m in old)
    assert ("error", "test:both", "predicate: a condition is an object") in old
    early = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/predicate/block.json": {"condition": "minecraft:match_block"},
            }
        )
    )
    assert any(
        "no condition type minecraft:match_block" in m for _, _, m in _loot_problems(early, "26.2")
    )


def test_older_files_in_26_3(make_pack):
    files = {
        "data/test/function/tick.mcfunction": "\n",
        "data/test/predicate/old.json": {"condition": "minecraft:random_chance", "chance": 0.5},
        "data/test/predicate/state.json": {
            "type": "minecraft:block_state_property",
            "block": "minecraft:stone",
        },
        "data/test/predicate/ref.json": {"type": "minecraft:reference", "name": "test:old"},
        "data/test/predicate/missing.json": {"type": "minecraft:inverted", "term": "test:nowhere"},
        "data/test/item_modifier/old.json": {"function": "minecraft:set_count", "count": 1},
        "data/test/loot_table/old_table.json": {
            "pools": [
                {
                    "rolls": 1,
                    "conditions": [{"type": "minecraft:survives_explosion"}],
                    "functions": [{"type": "minecraft:set_count", "count": 1}],
                    "entries": [{"type": "minecraft:tag", "name": "minecraft:logs"}],
                },
                {
                    "rolls": 1,
                    "condition": [{"type": "minecraft:survives_explosion"}],
                    "entries": [{"type": "minecraft:empty"}],
                },
            ]
        },
    }
    pack = Datapack.load(
        make_pack(files, mcmeta={"pack": {"description": "t", "pack_format": 121}})
    )
    found = _loot_problems(pack, "26.3")
    messages = {(resource, message) for _, resource, message in found}
    assert (
        "test:old",
        "predicate: a condition names its type in type since 26.3, not condition",
    ) in messages
    assert any(
        r == "test:state" and "no condition type minecraft:block_state_property" in m
        for r, m in messages
    )
    assert any(
        r == "test:ref" and "no condition type minecraft:reference" in m for r, m in messages
    )
    assert (
        "test:missing",
        "predicate: names predicate test:nowhere, which is not in the pack: the file may not load",
    ) in messages
    assert (
        "test:old",
        "item modifier: an item function names its type in type since 26.3, not function",
    ) in messages
    assert any("does not read conditions (it is condition since 26.3)" in m for _, m in messages)
    assert any("does not read functions (it is modifier since 26.3)" in m for _, m in messages)
    assert any("a tag entry names its items in items since 26.3" in m for _, m in messages)
    assert any(s == "error" and "not a list (use minecraft:all_of)" in m for s, _, m in found)
    # the same files keep their older meaning in 1.21.4: only the 26.3 types are wrong there
    legacy = {m for _, r, m in _loot_problems(pack, "1.21.4") if r == "test:old"}
    assert legacy == set()
