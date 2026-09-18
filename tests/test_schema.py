"""Field checks: what a version's own JSON files hold, and what a pack says
that they never say (``emulator/analysis/schema.py``)."""

from __future__ import annotations

import json
import zipfile

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


@pytest.fixture
def jar(tmp_path):
    """A jar holding a version's own data pack, as the game ships one."""
    path = tmp_path / "minecraft-1.21.4-client.jar"
    entries = {"version.json": {"id": "1.21.4"}}
    for index in range(6):
        entries[f"data/minecraft/recipe/shaped{index}.json"] = SHAPED
        entries[f"data/minecraft/loot_table/block{index}.json"] = TABLE
    for index in range(4):
        entries[f"data/minecraft/recipe/smelt{index}.json"] = SMELTING
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, json.dumps(content))
    return path


def test_a_version_says_what_its_files_hold(jar):
    schema = learn_from_jar(jar)
    assert schema.version_id == "1.21.4"
    recipe = schema.shape("recipe")
    assert recipe.seen == 10 and recipe.dispatch == "type"
    assert sorted(recipe.variants) == ["minecraft:crafting_shaped", "minecraft:smelting"]

    shaped = recipe.for_type("minecraft:crafting_shaped")
    assert sorted(shaped.required()) == ["category", "key", "pattern", "result", "type"]
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
    assert issues[("wrong-kind", "result.count")].severity == "error"
    assert "always a number" in issues[("wrong-kind", "result.count")].message
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
        ("warning", "test:typo", "experiance: no 1.21.4 file uses this field here"),
        (
            "info",
            "test:typo",
            "experience: missing: every 1.21.4 file sets it here (it may be optional)",
        ),
    ]
    # without a schema nothing of the kind is reported
    assert not [problem for problem in find_problems(pack, "1.21.4") if problem.code == "schema"]
