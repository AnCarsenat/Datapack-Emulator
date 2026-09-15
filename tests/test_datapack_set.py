from conftest import chat

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.datapack import Datapack, DatapackSet
from datapack_emulator.emulator.runtime.emulator import Emulator

V1214 = versions.parse("1.21.4")


def _two(make_pack, replace_tick: bool = False):
    first = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["one:tick"]},
            "data/one/function/tick.mcfunction": "say one\nfunction shared:hello\n",
            "data/shared/function/hello.mcfunction": "say hello from one\n",
        },
        name="one",
    )
    second = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {
                "values": ["two:tick"],
                **({"replace": True} if replace_tick else {}),
            },
            "data/two/function/tick.mcfunction": "say two\n",
            "data/shared/function/hello.mcfunction": "say hello from two\n",
        },
        name="two",
    )
    return DatapackSet.load([first, second])


def test_packs_run_together_tags_merge_and_later_packs_win(make_pack):
    packs = _two(make_pack)
    view = packs.view_for(V1214)
    assert view.resolve_function_tag("#minecraft:tick") == ["one:tick", "two:tick"]
    assert set(view.functions) >= {"one:tick", "two:tick", "shared:hello"}
    emulator = Emulator(packs, version=V1214)
    emulator.run(ticks=1)
    messages = chat(emulator.output.records)
    assert "[Server] one" in messages and "[Server] two" in messages
    assert "[Server] hello from two" in messages  # the later pack's copy of the function
    assert packs.name == "one + two"


def test_replace_in_a_later_tag_drops_earlier_values(make_pack):
    view = _two(make_pack, replace_tick=True).view_for(V1214)
    assert view.resolve_function_tag("#minecraft:tick") == ["two:tick"]


def test_removing_and_reordering_packs(make_pack):
    packs = _two(make_pack)
    assert packs.move(1, -1) and [pack.name for pack in packs] == ["two", "one"]
    view = packs.view_for(V1214)
    assert view.function("shared:hello").path.parts[-5] == "one"  # one is now later
    removed = packs.remove(0)
    assert removed.name == "two" and packs.view_for(V1214).function("two:tick") is None
    assert len(packs) == 1 and packs.name == "one"


def test_set_compatibility_looks_at_every_pack(make_pack):
    old = make_pack(
        {"data/a/function/x.mcfunction": "say x\n"},
        mcmeta={"pack": {"pack_format": 10}},
        name="old",
    )
    new = make_pack({"data/b/function/y.mcfunction": "say y\n"}, name="new")
    packs = DatapackSet([Datapack.load(new), Datapack.load(old)])
    result = packs.compatibility(V1214)
    assert not result.compatible and result.reason.startswith("old:")
    assert not packs.supports(V1214)


def test_archive_datapack_entries_must_stay_inside_the_archive(tmp_path, monkeypatch):
    import json
    import zipfile

    import pytest

    from datapack_emulator.project import Project
    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    for entry in (str(outside), "../../../outside_secret"):
        archive_path = tmp_path / "evil.dpemu"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "project.json", json.dumps({"archive_format": 2, "datapacks": [entry]})
            )
        with pytest.raises(ValueError, match="unsafe datapack path"):
            Project.load(archive_path)


def test_folder_spelling_is_checked_per_pack(make_pack):
    from datapack_emulator.emulator.engine import TestEngine
    from datapack_emulator.emulator.runtime.output import OutputBus

    modern = make_pack({"data/a/function/tick.mcfunction": "say a\n"}, name="modern")
    legacy = make_pack({"data/b/functions/tick.mcfunction": "say b\n"}, name="legacy")
    run = TestEngine(DatapackSet.load([modern, legacy]), ticks=1).run_version("1.21.4", OutputBus())
    warnings = [r.message for r in run.records if "reads 'function/'" in r.message]
    assert warnings == [
        "legacy: 1.21.4 reads 'function/', not 'functions/' — those files are ignored"
    ]


def test_an_unreadable_tag_file_does_not_hide_the_other_packs_on_1_16_1(make_pack):
    from datapack_emulator.emulator.commands.registry import command_set
    from datapack_emulator.emulator.runtime.library import FunctionLibrary

    version = versions.parse("1.16.1")
    objects = make_pack(
        {
            "data/minecraft/tags/functions/tick.json": {
                "values": [{"id": "c:x", "required": False}]
            },
            "data/c/functions/x.mcfunction": "say x\n",
        },
        name="objects",
    )
    plain = make_pack(
        {
            "data/minecraft/tags/functions/tick.json": {"values": ["d:y"]},
            "data/d/functions/y.mcfunction": "say y\n",
        },
        name="plain",
    )
    packs = DatapackSet.load([objects, plain])
    library = FunctionLibrary.build(packs.view_for(version), command_set(version))
    assert library.resolve_tag("#minecraft:tick") == ["d:y"]
    assert "#minecraft:tick" in library.tag_failures  # objects' file is still reported


def test_set_compatibility_keeps_every_packs_server_log(make_pack):
    version = versions.parse("1.20.2")
    ok = make_pack(
        {"data/a/function/x.mcfunction": "say\n"}, mcmeta={"pack": {"pack_format": 18}}, name="ok"
    )
    warn = make_pack(
        {"data/b/function/y.mcfunction": "say\n"},
        mcmeta={"pack": {"pack_format": 18, "supported_formats": [20, 30]}},
        name="warn",
    )
    alone = Datapack.load(warn).compatibility(version).server_log
    together = DatapackSet.load([ok, warn]).compatibility(version).server_log
    assert alone and together == [f"warn: {line}" for line in alone]
