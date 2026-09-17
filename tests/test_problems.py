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
    assert ("error", "selector-option", "test:tick:1") in found
    assert ("error", "snbt", "test:tick:2") in found
    assert ("warning", "missing-function", "test:tick:3") in found
    assert ("warning", "text-component", "test:tick:4") in found
    assert not any(p.line == 6 for p in problems)  # macro lines wait for their call
    assert ("error", "function-not-loaded", "test:old:1") in found
    assert ("error", "tag-not-loaded", "#minecraft:tick") in found
    assert ("warning", "condition", "test:bad") in found
    assert ("warning", "item-function", "test:bad") in found
    assert ("warning", "loot-entry", "test:bad") in found
    assert ("error", "loot-table", "test:bad") in found
    assert ("warning", "advancement", "test:empty") in found
    assert ("error", "advancement", "test:rewarding") in found
    assert ("warning", "missing-function", "test:broken_reward") in found
    assert ("error", "recipe", "test:untyped") in found
    assert ("error", "json", "test:broken") in found
    assert ("info", "unused-function", "test:unused") in found
    assert ("info", "recursion", "test:loop") in found
    unused = {p.resource for p in problems if p.code == "unused-function"}
    assert "test:rewarded" not in unused and "test:rewarded_helper" not in unused
    conditions = [p.message for p in problems if p.code == "condition"]
    assert "predicate: unknown condition type minecraft:nope" in conditions
    assert "predicate: unknown condition type minecraft:also_nope" in conditions
    severities = [p.severity for p in problems]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)
    totals = count(problems)
    assert totals["error"] >= 8 and totals["warning"] >= 7 and totals["info"] >= 2
    tick = next(p for p in problems if p.where == "test:tick:1")
    assert tick.path is not None and tick.path.name == "tick.mcfunction"
    assert tick.to_dict()["path"].endswith("tick.mcfunction")


def test_ids_are_checked_with_a_client_jar(pack, fake_jar):
    from datapack_emulator.emulator.vanilla import VanillaAssets

    problems = find_problems(pack, "1.21.4", vanilla=VanillaAssets.from_jar(fake_jar))
    unknown = [p for p in problems if p.code == "unknown-id"]
    assert [p.where for p in unknown] == ["test:tick:7"]
    assert "not_a_mob does not exist" in unknown[0].message
