"""Predicates, loot conditions and functions, and advancements."""

from __future__ import annotations

import random

import pytest
from conftest import chat, visible_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime.inventory import ItemStack
from datapack_emulator.emulator.runtime.loot import LootResult, apply, evaluate
from datapack_emulator.emulator.runtime.predicates import Context, check, item_matches
from datapack_emulator.emulator.versions import parse

PREDICATES = {
    "data/test/predicate/rich.json": {
        "condition": "minecraft:entity_scores",
        "entity": "this",
        "scores": {"money": {"min": 10}},
    },
    "data/test/predicate/raining.json": {"condition": "minecraft:weather_check", "raining": True},
    "data/test/predicate/tagged_pig.json": {
        "condition": "minecraft:entity_properties",
        "entity": "this",
        "predicate": {"type": "minecraft:pig", "nbt": '{Tags:["p"]}'},
    },
    "data/test/predicate/either.json": {
        "condition": "minecraft:any_of",
        "terms": [
            {"condition": "minecraft:reference", "name": "test:rich"},
            {"condition": "minecraft:reference", "name": "test:raining"},
        ],
    },
    "data/test/predicate/killer.json": {
        "condition": "minecraft:entity_properties",
        "entity": "attacker",
        "predicate": {},
    },
    "data/test/advancement/root.json": {
        "criteria": {"start": {"trigger": "minecraft:tick"}},
        "rewards": {"function": "test:rewarded", "experience": 10},
    },
    "data/test/advancement/rich.json": {
        "parent": "test:root",
        "display": {"title": "Rich", "description": "", "icon": {"id": "minecraft:gold_ingot"}},
        "criteria": {
            "money": {
                "trigger": "minecraft:tick",
                "conditions": {
                    "player": [
                        {
                            "condition": "minecraft:entity_scores",
                            "entity": "this",
                            "scores": {"money": {"min": 100}},
                        }
                    ]
                },
            },
            "manual": {"trigger": "minecraft:impossible"},
        },
        "requirements": [["money", "manual"]],
    },
    "data/test/advancement/child.json": {
        "parent": "test:rich",
        "criteria": {"never": {"trigger": "minecraft:impossible"}},
    },
    "data/test/function/rewarded.mcfunction": "tag @s add rewarded\n",
    "data/test/function/tick.mcfunction": "\n",
    "data/test/function/load.mcfunction": "scoreboard objectives add money dummy\n",
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
}


@pytest.fixture
def world(make_pack):
    pack = Datapack.load(make_pack(PREDICATES))

    def make(version: str = "1.21.4"):
        emulator = Emulator(pack, version=version)
        emulator.start()
        emulator.run_tick()

        def typed(line: str):
            before = len(emulator.output.records)
            result, _ = emulator.run_typed(line)
            return result, emulator.output.records[before:]

        emulator.typed = typed
        return emulator

    return make


def test_execute_if_predicate_and_references(world):
    emulator = world()
    assert not emulator.typed("execute as Player1 if predicate test:rich")[0].success
    emulator.typed("scoreboard players set Player1 money 12")
    assert emulator.typed("execute as Player1 if predicate test:rich")[0].success
    assert emulator.typed("execute as Player1 if predicate test:either")[0].success
    assert not emulator.typed("execute if predicate test:either")[0].success  # no "this"
    emulator.typed("weather rain")
    assert emulator.typed("execute if predicate test:either")[0].success
    _, records = emulator.typed("execute if predicate test:missing")
    assert visible_errors(records) == ["Can't find element 'test:missing' in registry 'predicate'"]
    _, records = emulator.typed("execute as Player1 if predicate test:killer")
    assert any("cannot check entity attacker" in r.message for r in records)
    inline = '{"condition":"minecraft:random_chance","chance":1}'
    assert emulator.typed(f"execute if predicate {inline}")[0].success


def test_selector_predicate_and_advancements(world):
    emulator = world()
    emulator.typed("summon pig ~ ~ ~ {Tags:[p]}")
    emulator.typed("summon pig")
    assert emulator.typed("execute if entity @e[predicate=test:tagged_pig]")[0].value == 1
    assert emulator.typed("execute if entity @e[type=pig,predicate=!test:tagged_pig]")[0].value == 1
    assert emulator.typed("execute if entity @a[advancements={test:root=true}]")[0].value == 1
    assert (
        emulator.typed("execute if entity @a[advancements={test:rich={money=false}}]")[0].value == 1
    )


def test_tick_criteria_rewards_and_the_command(world):
    emulator = world()
    player = emulator.world.players[0]
    progress = emulator.world.advancements
    root = progress.tree.get("test:root")
    assert progress.done(player.id, root)
    assert "rewarded" in player.tags and player.nbt["XpTotal"] == 10
    rich = progress.tree.get("test:rich")
    assert not progress.done(player.id, rich)
    emulator.typed("scoreboard players set Player1 money 100")
    emulator.run_tick()
    assert progress.done(player.id, rich)

    result, records = emulator.typed("advancement revoke Player1 only test:rich")
    assert chat(records) == ["Revoked the advancement [Rich] from Player1"]
    _, records = emulator.typed("advancement revoke Player1 only test:rich")
    assert visible_errors(records) == [
        "Couldn't revoke advancement [Rich] from Player1 as they don't have it"
    ]
    _, records = emulator.typed("advancement grant Player1 only test:rich manual")
    assert chat(records) == ["Granted criterion 'manual' of advancement [Rich] to Player1"]
    _, records = emulator.typed("advancement grant Player1 only test:rich nope")
    assert visible_errors(records) == [
        "The advancement [Rich] does not contain the criterion 'nope'"
    ]
    assert emulator.typed("advancement grant @a from test:rich")[0].value == 2
    assert emulator.typed("advancement revoke @a until test:child")[0].value == 3
    assert emulator.typed("advancement grant @a everything")[0].value == 3
    _, records = emulator.typed("advancement grant @a only test:nope")
    assert visible_errors(records) == ["Unknown advancement: test:nope"]


def test_loot_conditions_are_evaluated(world):
    emulator = world()
    emulator.world.state.weather = "rain"
    table = {
        "pools": [
            {
                "rolls": 1,
                "entries": [
                    {
                        "type": "alternatives",
                        "children": [
                            {
                                "type": "item",
                                "name": "minecraft:diamond",
                                "conditions": [
                                    {"condition": "minecraft:weather_check", "thundering": True}
                                ],
                            },
                            {"type": "item", "name": "minecraft:stick"},
                        ],
                    }
                ],
            },
            {
                "rolls": 1,
                "conditions": [{"condition": "minecraft:killed_by_player"}],
                "entries": [{"type": "item", "name": "minecraft:emerald"}],
            },
        ]
    }
    context = Context(world=emulator.world, rng=random.Random(1))
    result = evaluate(table, lambda _id: None, context=context)
    assert [stack.id for stack in result.items] == ["minecraft:stick"]
    assert "killed_by_player" in result.skipped
    emulator.world.state.weather = "thunder"
    result = evaluate(table, lambda _id: None, context=Context(world=emulator.world))
    assert [stack.id for stack in result.items] == ["minecraft:diamond"]


def test_match_tool_with_the_mine_tool(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/minecraft/loot_table/blocks/stone.json": {
                    "pools": [
                        {
                            "rolls": 1,
                            "entries": [
                                {
                                    "type": "alternatives",
                                    "children": [
                                        {
                                            "type": "item",
                                            "name": "minecraft:stone",
                                            "conditions": [
                                                {
                                                    "condition": "minecraft:match_tool",
                                                    "predicate": {
                                                        "items": "minecraft:diamond_pickaxe"
                                                    },
                                                }
                                            ],
                                        },
                                        {"type": "item", "name": "minecraft:cobblestone"},
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed("setblock 0 0 0 stone")
    emulator.run_typed("loot give Player1 mine 0 0 0 diamond_pickaxe")
    emulator.run_typed("loot give Player1 mine 0 0 0 wooden_pickaxe")
    inventory = emulator.world.players[0].inventory
    assert [inventory.get(f"container.{i}").id for i in range(2)] == [
        "minecraft:stone",
        "minecraft:cobblestone",
    ]
    emulator.run_typed("item replace entity Player1 weapon.mainhand with diamond_pickaxe")
    emulator.run_typed("execute as Player1 run loot give Player1 mine 0 0 0 mainhand")
    assert inventory.get("container.1").id == "minecraft:cobblestone"
    assert inventory.get("container.2").id == "minecraft:stone"


def _apply(function, stack, version="1.21.4"):
    return apply(function, stack, Context(version=parse(version)), LootResult())


def test_item_functions_in_each_format():
    sword = _apply(
        [
            {"function": "set_name", "name": "Blade"},
            {"function": "set_lore", "lore": ["one"], "mode": "append"},
            {"function": "set_damage", "damage": 0.5},
            {"function": "set_enchantments", "enchantments": {"sharpness": 2}},
            {"function": "set_custom_data", "tag": "{a:1b}"},
            {"function": "limit_count", "limit": {"max": 1}},
        ],
        ItemStack("minecraft:iron_sword", 3),
    )
    assert sword.count == 1
    assert sword.components["minecraft:custom_name"] == "Blade"
    assert sword.components["minecraft:lore"] == ["one"]
    assert sword.components["minecraft:damage"] == 125
    assert sword.components["minecraft:enchantments"] == {"levels": {"minecraft:sharpness": 2}}
    assert sword.components["minecraft:custom_data"] == {"a": 1}
    flat = _apply(
        {"function": "set_enchantments", "enchantments": {"sharpness": 2}},
        ItemStack("minecraft:iron_sword"),
        "1.21.5",
    )
    assert flat.components["minecraft:enchantments"] == {"minecraft:sharpness": 2}
    old = _apply(
        [
            {"function": "set_name", "name": {"text": "Old"}},
            {"function": "set_damage", "damage": 1.0},
            {"function": "set_enchantments", "enchantments": {"sharpness": 1}},
        ],
        ItemStack("minecraft:iron_sword"),
        "1.20.4",
    )
    assert old.tag == {
        "display": {"Name": '{"text": "Old"}'},
        "Damage": 0,
        "Enchantments": [{"id": "minecraft:sharpness", "lvl": 1}],
    }
    book = _apply(
        {"function": "set_enchantments", "enchantments": {"mending": 1}},
        ItemStack("minecraft:book"),
    )
    assert book.id == "minecraft:enchanted_book"
    assert "minecraft:stored_enchantments" in book.components
    filtered = _apply(
        {
            "function": "filtered",
            "item_filter": {"items": "minecraft:stone"},
            "modifier": {"function": "set_count", "count": 5},
        },
        ItemStack("minecraft:dirt"),
    )
    assert filtered.count == 1
    assert _apply({"function": "discard"}, ItemStack("minecraft:dirt")).count == 0


def test_conditions_on_functions_and_item_predicates():
    context = Context(rng=random.Random(0))
    never = {"condition": "minecraft:random_chance", "chance": 0}
    stack = apply(
        {"function": "set_count", "count": 9, "conditions": [never]},
        ItemStack("minecraft:dirt"),
        context,
        LootResult(),
    )
    assert stack.count == 1
    assert check({"condition": "inverted", "term": never}, context)
    assert item_matches(
        {"items": ["minecraft:dirt"], "count": {"min": 1}}, ItemStack("minecraft:dirt"), context
    )
    assert not item_matches(
        {"items": "#minecraft:logs"},
        ItemStack("minecraft:dirt"),
        Context(tags=lambda registry, tag: {"minecraft:oak_log"}),
    )
    assert check(
        {"condition": "value_check", "value": {"type": "uniform", "min": 1, "max": 1}, "range": 1},
        context,
    )


def test_item_modifiers_use_conditions(world, make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "\n",
                "data/test/item_modifier/rename.json": [
                    {
                        "function": "set_name",
                        "name": "Rainy",
                        "conditions": [{"condition": "weather_check", "raining": True}],
                    }
                ],
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run_typed("item replace entity Player1 weapon.mainhand with stick")
    emulator.run_typed("item modify entity Player1 weapon.mainhand test:rename")
    stack = emulator.world.players[0].inventory.get("container.0")
    assert "minecraft:custom_name" not in stack.components
    emulator.run_typed("weather rain")
    emulator.run_typed("item modify entity Player1 weapon.mainhand test:rename")
    assert stack.components.get("minecraft:custom_name") == "Rainy" or (
        emulator.world.players[0].inventory.get("container.0").components["minecraft:custom_name"]
        == "Rainy"
    )
