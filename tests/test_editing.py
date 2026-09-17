"""Completion and renaming: what can be typed next, and following an id."""

from __future__ import annotations

import json

import pytest

from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.analysis.completion import Candidate, complete, word_at
from datapack_emulator.emulator.analysis.rename import RenameError, rename_function

PACK = {
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
    "data/test/function/tick.mcfunction": (
        "function test:helper\nexecute as @a at @s run function test:helper\n"
    ),
    "data/test/function/helper.mcfunction": "say hi\n",
    "data/test/function/helper_two.mcfunction": "say other\n",
    "data/test/advancement/done.json": {
        "criteria": {"tick": {"trigger": "minecraft:tick"}},
        "rewards": {"function": "test:helper"},
    },
}


@pytest.fixture
def pack(make_pack):
    return Datapack.load(make_pack(PACK))


def texts(line, cursor=None, **kwargs):
    return [candidate.text for candidate in complete(line, cursor, **kwargs)]


def test_the_word_being_typed_is_found():
    assert word_at("execute if bl") == ("bl", 11)
    assert word_at("execute as @e[type=", 19) == ("", 19)
    assert word_at("say hello", 3) == ("say", 0)
    assert word_at("", None) == ("", 0)


def test_commands_subcommands_and_conditions_come_from_the_version():
    assert "execute" in texts("exe", version="1.21.4")
    assert texts("execute as @a run sa", version="1.21.4") == [
        "save-all",
        "save-off",
        "save-on",
        "say",
    ]
    subcommands = texts("execute ", version="1.21.4")
    assert {"if", "unless", "as", "at", "run"} <= set(subcommands)
    assert "on" in texts("execute ", version="1.21.4")
    assert "on" not in texts("execute ", version="1.19")  # added in 1.19.4
    assert "function" in texts("execute if ", version="1.21.4")
    assert "function" not in texts("execute if ", version="1.20")
    assert texts("execute store ", version="1.21.4") == ["result", "success"]
    assert "score" in texts("execute store result ", version="1.21.4")


def test_selector_options_and_function_ids(pack):
    from datapack_emulator.emulator import versions

    view = pack.view_for(versions.parse("1.21.4"))
    assert "type" in texts("execute as @e[", version="1.21.4")
    assert "limit" in texts("execute as @e[type=pig,", version="1.21.4")
    assert texts("execute as @e[type=pi", version="1.21.4") == []  # a value, no jar loaded
    assert texts("function ", version="1.21.4", view=view) == [
        "test:helper",
        "test:helper_two",
        "test:tick",
        "#minecraft:tick",
    ]
    assert texts("function test:hel", version="1.21.4", view=view) == [
        "test:helper",
        "test:helper_two",
    ]
    assert texts("execute as @a run function ", version="1.21.4", view=view)[0] == "test:helper"
    assert texts("say hello ", version="1.21.4", view=view) == []


def test_ids_come_from_a_client_jar():
    class FakeVanilla:
        registries = {"entity_type": frozenset({"minecraft:pig", "minecraft:pillager"})}

    assert texts("summon pi", version="1.21.4", vanilla=FakeVanilla()) == [
        "minecraft:pig",
        "minecraft:pillager",
    ]
    assert texts("summon ", version="1.21.4") == []  # without a jar, nothing is claimed
    assert Candidate("pig", "id", "entity_type").label == "pig  (id: entity_type)"


def test_renaming_follows_every_reference(pack, tmp_path):
    edits = rename_function(pack, "test:helper", "test:deep/worker", version="1.21.4")
    shown = [edit.format() for edit in edits]
    assert shown[0].startswith("move ") and "deep/worker.mcfunction" in shown[0]
    assert any("tick.mcfunction:1" in line for line in shown)
    assert any("advancement/done.json" in line for line in shown)
    assert all("helper_two" not in line.split(" -> ")[1] for line in shown[1:])  # not a prefix
    root = pack.path
    assert (root / "data/test/function/helper.mcfunction").exists()  # nothing written yet

    rename_function(pack, "test:helper", "test:deep/worker", version="1.21.4", apply=True)
    assert not (root / "data/test/function/helper.mcfunction").exists()
    assert (root / "data/test/function/deep/worker.mcfunction").read_text() == "say hi\n"
    tick = (root / "data/test/function/tick.mcfunction").read_text()
    assert tick.splitlines() == [
        "function test:deep/worker",
        "execute as @a at @s run function test:deep/worker",
    ]
    rewards = json.loads((root / "data/test/advancement/done.json").read_text())
    assert rewards["rewards"]["function"] == "test:deep/worker"
    assert (root / "data/test/function/helper_two.mcfunction").read_text() == "say other\n"


def test_renaming_refuses_what_it_cannot_do(pack):
    for old, new, said in (
        ("test:helper", "test:helper", "the new id is the old one"),
        ("test:helper", "Test:Helper", "not a resource location"),
        ("test:nothing", "test:other", "is not a function"),
        ("test:helper", "test:helper_two", "already a function"),
    ):
        with pytest.raises(RenameError) as error:
            rename_function(pack, old, new, version="1.21.4")
        assert said in str(error.value)
