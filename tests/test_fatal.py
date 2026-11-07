"""Problems that stop the world or the pack from loading: severity "fatal".

Modelled on a real failure: a 1.21.11 pack on a 26.2 server stopped with
"Failed to load datapacks, can't proceed with server load" because its
dimension type lacked ``has_ender_dragon_fight``, its noise settings lacked
``noise_router.preliminary_surface_level`` and a configured feature used the
removed ``minecraft:random_patch``.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.analysis.problems import (
    SEVERITIES,
    find_problems,
    is_world_registry,
    load_blockers,
)
from datapack_emulator.emulator.analysis.schema import learn_from_jar


def dimension(dragon: bool, light: float) -> dict:
    return {
        "has_ender_dragon_fight": dragon,  # written even when false: required
        "ambient_light": light,
        "attributes": {"minecraft:visual/fog_color": "#ffffff"},  # never empty: may be optional
        "height": 384,
    }


FEATURES = {
    "ore": {"type": "minecraft:ore", "config": {"size": 9, "targets": []}},
    "tree": {"type": "minecraft:tree", "config": {"trunk_provider": {}, "foliage": 2}},
    "lake": {"type": "minecraft:lake", "config": {"fluid": {}, "barrier": {}}},
    "spring": {"type": "minecraft:spring_feature", "config": {"state": {}, "rock_count": 4}},
}


@pytest.fixture
def schema(tmp_path):
    path = tmp_path / "minecraft-26.2-client.jar"
    entries = {"version.json": {"id": "26.2"}}
    for name, (dragon, light) in {
        "overworld": (False, 0.0),
        "the_nether": (False, 0.1),
        "the_end": (True, 0.25),
        "overworld_caves": (False, 0.0),
    }.items():
        entries[f"data/minecraft/dimension_type/{name}.json"] = dimension(dragon, light)
    for copy in range(3):
        for name, feature in FEATURES.items():
            entries[f"data/minecraft/worldgen/configured_feature/{name}{copy}.json"] = feature
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, json.dumps(content))
    return learn_from_jar(path)


def problems_of(make_pack, files, schema=None, mcmeta=None):
    pack = Datapack.load(make_pack(files, mcmeta=mcmeta))
    return find_problems(pack, "26.2", vanilla=None, schema=schema)


def test_world_registries():
    for registry in ("worldgen/biome", "dimension_type", "dimension", "enchantment", "damage_type"):
        assert is_world_registry(registry)
    for registry in ("function", "recipe", "loot_table", "advancement", "tags/function"):
        assert not is_world_registry(registry)
    assert SEVERITIES[0] == "fatal"


def test_a_required_field_missing_from_a_dimension_type_is_fatal(make_pack, schema):
    broken = {"ambient_light": 0.5, "height": 256}  # no dragon fight, no attributes
    problems = problems_of(make_pack, {"data/test/dimension_type/void.json": broken}, schema)
    fatal = load_blockers(problems)
    assert [(p.code, p.resource) for p in fatal] == [("world-registry", "test:void")]
    assert "has_ender_dragon_fight" in fatal[0].message
    assert "will not load the world" in fatal[0].message
    # a field vanilla always sets but never to a "nothing" value may be optional
    notes = [p for p in problems if "attributes" in p.message]
    assert notes and notes[0].severity == "info"
    # fatal comes first in the list
    assert problems[0].severity == "fatal"


def test_a_kind_the_version_does_not_have_is_fatal(make_pack, schema):
    gone = {"type": "minecraft:random_patch", "config": {"tries": 32}}
    problems = problems_of(
        make_pack, {"data/test/worldgen/configured_feature/patch.json": gone}, schema
    )
    (fatal,) = load_blockers(problems)
    assert "minecraft:random_patch" in fatal.message
    fine = problems_of(
        make_pack, {"data/test/worldgen/configured_feature/ore.json": FEATURES["ore"]}, schema
    )
    assert not load_blockers(fine)


def test_unparsable_world_registry_file_is_fatal_but_a_recipe_is_not(make_pack):
    files = {
        "data/test/worldgen/biome/bad.json": "{not json",
        "data/test/recipe/bad.json": "{not json",
    }
    severities = sorted(p.severity for p in problems_of(make_pack, files) if p.code == "json")
    assert severities == ["error", "fatal"]


def test_an_unreadable_pack_mcmeta_is_fatal(tmp_path):
    root = tmp_path / "pack"
    (root / "data" / "test" / "function").mkdir(parents=True)
    (root / "data" / "test" / "function" / "a.mcfunction").write_text("say hi\n")
    (root / "pack.mcmeta").write_text("{broken")
    problems = find_problems(Datapack.load(root), "26.2")
    assert any(p.severity == "fatal" and p.code == "pack" for p in problems)


def test_agent_reports_load_blockers(make_pack, monkeypatch, tmp_path):
    from datapack_emulator import agent
    from datapack_emulator.emulator.vanilla import VanillaLibrary

    monkeypatch.setattr(
        agent, "default_library", lambda: VanillaLibrary(tmp_path / "j", search_dirs=[])
    )
    pack = make_pack(
        {
            "data/test/worldgen/biome/bad.json": "{not json",
            "data/test/function/tick.mcfunction": "say hi\n",
        }
    )
    checked = agent.check(pack, "1.21.4")
    assert checked["ok"] is False
    assert checked["load_blockers"] and checked["load_blockers"][0]["resource"] == "test:bad"
    assert "stop the world or the pack from loading" in checked["summary"]
    assert checked["results"][0]["counts"]["fatal"] == 1
    ran = agent.run(pack, "1.21.4", ticks=1)
    assert ran["ok"] is False and ran["summary"].startswith("WILL NOT LOAD")
    assert ran["load_blockers"]


def test_cli_check_exits_1_on_fatal(make_pack, capsys):
    from datapack_emulator.cli import main

    pack = make_pack({"data/test/worldgen/biome/bad.json": "{not json"})
    assert main(["check", str(pack), "--version", "1.21.4", "--no-vanilla"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("fatal") and "FATAL" in out
    assert (
        main(["check", str(pack), "--version", "1.21.4", "--no-vanilla", "--severity", "fatal"])
        == 1
    )


def test_what_vanilla_never_does_is_not_fatal(tmp_path, make_pack):
    """Real false positives on 26.2: vanilla writes ``fieldOf().orElse()``
    fields even at their default, keys environment attributes by id, and uses
    few of the kinds a version has (no enchantment runs a function)."""
    path = tmp_path / "minecraft-26.2-client.jar"
    tree = {"ignore_vines": False, "decorators": []}
    effect = {"effect": {"type": "minecraft:ignite", "duration": 1}}
    entries = {"version.json": {"id": "26.2"}}
    for index in range(4):
        entries[f"data/minecraft/trim_pattern/p{index}.json"] = {"asset_id": "a", "decal": False}
        entries[f"data/minecraft/worldgen/configured_feature/t{index}.json"] = {
            "type": "minecraft:tree",
            "config": tree,
        }
        entries[f"data/minecraft/dimension_type/d{index}.json"] = {
            "height": 256,
            "attributes": {"minecraft:gameplay/respawn_anchor_works": index == 0},
        }
        entries[f"data/minecraft/enchantment/e{index}.json"] = {
            "anvil_cost": 1,
            "effects": {"minecraft:post_attack": [effect]},
        }
    for kind in ("minecraft:ignite", "minecraft:explode", "minecraft:damage_entity"):
        for index in range(3):
            entries[f"data/minecraft/enchantment/{kind[10:]}{index}.json"] = {
                "anvil_cost": 1,
                "effects": {
                    "minecraft:tick": [{"effect": {"type": kind, kind[10:]: {"x": index}}}]
                },
            }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, json.dumps(content))
    schema = learn_from_jar(path)
    files = {
        "data/test/trim_pattern/p.json": {"asset_id": "a"},
        "data/test/worldgen/configured_feature/t.json": {
            "type": "minecraft:tree",
            "config": {"decorators": []},
        },
        "data/test/dimension_type/d.json": {"height": 256, "attributes": {}},
        "data/test/enchantment/e.json": {
            "anvil_cost": 1,
            "effects": {
                "minecraft:tick": [
                    {"effect": {"type": "minecraft:run_function", "function": "a:b"}}
                ]
            },
        },
    }
    problems = problems_of(make_pack, files, schema)
    assert not load_blockers(problems), [p.format() for p in load_blockers(problems)]
