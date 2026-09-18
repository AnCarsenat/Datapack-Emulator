from datapack_emulator.emulator import Datapack, versions
from datapack_emulator.emulator.analysis.explain import describe_range, explain_line


def _rows(line, version="1.21.4", **kwargs):
    return dict(explain_line(line, version, **kwargs))


def test_execute_chain_is_explained_step_by_step(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/a.mcfunction": "say a\n",
                "data/minecraft/tags/function/tick.json": {"values": ["test:a"]},
            }
        )
    )
    view = pack.view_for(versions.parse("1.21.4"))
    rows = _rows(
        "execute as @e[type=!minecraft:player,tag=hat,limit=1] if score @s t matches 1.. "
        "run function #minecraft:tick",
        view=view,
    )
    assert rows["command"].startswith("execute")
    step = rows["step 1: as @e[type=!minecraft:player,tag=hat,limit=1]"]
    assert "every entity, not of type minecraft:player, with tag hat, at most 1" in step
    assert "the score t of @s is 1 or more" in rows["step 2: if score @s t matches 1.."]
    assert rows["run › references #minecraft:tick"] == "tag with 1 function(s): test:a"
    assert rows["loads in 1.21.4"] == "yes" and "ms per run" in rows["estimated cost"]


def test_version_support_and_emulator_coverage_are_reported():
    old = _rows("item replace entity @s armor.head with air", "1.16.1")
    assert old["in the emulator"].startswith("does not exist in 1.16.1 (added in 1.17)")
    assert old["loads in 1.16.1"].startswith("no")
    assert _rows("item replace entity @s armor.head with air")["in the emulator"] == "emulated"
    assert _rows("fill ~ ~ ~ ~1 ~1 ~1 stone")["in the emulator"] == "emulated"
    bossbar = _rows("bossbar list")
    assert bossbar["in the emulator"] == (
        "runs without changing the emulated world: boss bars are not modelled"
    )
    macro = _rows("$function test:x {a:$(a)}", "1.20.1")
    assert "needs the argument(s) a" in macro["macro line"] and "NBT" not in macro
    assert "1.20.2" in macro["macro support"]
    assert _rows("# comment")["does"] == "nothing: a comment"
    assert not _rows("execute if block ~ ~ ~ stone run say x")[
        "step 1: if block ~ ~ ~ stone"
    ].endswith("(not emulated: treated as false)")
    assert _rows("execute if biome ~ ~ ~ plains run say x")[
        "step 1: if biome ~ ~ ~ plains"
    ].endswith("(not emulated: treated as false)")
    assert describe_range("..5") == "5 or less" and describe_range("3") == "exactly 3"


def test_unmodelled_commands_leave_one_note_per_run(make_pack):
    from datapack_emulator.emulator import Emulator

    pack = Datapack.load(
        make_pack({"data/test/function/tick.mcfunction": "recipe give @a *\nparticle flame\n"})
    )
    emulator = Emulator(pack, version="1.21.4")
    emulator.run(ticks=3)
    notes = [r.message for r in emulator.output.records if r.key == "emulator.not_modelled"]
    assert notes == ["'recipe' runs, but recipes are not modelled"]
