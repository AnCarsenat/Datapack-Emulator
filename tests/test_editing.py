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


def test_completion_reads_macro_and_slash_lines(pack):
    from datapack_emulator.emulator import versions

    view = pack.view_for(versions.parse("1.21.4"))
    assert texts("$function test:hel", version="1.21.4", view=view) == [
        "test:helper",
        "test:helper_two",
    ]
    assert texts("/exe", version="1.21.4") == ["execute"]
    assert texts("$exe", version="1.21.4") == ["execute"]
    assert word_at("$function test:hel") == ("test:hel", 10)
    assert word_at("/exe") == ("exe", 1)
    # inside a string nothing is a command
    assert texts('say "hello al', version="1.21.4") == []


def test_completion_offers_each_thing_where_it_goes(pack):
    from datapack_emulator.emulator import versions

    view = pack.view_for(versions.parse("1.21.4"))
    subcommands = texts("execute ", version="1.21.4")
    assert subcommands.count("run") == 1  # `run` is a feature like the others
    assert texts("execute as ", version="1.21.4") == []  # an entity is wanted, not a subcommand
    assert texts("schedule ", version="1.21.4", view=view) == ["clear", "function"]
    assert texts("schedule function ", version="1.21.4", view=view)[0] == "test:helper"
    assert texts("schedule clear ", version="1.21.4", view=view)[0] == "test:helper"
    assert texts("function test:tick ", version="1.21.4", view=view) == []  # `with`, not an id


def test_completion_reads_a_negated_selector_type():
    class FakeVanilla:
        registries = {"entity_type": frozenset({"minecraft:pig", "minecraft:pillager"})}

    assert texts("execute as @e[type=!pi", version="1.21.4", vanilla=FakeVanilla()) == [
        "minecraft:pig",
        "minecraft:pillager",
    ]


def test_renaming_leaves_the_tag_of_the_same_id_alone(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/helper.mcfunction": "say hi\n",
                "data/test/tags/function/helper.json": {"values": ["test:helper"]},
                "data/test/function/tick.mcfunction": (
                    "function #test:helper\nfunction test:helper\n"
                ),
            }
        )
    )
    rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
    root = pack.path
    assert (root / "data/test/function/tick.mcfunction").read_text().splitlines() == [
        "function #test:helper",  # the tag is another resource, it keeps its id
        "function test:worker",
    ]
    tag = json.loads((root / "data/test/tags/function/helper.json").read_text())
    assert tag["values"] == ["test:worker"]  # what the tag points at did move


def test_renaming_follows_a_reference_without_its_namespace(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/minecraft/function/helper.mcfunction": "say hi\n",
                "data/minecraft/function/tick.mcfunction": (
                    "function helper\nfunction minecraft:helper\n"
                ),
            }
        )
    )
    rename_function(pack, "minecraft:helper", "minecraft:worker", version="1.21.4", apply=True)
    text = (pack.path / "data/minecraft/function/tick.mcfunction").read_text()
    assert text.splitlines() == ["function minecraft:worker", "function minecraft:worker"]


def test_renaming_stays_inside_the_pack(pack):
    root = pack.path
    (root / "notes.json").write_text('{"note": "test:helper is nice"}\n', encoding="utf-8")
    with pytest.raises(RenameError) as error:
        rename_function(pack, "test:helper", "test:../../escaped", version="1.21.4")
    assert "not a path inside the pack" in str(error.value)
    edits = rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
    assert all("notes.json" not in edit.format() for edit in edits)  # outside data/
    assert (root / "notes.json").read_text() == '{"note": "test:helper is nice"}\n'


def test_renaming_moves_every_copy_of_the_function(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "pack.mcmeta": {
                    "pack": {"pack_format": 61, "description": "overlaid"},
                    "overlays": {
                        "entries": [{"formats": [4, 60], "directory": "old"}],
                    },
                },
                "data/test/function/helper.mcfunction": "say new\n",
                "data/test/functions/helper.mcfunction": "say plural\n",
                "old/data/test/functions/helper.mcfunction": "say old\n",
                "data/test/function/tick.mcfunction": "function test:helper\n",
            }
        )
    )
    rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
    root = pack.path
    for gone in (
        "data/test/function/helper.mcfunction",
        "data/test/functions/helper.mcfunction",
        "old/data/test/functions/helper.mcfunction",
    ):
        assert not (root / gone).exists(), gone
    assert (root / "data/test/function/worker.mcfunction").read_text() == "say new\n"
    assert (root / "data/test/functions/worker.mcfunction").read_text() == "say plural\n"
    assert (root / "old/data/test/functions/worker.mcfunction").read_text() == "say old\n"


def test_renaming_writes_nothing_when_a_file_cannot_be_written(pack):
    root = pack.path
    locked = root / "data/test/function/tick.mcfunction"
    locked.chmod(0o444)
    try:
        with pytest.raises(RenameError) as error:
            rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
        assert "cannot write" in str(error.value)
    finally:
        locked.chmod(0o644)
    assert (root / "data/test/function/helper.mcfunction").exists()  # nothing moved
    assert "test:helper" in locked.read_text()
    advancement = (root / "data/test/advancement/done.json").read_text()
    assert "test:helper" in advancement  # and nothing else was written either


def test_completion_is_quiet_where_a_command_cannot_go(pack):
    from datapack_emulator.emulator import versions

    view = pack.view_for(versions.parse("1.21.4"))
    for line in (
        'say "hello run ',  # inside a string
        "say run ",  # the word run in a message
        "tag @s add run ",  # ... or in an argument
        "# this will run ",  # a comment
        "execute as ",  # an entity is wanted
        "execute positioned 1 2 ",  # a third coordinate is wanted
    ):
        assert texts(line, version="1.21.4", view=view) == [], line
    # a tab separates words like a space does
    assert texts("execute\tif ", version="1.21.4") == texts("execute if ", version="1.21.4")
    # after an execute chain's run, a whole command is wanted again
    assert "say" in texts("execute as @a run sa", version="1.21.4")
    # selector options the version does not have are not offered
    assert "predicate" not in texts("execute as @e[", version="1.14")
    assert "predicate" in texts("execute as @e[", version="1.21.4")


def test_renaming_keeps_a_file_as_it_was_written(make_pack):
    """CRLF stays CRLF, and a rollback restores the bytes."""
    root = make_pack({"data/test/function/helper.mcfunction": "say hi\n"})
    crlf = root / "data/test/function/tick.mcfunction"
    crlf.write_bytes(b"function test:helper\r\nsay done\r\n")
    pack = Datapack.load(root)

    rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
    assert crlf.read_bytes() == b"function test:worker\r\nsay done\r\n"

    # and when a write fails, every file is byte-identical afterwards
    before = crlf.read_bytes()
    pack = Datapack.load(root)
    (root / "data/test/function").chmod(0o555)
    try:
        with pytest.raises(RenameError):
            rename_function(pack, "test:worker", "other:deep/one", version="1.21.4", apply=True)
    finally:
        (root / "data/test/function").chmod(0o755)
    assert crlf.read_bytes() == before
    assert not (root / "data/other").exists()  # the folders it made are gone too


def test_renaming_does_not_follow_a_symlink_out_of_the_pack(make_pack, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"note": "test:helper"}\n', encoding="utf-8")
    root = make_pack(
        {
            "data/test/function/helper.mcfunction": "say hi\n",
            "data/test/function/tick.mcfunction": "function test:helper\n",
        }
    )
    (root / "data/test/linked.json").symlink_to(outside)
    pack = Datapack.load(root)

    rename_function(pack, "test:helper", "test:worker", version="1.21.4", apply=True)
    assert outside.read_text() == '{"note": "test:helper"}\n'
    assert (root / "data/test/function/tick.mcfunction").read_text() == "function test:worker\n"


def test_renaming_a_minecraft_function_leaves_json_strings_alone(make_pack):
    """A bare id is a function call in a command; in JSON it is any string."""
    root = make_pack(
        {
            "data/minecraft/function/helper.mcfunction": "say hi\n",
            "data/minecraft/function/tick.mcfunction": "function helper\n",
            "data/test/predicate/p.json": {"condition": "minecraft:value_check", "helper": 3},
        }
    )
    pack = Datapack.load(root)
    rename_function(pack, "minecraft:helper", "minecraft:worker", version="1.21.4", apply=True)
    assert (root / "data/minecraft/function/tick.mcfunction").read_text() == (
        "function minecraft:worker\n"
    )
    assert json.loads((root / "data/test/predicate/p.json").read_text())["helper"] == 3


def test_renaming_refuses_an_id_that_is_not_a_place_in_the_pack(pack):
    for new_id in (".:x", "test:./x", "test:a//b", "test:x/"):
        with pytest.raises(RenameError):
            rename_function(pack, "test:helper", new_id, version="1.21.4")


def test_renaming_moves_only_the_pack_the_function_comes_from(make_pack):
    from datapack_emulator.emulator.datapack import DatapackSet

    first = make_pack(
        {
            "data/test/function/helper.mcfunction": "say first\n",
            "data/test/function/tick.mcfunction": "function test:helper\n",
        }
    )
    second = make_pack(
        {
            "data/test/function/helper.mcfunction": "say second\n",
            "data/test/function/other.mcfunction": "function test:helper\n",
        }
    )
    packs = DatapackSet.load([first, second])

    rename_function(packs, "test:helper", "test:worker", version="1.21.4", apply=True)
    # the later pack wins the id, so its file is the one that moves
    assert (second / "data/test/function/worker.mcfunction").read_text() == "say second\n"
    assert (first / "data/test/function/helper.mcfunction").read_text() == "say first\n"
    # references follow in both, so the set still calls the function that runs
    for pack, name in ((first, "tick"), (second, "other")):
        assert (pack / f"data/test/function/{name}.mcfunction").read_text() == (
            "function test:worker\n"
        )
