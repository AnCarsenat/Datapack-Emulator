"""Static problems of a pack: what the problems dock and ``check`` report."""

from __future__ import annotations

import pytest

from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.analysis.problems import count, find_problems

BAD_PACK = {
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick", "test:gone"]},
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
    "data/test/function/load.mcfunction": "say loaded\n",
    "data/test/function/tick.mcfunction": (
        "execute as @e[tpye=pig] run say hi\n"  # 1: unknown selector option
        "summon pig ~ ~ ~ {Tags:[\n"  # 2: broken SNBT
        "function test:nowhere\n"  # 3: missing function
        'tellraw @a {"text":\n'  # 4: broken text component
        "function test:loop\n"  # 5
        "$say $(macro) {broken\n"  # 6: macros are checked when called
        "summon minecraft:not_a_mob\n"  # 7: unknown id (needs a jar)
    ),
    "data/test/function/loop.mcfunction": "function test:loop\n",
    "data/test/function/unused.mcfunction": "say never\n",
    "data/test/function/rewarded.mcfunction": "function test:rewarded_helper\n",
    "data/test/function/rewarded_helper.mcfunction": "say reward\n",
    "data/test/function/old.mcfunction": "frobnicate\n",
    "data/test/predicate/bad.json": [
        {"condition": "minecraft:random_chance", "chance": 0.5},
        {"condition": "minecraft:nope"},
        {"condition": "inverted", "term": {"condition": "minecraft:also_nope"}},
    ],
    "data/test/item_modifier/bad.json": {"function": "minecraft:frobnicate"},
    "data/test/loot_table/bad.json": {
        "pools": [
            {"entries": [{"type": "minecraft:mystery"}]},
            {"rolls": 1, "entries": "x"},
        ]
    },
    "data/test/advancement/empty.json": {"parent": "test:missing"},
    "data/test/advancement/rewarding.json": {
        "criteria": {"a": {"trigger": "minecraft:tick"}, "b": {}},
        "rewards": {"function": "test:rewarded"},
    },
    "data/test/advancement/broken_reward.json": {
        "criteria": {"a": {"trigger": "minecraft:tick"}},
        "rewards": {"function": "test:absent"},
    },
    "data/test/recipe/untyped.json": {"ingredients": []},
    "data/test/recipe/broken.json": "{not json",
}


@pytest.fixture
def pack(make_pack):
    return Datapack.load(make_pack(BAD_PACK))


def test_problems_of_a_broken_pack(pack):
    problems = find_problems(pack, "1.21.4")
    found = {(p.severity, p.code, p.where) for p in problems}
    assert ("error", "function-not-loaded", "test:tick:1") in found  # unknown option 'tpye'
    assert ("warning", "snbt", "test:tick:2") in found
    assert ("warning", "missing-function", "test:tick:3") in found
    assert ("error", "text-component", "test:tick:4") in found
    assert not any(p.line == 6 for p in problems)  # macro lines wait for their call
    assert ("error", "function-not-loaded", "test:old:1") in found
    assert ("error", "tag-not-loaded", "#minecraft:tick") in found
    assert ("error", "condition", "test:bad") in found
    assert ("error", "item-function", "test:bad") in found
    assert ("error", "loot-entry", "test:bad") in found
    assert ("error", "loot-table", "test:bad") in found
    assert ("error", "advancement", "test:empty") in found
    assert ("warning", "advancement", "test:empty") in found  # its parent
    assert ("error", "advancement", "test:rewarding") in found
    assert ("warning", "missing-function", "test:broken_reward") in found
    assert ("error", "recipe", "test:untyped") in found
    assert ("error", "json", "test:broken") in found
    assert ("info", "unused-function", "test:unused") in found
    assert ("info", "recursion", "test:loop") in found
    unused = {p.resource for p in problems if p.code == "unused-function"}
    assert "test:rewarded" not in unused and "test:rewarded_helper" not in unused
    conditions = [p.message for p in problems if p.code == "condition"]
    assert (
        "predicate: 1.21.4 has no condition type minecraft:nope: the file does not load"
        in conditions
    )
    assert any("minecraft:also_nope" in message for message in conditions)
    assert "Unknown option 'tpye'" in next(p for p in problems if p.where == "test:tick:1").message
    severities = [p.severity for p in problems]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)
    totals = count(problems)
    assert totals["error"] >= 12 and totals["warning"] >= 4 and totals["info"] >= 2
    tick = next(p for p in problems if p.where == "test:tick:1")
    assert tick.path is not None and tick.path.name == "tick.mcfunction"
    assert tick.to_dict()["path"].endswith("tick.mcfunction")


def test_ids_are_checked_with_a_client_jar(pack, fake_jar):
    from datapack_emulator.emulator.vanilla import VanillaAssets

    problems = find_problems(pack, "1.21.4", vanilla=VanillaAssets.from_jar(fake_jar))
    unknown = [p for p in problems if p.code == "unknown-id"]
    assert [p.where for p in unknown] == ["test:tick:7"]
    assert "not_a_mob does not exist" in unknown[0].message


def test_versioned_names_templates_and_entry_points(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                "data/test/function/tick.mcfunction": (
                    "return run function test:returned\n"
                    "$say no variables\n"
                    "$say $(unterminated\n"
                    "$say $(bad.name)\n"
                ),
                "data/test/function/returned.mcfunction": "say hi\n",
                "data/test/function/enchanted.mcfunction": "say zap\n",
                "data/test/enchantment/zap.json": {
                    "effects": {
                        "minecraft:tick": [
                            {
                                "effect": {
                                    "type": "minecraft:run_function",
                                    "function": "test:enchanted",
                                }
                            }
                        ]
                    }
                },
                "data/test/predicate/old.json": {"condition": "minecraft:alternative", "terms": []},
                "data/test/predicate/new.json": {"condition": "minecraft:any_of", "terms": []},
                "data/test/item_modifier/old.json": {"function": "minecraft:set_nbt", "tag": "{}"},
                "data/test/item_modifier/modded.json": {"function": "mod:thing"},
                "data/test/advancement/requirements.json": {
                    "criteria": {
                        "a": {"trigger": "minecraft:tick"},
                        "b": {"trigger": "minecraft:no_such_trigger"},
                    },
                    "requirements": [["a", "c"]],
                    "rewards": {"function": "#test:tag"},
                },
                "data/test/recipe/strange.json": {"type": "minecraft:nope"},
                "data/test/recipe/fine.json": {"type": "minecraft:crafting_shaped"},
            }
        )
    )
    problems = find_problems(pack, "1.21.4")
    found = {(p.code, p.where): p.message for p in problems}
    assert not any(
        p.resource in ("test:returned", "test:enchanted") and p.code == "unused-function"
        for p in problems
    )
    assert "No variables in macro" in found[("function-not-loaded", "test:tick:2")]
    assert ("function-not-loaded", "test:tick:3") not in found  # only the first failure counts
    assert ("condition", "test:old") in found and ("condition", "test:new") not in found
    assert "no item function minecraft:set_nbt" in found[("item-function", "test:old")]
    assert ("item-function", "test:modded") in found
    advancement = [p.message for p in problems if p.resource == "test:requirements"]
    assert "criterion b: 1.21.4 has no trigger minecraft:no_such_trigger" in advancement
    assert "requirements name an unknown criterion c" in advancement
    assert "criterion b is not in the requirements: the advancement does not load" in advancement
    assert any("is a tag" in message for message in advancement)
    assert ("recipe", "test:strange") in found and ("recipe", "test:fine") not in found
    # the same files for 1.19.4 (plural folders): the old names are right there
    legacy = Datapack.load(
        make_pack(
            {
                "data/test/functions/tick.mcfunction": "say hi\n",
                "data/test/predicates/old.json": {
                    "condition": "minecraft:alternative",
                    "terms": [],
                },
                "data/test/predicates/new.json": {"condition": "minecraft:any_of", "terms": []},
                "data/test/item_modifiers/old.json": {"function": "minecraft:set_nbt", "tag": "{}"},
            },
            mcmeta={"pack": {"pack_format": 12, "description": "old"}},
        )
    )
    old = {(p.code, p.resource) for p in find_problems(legacy, "1.19.4")}
    assert ("condition", "test:new") in old and ("condition", "test:old") not in old
    assert ("item-function", "test:old") not in old


def test_macro_templates_load_when_valid(make_pack):
    from datapack_emulator.emulator.runtime.library import macro_template_problem

    assert macro_template_problem("$say $(name) and $(other_2)") == ""
    assert macro_template_problem("$say hi") == "No variables in macro"
    assert macro_template_problem("$say $(x") == "Unterminated macro variable"
    assert macro_template_problem("$say $(a.b)") == "Invalid macro variable name 'a.b'"


def test_long_chains_many_functions_and_odd_files(make_pack):
    import time

    files = {
        "data/minecraft/tags/function/tick.json": {"values": ["test:f0"]},
        "data/test/tags/function/listed.json": ["test:f0"],
        "data/test/loot_table/listed.json": [1, 2],
        "data/test/advancement/text.json": ["not", "an", "object"],
    }
    for index in range(1500):
        files[f"data/test/function/f{index}.mcfunction"] = f"function test:f{index + 1}\n"
    files["data/test/function/f1500.mcfunction"] = "function test:f0\n"
    pack = Datapack.load(make_pack(files))
    started = time.perf_counter()
    problems = find_problems(pack, "1.21.4")
    assert time.perf_counter() - started < 10
    found = {(p.code, p.resource) for p in problems}
    assert ("recursion", "test:f0") in found
    assert ("json", "#test:listed") in found or ("json", "test:listed") in found
    assert ("loot-table", "test:listed") in found
    assert ("advancement", "test:text") in found

    # a version that reads no function of the pack says so
    singular = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "say hi\n"}))
    messages = [p.message for p in find_problems(singular, "1.20.4") if p.code == "pack"]
    assert any("reads none of the pack's 1 function(s)" in m for m in messages)
