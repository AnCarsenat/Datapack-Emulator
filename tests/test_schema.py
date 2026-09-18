"""Field checks: what a version's own JSON files hold, and what a pack says
that they never say (``emulator/analysis/schema.py``)."""

from __future__ import annotations

import json
import zipfile
from typing import Any

import pytest

from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.analysis.problems import find_problems
from datapack_emulator.emulator.analysis.schema import (
    Schema,
    learn_from_jar,
    load_schema,
    save_schema,
    schema_for,
    schema_path,
)
from datapack_emulator.emulator.vanilla import VanillaAssets

SHAPED = {
    "type": "minecraft:crafting_shaped",
    "category": "building",
    "pattern": ["##", "##"],
    "key": {"#": "minecraft:oak_planks"},
    "result": {"id": "minecraft:crafting_table", "count": 1},
}
SMELTING = {
    "type": "minecraft:smelting",
    "ingredient": "minecraft:raw_iron",
    "result": {"id": "minecraft:iron_ingot"},
    "experience": 0.7,
    "cookingtime": 200,
}
TABLE = {
    "type": "minecraft:block",
    "pools": [
        {
            "rolls": 1,
            "entries": [
                {
                    "type": "minecraft:item",
                    "name": "minecraft:stone",
                    "functions": [{"function": "minecraft:set_count", "count": 2}],
                }
            ],
            "conditions": [{"condition": "minecraft:survives_explosion"}],
        }
    ],
}


#: what a jar must hold of one kind of file before the checks claim anything
#: about it (analysis.schema.KIND_EVIDENCE)
MANY = 25


@pytest.fixture
def jar(tmp_path):
    """A jar holding a version's own data pack, as the game ships one."""
    path = tmp_path / "minecraft-1.21.4-client.jar"
    entries = {"version.json": {"id": "1.21.4"}}
    for index in range(MANY):
        entries[f"data/minecraft/recipe/shaped{index}.json"] = SHAPED
        entries[f"data/minecraft/loot_table/block{index}.json"] = TABLE
    for index in range(MANY):
        entries[f"data/minecraft/recipe/smelt{index}.json"] = SMELTING
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, json.dumps(content))
    return path


def real_jar():
    """A client jar of a real version, when this machine has one."""
    from datapack_emulator.emulator.vanilla import VanillaLibrary
    from datapack_emulator.settings import PATHS

    jars = VanillaLibrary(PATHS.VANILLA_CACHE).local_jars()
    return next(iter(sorted(jars.items())), (None, None))[1]


def test_a_version_says_what_its_files_hold(jar):
    schema = learn_from_jar(jar)
    assert schema.version_id == "1.21.4"
    recipe = schema.shape("recipe")
    assert recipe.seen == 2 * MANY and recipe.dispatch == "type"
    assert sorted(recipe.variants) == ["minecraft:crafting_shaped", "minecraft:smelting"]

    shaped = recipe.for_type("minecraft:crafting_shaped")
    assert sorted(shaped.required()) == ["category", "key", "pattern", "result", "type"]
    # a recipe's key letters are the pack's own names, not fields of a schema
    assert shaped.fields["key"].named_by_the_pack is True
    assert shaped.fields["result"].fields["count"].kinds == {"a number"}
    assert recipe.describe() == "an object (2 kinds of type)"

    # conditions and item functions are written inside loot tables, not as
    # files of their own: they are collected from where they are used
    assert schema.shape("predicate").for_type("minecraft:survives_explosion") is not None
    assert schema.shape("item_modifier").for_type("minecraft:set_count") is not None


def test_a_file_is_checked_against_the_version_it_is_for(jar):
    schema = learn_from_jar(jar)
    issues = {
        (issue.kind, issue.where): issue
        for issue in schema.check(
            {
                "type": "minecraft:crafting_shaped",
                "category": "building",
                "key": {"#": "minecraft:oak_planks"},
                "result": {"id": "minecraft:crafting_table", "count": "many"},
                "grup": "nope",
            },
            "recipe",
        )
    }
    assert set(issues) == {
        ("unknown-field", "grup"),
        ("missing-field", "pattern"),
        ("wrong-kind", "result.count"),
    }
    # a kind no file of that version holds is a warning, not a rule of the
    # game: the version's own files say what it writes, not what it accepts
    assert issues[("wrong-kind", "result.count")].severity == "warning"
    assert "holds a number here" in issues[("wrong-kind", "result.count")].message
    assert issues[("unknown-field", "grup")].severity == "warning"
    # "every file sets it" does not make a field required: that is a note
    assert issues[("missing-field", "pattern")].severity == "info"

    # a nested typo is found where it is, and a good file says nothing
    nested = dict(TABLE)
    nested["pools"] = [
        {
            "rolls": 1,
            "entries": [
                {
                    "type": "minecraft:item",
                    "name": "minecraft:stone",
                    "functions": [{"function": "minecraft:set_count", "cuont": 2}],
                }
            ],
            "conditions": [{"condition": "minecraft:survives_explosion"}],
        }
    ]
    where = [issue.where for issue in schema.check(nested, "loot_table")]
    assert "pools[0].entries[0].functions[0].cuont" in where
    assert schema.check(TABLE, "loot_table") == []
    # an unknown type is the registry check's business, not this one
    assert schema.check({"type": "test:custom", "whatever": 1}, "recipe") == []


def test_a_schema_is_written_read_and_refused(jar, tmp_path):
    schema = learn_from_jar(jar)
    path = save_schema(schema, schema_path(tmp_path, schema.version_id, jar))
    assert path.name.startswith("1.21.4-")  # one file per jar, not per version id
    again = load_schema(path)
    assert again.version_id == "1.21.4"
    assert sorted(again.folders) == sorted(schema.folders)
    assert again.check(SHAPED, "recipe") == []

    (tmp_path / "other.json").write_text('{"kind": "something else"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a datapack-emulator schema"):
        load_schema(tmp_path / "other.json")
    (tmp_path / "old.json").write_text(
        json.dumps({"kind": "datapack-emulator-schema", "format": 99}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="this build reads"):
        load_schema(tmp_path / "old.json")
    with pytest.raises(ValueError, match="cannot read"):
        load_schema(tmp_path / "nothing.json")


def test_a_learned_schema_is_kept_beside_the_jar(jar, tmp_path):
    assets = VanillaAssets.from_jar(jar)
    first = schema_for(assets, tmp_path)
    assert first is not None and schema_path(tmp_path, "1.21.4", jar).is_file()

    # the second time it is read back, not learned again
    kept = schema_path(tmp_path, "1.21.4", jar)
    kept.write_text(
        json.dumps(Schema(version_id="from the cache").to_dict()),
        encoding="utf-8",
    )
    assert schema_for(assets, tmp_path).version_id == "from the cache"

    # a file this build cannot read is learned again instead of failing
    kept.write_text('{"kind": "datapack-emulator-schema", "format": 99}', encoding="utf-8")
    assert schema_for(assets, tmp_path).version_id == "1.21.4"
    assert schema_for(None, tmp_path) is None  # no jar: nothing is claimed


def test_the_problems_list_reports_fields_the_version_never_uses(jar, make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/recipe/typo.json": {
                    "type": "minecraft:smelting",
                    "ingredient": "minecraft:raw_iron",
                    "result": {"id": "minecraft:iron_ingot"},
                    "experiance": 0.7,
                    "cookingtime": 200,
                },
                "data/test/recipe/fine.json": SMELTING,
            }
        )
    )
    schema = learn_from_jar(jar)
    problems = find_problems(pack, "1.21.4", vanilla=None, schema=schema)
    fields = [problem for problem in problems if problem.code == "schema"]
    assert [(problem.severity, problem.resource, problem.message) for problem in fields] == [
        ("warning", "test:typo", "experiance: no 1.21.4 file read uses this field here"),
        (
            "info",
            "test:typo",
            "experience: missing: every 1.21.4 file read sets it here (it may be optional)",
        ),
    ]
    # without a schema nothing of the kind is reported
    assert not [problem for problem in find_problems(pack, "1.21.4") if problem.code == "schema"]


def test_a_version_agrees_with_its_own_files(jar):
    """The decisive check: every file the schema was learned from must pass it.
    Anything else is the checker disagreeing with the game."""
    schema = learn_from_jar(jar)
    with zipfile.ZipFile(jar) as archive:
        for name in archive.namelist():
            for folder in schema.folders:
                if name.startswith(f"data/minecraft/{folder}/") and name.endswith(".json"):
                    content = json.loads(archive.read(name))
                    assert schema.check(content, folder) == [], name


@pytest.mark.skipif(real_jar() is None, reason="no client jar installed")
def test_a_real_version_agrees_with_its_own_files():
    """The same, against a real client jar when this machine has one: the
    synthetic jars above cannot show what vanilla's own variety does."""
    path = real_jar()
    schema = learn_from_jar(path)
    found = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            for folder in schema.folders:
                if name.startswith(f"data/minecraft/{folder}/") and name.endswith(".json"):
                    for issue in schema.check(json.loads(archive.read(name)), folder):
                        found.append(f"{name}: {issue.where}: {issue.message}")
    assert found[:10] == [] and len(found) == 0


def test_what_a_pack_names_itself_is_not_a_field(jar):
    """Advancement criteria and recipe key letters are named by the pack."""
    schema = learn_from_jar(jar)
    mine = {
        "type": "minecraft:crafting_shaped",
        "category": "building",
        "pattern": ["qz", "vq"],
        "key": {"q": "minecraft:oak_planks", "z": "minecraft:stick", "v": "#minecraft:planks"},
        "result": {"id": "minecraft:crafting_table", "count": 1},
    }
    assert schema.check(mine, "recipe") == []
    # what those names hold is still checked
    mine["key"] = {"q": {"item": "minecraft:oak_planks"}}
    assert [issue.kind for issue in schema.check(mine, "recipe")] == ["wrong-kind"]


def test_nothing_is_claimed_where_learning_stopped(tmp_path):
    """Deeply nested files (worldgen density functions) are learned only so
    far; below that nothing is known, so nothing is reported."""

    def nest(depth):
        value: Any = {"leaf": 1}
        for _ in range(depth):
            value = {"argument": value}
        return value

    path = tmp_path / "minecraft-1.21.4-client.jar"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": "1.21.4"}))
        for index in range(MANY):
            archive.writestr(
                f"data/minecraft/worldgen/noise_settings/n{index}.json",
                json.dumps({"noise_router": nest(14)}),
            )
    schema = learn_from_jar(path)
    assert schema.check({"noise_router": nest(14)}, "worldgen/noise_settings") == []


def test_a_kind_of_object_is_only_dispatched_where_it_is_one(tmp_path):
    """`"type": "top"` is a block state's value, and an entity predicate's
    `type` is an entity id: neither says what the object is."""
    path = tmp_path / "minecraft-1.21.4-client.jar"
    predicate = {
        "condition": "minecraft:block_state_property",
        "block": "minecraft:oak_slab",
        "properties": {"type": "bottom"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": "1.21.4"}))
        for index in range(MANY):
            archive.writestr(
                f"data/minecraft/loot_table/b{index}.json",
                json.dumps(
                    {
                        "type": "minecraft:block",
                        "pools": [
                            {
                                "rolls": 1,
                                "entries": [{"type": "minecraft:item", "name": "x"}],
                                "conditions": [predicate],
                            }
                        ],
                    }
                ),
            )
    schema = learn_from_jar(path)
    properties = schema.shape("predicate").for_type("minecraft:block_state_property")
    assert properties.fields["properties"].dispatch == ""  # not a kind of object
    other = dict(predicate, properties={"type": "top", "waterlogged": "true"})
    assert schema.check(other, "predicate") == []


def test_the_pre_1_21_folder_spellings_are_read(tmp_path):
    """Before 1.21 the jar writes `recipes/`, `loot_tables/`, `advancements/`."""
    path = tmp_path / "minecraft-1.20.4-client.jar"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": "1.20.4"}))
        for index in range(MANY):
            archive.writestr(f"data/minecraft/recipes/r{index}.json", json.dumps(SHAPED))
            archive.writestr(f"data/minecraft/loot_tables/t{index}.json", json.dumps(TABLE))
    schema = learn_from_jar(path)
    assert schema.shape("recipe").seen == MANY
    assert schema.shape("loot_table").seen == MANY


def test_fields_are_not_checked_against_another_versions_files(jar, make_pack):
    """A 1.20 pack is written differently from a 1.21 one: checking it against
    a 1.21 jar would report the difference as the pack's mistake."""
    pack = Datapack.load(make_pack({"data/test/recipe/r.json": SHAPED}))
    schema = learn_from_jar(jar)  # 1.21.4
    problems = [
        problem
        for problem in find_problems(pack, "1.20.4", vanilla=None, schema=schema)
        if problem.code == "schema"
    ]
    assert [(problem.severity, problem.message) for problem in problems] == [
        (
            "info",
            "JSON fields are not checked: the client jar is 1.21.4, the emulated version is 1.20.4",
        )
    ]


def test_a_jar_cannot_choose_where_its_schema_is_written(tmp_path):
    """The version id comes out of the jar: it names the file, never the folder."""
    path = tmp_path / "evil.jar"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": "../../../../pwned"}))
        archive.writestr("data/minecraft/recipe/r.json", json.dumps(SHAPED))
    kept = schema_path(tmp_path / "cache", learn_from_jar(path).version_id, path)
    assert kept.parent == tmp_path / "cache" / "schemas"
    assert "/" not in kept.name and kept.resolve().parent == kept.parent.resolve()
