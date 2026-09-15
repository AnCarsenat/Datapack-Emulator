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
