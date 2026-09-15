"""One test per bug found in review; each failed before its fix."""

from conftest import chat, game_errors

from src.emulator import Datapack, Emulator
from src.emulator.commands.parser import Command


def run(make_pack, body: str, version: str = "1.21.4", ticks: int = 1, extra=None, **kwargs):
    files = {"data/test/function/tick.mcfunction": body, **(extra or {})}
    pack = Datapack.load(make_pack(files))
    emulator = Emulator(pack, version=version, **kwargs)
    emulator.run(ticks=ticks)
    return emulator


def test_self_selector_applies_its_arguments(make_pack):
    emulator = run(
        make_pack,
        "execute as @a if entity @s[tag=missing] run say wrongly matched\n"
        "tag @a add present\n"
        "execute as @a if entity @s[tag=present] run say matched\n"
        "execute as @a if entity @s[type=minecraft:pig] run say wrongly a pig\n",
    )
    assert chat(emulator.output.records) == ["[Player1] matched"]


def test_return_inside_execute_stops_the_function(make_pack):
    emulator = run(make_pack, "execute if entity @a run return 0\nsay after\n")
    assert "[Server] after" not in chat(emulator.output.records)


def test_execute_if_blocks_keeps_its_run_child():
    command = Command.parse("execute if blocks 0 0 0 1 1 1 5 5 5 all run say hi")
    assert command.subcommands[0].arguments[-1] == "all"
    assert command.child is not None and command.child.name == "say"


def test_schedule_accepts_function_tags(make_pack):
    emulator = run(
        make_pack,
        "execute unless score #s x matches 1 run schedule function #test:group 1t\n"
        "scoreboard players set #s x 1\n",
        ticks=3,
        extra={
            # giving any function tag turns off the fixture's default tick tag
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add x dummy\n",
            "data/test/tags/function/group.json": {"values": ["test:member"]},
            "data/test/function/member.mcfunction": "say member ran\n",
        },
    )
    assert chat(emulator.output.records).count("[Server] member ran") == 1
    assert not game_errors(emulator.output.records)


def test_data_modify_from_reads_the_source(make_pack):
    emulator = run(
        make_pack,
        "data modify storage test:b y set value 7\n"
        "data modify storage test:a x set from storage test:b y\n",
    )
    assert emulator.world.storage["test:a"]["x"] == 7


def test_store_result_in_storage_is_an_integer(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add x dummy\n"
        "scoreboard players set #g x 3\n"
        "execute store result storage test:m n int 1 run scoreboard players get #g x\n"
        "function test:use with storage test:m\n",
        extra={"data/test/function/use.mcfunction": "$scoreboard players set #copy x $(n)\n"},
    )
    assert emulator.world.storage["test:m"]["n"] == 3
    assert isinstance(emulator.world.storage["test:m"]["n"], int)
    assert emulator.world.scoreboard.get("#copy", "x") == 3


def test_scoreboard_swap_updates_both_holders(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add x dummy\n"
        "scoreboard players set #a x 1\n"
        "scoreboard players set #b x 2\n"
        "scoreboard players operation #a x >< #b x\n",
    )
    board = emulator.world.scoreboard
    assert (board.get("#a", "x"), board.get("#b", "x")) == (2, 1)


def test_id_checks_strip_components_nbt_and_particle_options(make_pack, fake_jar):
    from src.emulator.vanilla import VanillaAssets

    emulator = run(
        make_pack,
        "give @a minecraft:diamond[enchantments={levels:{sharpness:1}}]\n"
        "give @a minecraft:diamond{display:{}} 1\n"
        "clear @a minecraft:diamond[custom_name='x']\n"
        "particle minecraft:flame{scale:1} ~ ~ ~\n",
        vanilla=VanillaAssets.from_jar(fake_jar),
    )
    assert game_errors(emulator.output.records) == []


def test_teleport_to_an_entity(make_pack):
    emulator = run(
        make_pack,
        'summon minecraft:marker 10 20 30 {Tags:["target"]}\n'
        "execute as @a run tp @e[tag=target,limit=1]\n",
    )
    player = emulator.world.players[0]
    assert player.position == [10.0, 20.0, 30.0]


def test_tellraw_accepts_snbt_components(make_pack):
    emulator = run(
        make_pack, 'tellraw @a {text:"hi",color:"red"}\ntellraw @a ["",{text:"a"},"b"]\n'
    )
    assert chat(emulator.output.records) == ["[Player1] hi", "[Player1] ab"]
    assert not game_errors(emulator.output.records)


def test_whole_number_max_format_accepts_minor_formats(make_pack):
    from src.emulator import versions

    pack = Datapack.load(make_pack({}, mcmeta={"pack": {"min_format": 88, "max_format": 94}}))
    assert pack.supports(versions.parse("1.21.11"))  # format 94.1
    assert "1.21.11" in [v.id for v in pack.declared_versions()]


def test_pack_view_cache_does_not_keep_packs_alive(make_pack):
    import gc
    import weakref

    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "say one\n"}))
    assert pack.view() is pack.view()  # still cached per instance
    reference = weakref.ref(pack)
    del pack
    gc.collect()
    assert reference() is None


# -- second review ------------------------------------------------------------


def test_store_target_is_bound_where_store_appears(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "summon minecraft:pig\n"
        "execute as @a store result score @s o as @e[type=minecraft:pig] run return 7\n",
    )
    board = emulator.world.scoreboard
    assert board.get("Player1", "o") == 7
    pig = next(e for e in emulator.world.entities if e.type == "minecraft:pig")
    assert board.get(pig.id, "o") is None


def test_store_without_run_stores_the_condition_count(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "summon minecraft:pig\nsummon minecraft:pig\n"
        "execute store result score #pigs o if entity @e[type=minecraft:pig]\n",
    )
    assert emulator.world.scoreboard.get("#pigs", "o") == 2


def test_execute_on_without_a_relation_ends_the_branch(make_pack):
    emulator = run(
        make_pack, "execute as @a on vehicle run kill @s\nexecute as @a on vehicle run say hi\n"
    )
    assert emulator.world.players, "the player must not be killed"
    assert "[Player1] hi" not in chat(emulator.output.records)


def test_if_function_needs_a_non_zero_return(make_pack):
    emulator = run(
        make_pack,
        "execute if function test:noret run say void passed\n"
        "execute if function test:zero run say zero passed\n"
        "execute if function test:seven run say seven passed\n",
        extra={
            "data/test/function/noret.mcfunction": "say inside\n",
            "data/test/function/zero.mcfunction": "return 0\n",
            "data/test/function/seven.mcfunction": "return 7\n",
        },
    )
    messages = chat(emulator.output.records)
    assert "[Server] seven passed" in messages
    assert "[Server] void passed" not in messages and "[Server] zero passed" not in messages


def test_trailing_condition_runs_its_function_once(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "execute store result score #r o if function test:counted\n",
        extra={
            "data/test/function/counted.mcfunction": "scoreboard players add #calls o 1\nreturn 1\n"
        },
    )
    assert emulator.world.scoreboard.get("#calls", "o") == 1
    assert emulator.world.scoreboard.get("#r", "o") == 1


def test_data_merges_are_deep(make_pack):
    emulator = run(
        make_pack,
        "data merge storage t:s {a:{c:2},keep:1}\n"
        "data merge storage t:s {a:{b:1}}\n"
        "data modify storage t:m obj set value {x:1}\n"
        "data modify storage t:m obj merge value {y:2}\n",
    )
    assert emulator.world.storage["t:s"] == {"a": {"c": 2, "b": 1}, "keep": 1}
    assert emulator.world.storage["t:m"]["obj"] == {"x": 1, "y": 2}


def test_data_modify_set_string_slices_the_source(make_pack):
    emulator = run(
        make_pack,
        'data modify storage t:s name set value "minecraft:stone"\n'
        "data modify storage t:s short set string storage t:s name 10\n"
        "data modify storage t:s middle set string storage t:s name 0 9\n",
    )
    storage = emulator.world.storage["t:s"]
    assert storage["short"] == "stone" and storage["middle"] == "minecraft"


def test_data_get_scales_and_floors(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "data modify storage t:s v set value 1.5d\n"
        "data modify storage t:s n set value -1.5d\n"
        "execute store result score #scaled o run data get storage t:s v 10\n"
        "execute store result score #neg o run data get storage t:s n\n",
    )
    board = emulator.world.scoreboard
    assert board.get("#scaled", "o") == 15
    assert board.get("#neg", "o") == -2


def test_append_to_a_non_list_fails(make_pack):
    emulator = run(
        make_pack,
        "data modify storage t:s v set value 5\n"
        "data modify storage t:s v append value 1\n"
        "data modify storage t:s fresh append value 1\n",
    )
    storage = emulator.world.storage["t:s"]
    assert storage["v"] == 5 and storage["fresh"] == [1]
    assert game_errors(emulator.output.records) == ["Expected a list: got 5"]


def test_scheduled_functions_run_after_tick_functions_in_the_same_tick(make_pack):
    emulator = run(
        make_pack,
        "say tick\n"
        "execute unless score #s o matches 1 run schedule function test:later 1t\n"
        "scoreboard players set #s o 1\n",
        ticks=2,
        extra={
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add o dummy\n",
            "data/test/function/later.mcfunction": "say later\n",
        },
    )
    said = [
        (r.tick, r.message)
        for r in emulator.output.records
        if r.message in ("[Server] tick", "[Server] later")
    ]
    assert said == [(0, "[Server] tick"), (0, "[Server] later"), (1, "[Server] tick")]


def test_deep_recursion_is_not_capped_at_64(make_pack):
    emulator = run(
        make_pack,
        "function test:loop\n",
        extra={
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add o dummy\n",
            "data/test/function/loop.mcfunction": (
                "scoreboard players add #d o 1\n"
                "execute if score #d o matches ..299 run function test:loop\n"
            ),
        },
    )
    assert emulator.world.scoreboard.get("#d", "o") == 300


def test_each_version_reads_only_its_folder_spelling(make_pack):
    from src.emulator import versions

    plural_only = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
                "data/test/functions/tick.mcfunction": "say plural ran\n",
            }
        )
    )
    assert plural_only.view_for(versions.parse("1.20.4")).function("test:tick") is not None
    assert plural_only.view_for(versions.parse("1.21.4")).function("test:tick") is None
    modern = Emulator(plural_only, version="1.21.4")
    modern.run(ticks=1)
    assert "[Server] plural ran" not in chat(modern.output.records)

    both = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                "data/test/functions/tick.mcfunction": "say OLD plural\n",
                "data/test/function/tick.mcfunction": "say NEW singular\n",
            }
        )
    )
    assert both.view_for(versions.parse("1.20.4")).function("test:tick").lines == ["say OLD plural"]
    assert both.view_for(versions.parse("1.21.4")).function("test:tick").lines == [
        "say NEW singular"
    ]


def test_selector_nbt_and_volume_arguments(make_pack):
    emulator = run(
        make_pack,
        'summon minecraft:pig 0 0 0 {NoAI:1b,Tags:["calm"]}\n'
        "summon minecraft:pig 20 0 0\n"
        "execute if entity @e[type=minecraft:pig,nbt={NoAI:1b}] run say one calm pig\n"
        'execute if entity @e[type=minecraft:pig,nbt={NoAI:1b,Tags:["calm"]},x=0,y=0,z=0,dx=2,dy=2,dz=2] run say in box\n'
        "execute if entity @e[type=minecraft:pig,x=10,y=0,z=0,dx=2,dy=2,dz=2] run say wrongly in box\n"
        "execute store result score #n o if entity @e[type=minecraft:pig,nbt={NoAI:1b}]\n",
        extra={
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add o dummy\n",
        },
    )
    messages = chat(emulator.output.records)
    assert "[Server] one calm pig" in messages and "[Server] in box" in messages
    assert "[Server] wrongly in box" not in messages
    assert emulator.world.scoreboard.get("#n", "o") == 1


def test_unmodelled_selector_arguments_are_reported_once(make_pack):
    from src.emulator.runtime.output import LogSource

    emulator = run(make_pack, "execute if entity @a[gamemode=creative] run say creative\n", ticks=3)
    notes = [
        r.message
        for r in emulator.output.records
        if r.source is LogSource.EMULATOR and "gamemode" in r.message
    ]
    assert len(notes) == 1


def test_scoreboard_edge_cases(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "scoreboard players set #max o 2147483647\n"
        "scoreboard players add #max o 1\n"
        'scoreboard players display name #max o "Big"\n'
        "scoreboard players set #zero o 0\n"
        "scoreboard players set #a o 5\n"
        "scoreboard players operation #a o /= #zero o\n"
        "scoreboard players set #b o 1\n"
        "scoreboard players set * o 9\n",
    )
    board = emulator.world.scoreboard
    assert board.get("#max", "o") == 9  # wrapped to -2147483648 first, then `*` set it
    assert game_errors(emulator.output.records) == ["Cannot divide by zero"]
    assert board.get("#a", "o") == 9 and board.get("#b", "o") == 9
    assert "*" not in board.scores


def test_scores_wrap_at_32_bits(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "scoreboard players set #max o 2147483647\n"
        "scoreboard players add #max o 1\n",
    )
    assert emulator.world.scoreboard.get("#max", "o") == -2147483648


def test_tellraw_resolves_score_and_selector_components(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "scoreboard players set #a o 42\n"
        "scoreboard players set @a o 7\n"
        'tellraw @a ["score ",{"score":{"name":"#a","objective":"o"}},'
        '" mine ",{"score":{"name":"*","objective":"o"}}," who ",{"selector":"@a"}]\n',
    )
    assert chat(emulator.output.records) == ["[Player1] score 42 mine 7 who Player1"]


def test_reports_escape_pack_content(make_pack):
    from src.emulator import TestEngine

    pack = Datapack.load(
        make_pack(
            {"data/test/function/tick.mcfunction": "say hi\n"}, name="<img src=x onerror=alert(1)>"
        )
    )
    run_result = TestEngine(pack, ticks=1).run_version("1.21.4")
    matrix = TestEngine.to_html([run_result], pack.name)
    profile = run_result.profiler.to_html(f"Function profiler — {pack.name}")
    for page in (matrix, profile):
        assert "<img" not in page and "&lt;img" in page
