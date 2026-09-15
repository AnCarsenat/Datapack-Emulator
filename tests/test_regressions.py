"""One test per bug found in review; each failed before its fix."""

from pathlib import Path

from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.commands.parser import Command


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
    from datapack_emulator.emulator.vanilla import VanillaAssets

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
    from datapack_emulator.emulator import versions

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
    from datapack_emulator.emulator import versions

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
    from datapack_emulator.emulator.runtime.output import LogSource

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
    from datapack_emulator.emulator import TestEngine

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


def test_nbt_filter_sees_tags_and_reports_unmodelled_data(make_pack):
    from datapack_emulator.emulator.runtime.output import LogSource

    emulator = run(
        make_pack,
        "summon minecraft:pig\n"
        "tag @e[type=minecraft:pig] add b\n"
        'execute if entity @e[type=minecraft:pig,nbt={Tags:["b"]}] run say tagged pig\n'
        'execute if entity @a[nbt={SelectedItem:{id:"minecraft:stick"}}] run say holding\n',
    )
    assert "[Server] tagged pig" in chat(emulator.output.records)
    notes = [r.message for r in emulator.output.records if r.source is LogSource.EMULATOR]
    assert any("SelectedItem" in note for note in notes)


def test_star_means_every_tracked_holder(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "scoreboard objectives add p dummy\n"
        "scoreboard players set #a p 1\n"
        "scoreboard players set * o 5\n",
    )
    assert emulator.world.scoreboard.get("#a", "o") == 5


def test_data_get_saturates_and_set_string_needs_a_value(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add o dummy\n"
        "data modify storage t:s big set value 1e20d\n"
        "data modify storage t:s obj set value {A:1}\n"
        "execute store result score #big o run data get storage t:s big\n"
        "data modify storage t:s text set string storage t:s obj\n",
    )
    assert emulator.world.scoreboard.get("#big", "o") == 2147483647
    assert "text" not in emulator.world.storage["t:s"]


# -- vanilla behaviour hat_v2 relies on ----------------------------------------


def test_failures_inside_functions_are_silent_but_typed_commands_are_not(make_pack):
    from conftest import visible_errors

    from datapack_emulator.emulator.commands.parser import Command
    from datapack_emulator.emulator.runtime.output import LogLevel

    emulator = run(make_pack, "tag @a remove nothing\n", ticks=2)
    failures = [r for r in emulator.output.records if r.message == "Target does not have this tag"]
    assert len(failures) == 2 and all(r.level == LogLevel.DEBUG and r.failure for r in failures)
    assert visible_errors(emulator.output.records) == []

    emulator.run_command(Command.parse("tag @a remove nothing"), emulator.root_context())
    assert visible_errors(emulator.output.records) == ["Target does not have this tag"]


def test_emulator_limitations_are_noted_once_per_run(make_pack):
    from datapack_emulator.emulator.runtime.output import LogLevel, LogSource

    emulator = run(
        make_pack,
        "execute if block 0 0 0 minecraft:stone run say stone\ndata get block 0 0 0 Items\n",
        ticks=5,
    )
    notes = [r for r in emulator.output.records if r.source is LogSource.EMULATOR]
    assert len([r for r in notes if "'block'" in r.message]) == 1
    assert len([r for r in notes if "data get block" in r.message]) == 1
    assert all(r.level <= LogLevel.INFO for r in notes if "block" in r.message)


def test_tags_with_missing_required_entries_are_dropped(make_pack):
    from datapack_emulator.emulator import Datapack, Emulator

    pack = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/function/tick.json": {"values": ["test:a", "test:gone"]},
                "data/minecraft/tags/function/load.json": {
                    "values": ["test:a", {"id": "test:gone", "required": False}]
                },
                "data/test/function/a.mcfunction": "say a\n",
            }
        )
    )
    modern = Emulator(pack, version="1.21.4")
    assert modern.library.resolve_tag("#minecraft:tick") == []
    assert modern.library.resolve_tag("#minecraft:load") == ["test:a"]
    assert "#minecraft:tick" in modern.library.tag_failures

    legacy_pack = Datapack.load(
        make_pack(
            {
                "data/minecraft/tags/functions/load.json": {
                    "values": [{"id": "test:a", "required": False}]
                },
                "data/test/functions/a.mcfunction": "say a\n",
            }
        )
    )
    assert Emulator(legacy_pack, version="1.16.1").library.resolve_tag("#minecraft:load") == []
    assert Emulator(legacy_pack, version="1.16.2").library.resolve_tag("#minecraft:load") == [
        "test:a"
    ]


def test_first_tick_runs_load_and_tick_in_the_versions_order(make_pack):
    from datapack_emulator.emulator import Datapack, Emulator

    def order(version: str) -> list[str]:
        pack = Datapack.load(
            make_pack(
                {
                    "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
                    "data/minecraft/tags/functions/load.json": {"values": ["test:load"]},
                    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
                    "data/test/functions/tick.mcfunction": "say tick\n",
                    "data/test/functions/load.mcfunction": "say load\n",
                    "data/test/function/tick.mcfunction": "say tick\n",
                    "data/test/function/load.mcfunction": "say load\n",
                }
            )
        )
        emulator = Emulator(pack, version=version)
        emulator.run(ticks=2)
        return chat(emulator.output.records)

    assert order("1.19.2") == ["[Server] tick", "[Server] load", "[Server] tick"]
    assert order("1.19.3") == ["[Server] load", "[Server] tick", "[Server] tick"]


# -- third review ---------------------------------------------------------------


def test_macro_lines_fail_the_load_before_macros_existed(make_pack):
    from datapack_emulator.emulator import Datapack, Emulator

    pack = Datapack.load(
        make_pack(
            {
                "data/test/functions/m.mcfunction": "say before\n$say $(x)\n",
                "data/test/function/m.mcfunction": "say before\n$say $(x)\n",
            }
        )
    )
    assert "test:m" in Emulator(pack, version="1.20.1").library.function_failures
    assert "test:m" in Emulator(pack, version="1.20.4").library.functions


def test_tick_history_is_bounded_but_statistics_are_complete(make_pack):
    from datapack_emulator.emulator.analysis.profiler import Profiler

    profiler = Profiler()
    for index in range(Profiler.TICK_HISTORY + 500):
        profiler.record_tick(float(index % 7))
    profiler.record_tick(99.0)
    assert len(profiler.tick_times) == Profiler.TICK_HISTORY
    assert profiler.ticks == Profiler.TICK_HISTORY + 501
    assert profiler.worst_tick_us == 99.0
    expected = (sum(i % 7 for i in range(Profiler.TICK_HISTORY + 500)) + 99.0) / profiler.ticks
    assert abs(profiler.average_tick_us - expected) < 1e-9


def test_settings_only_trust_a_real_checkout(tmp_path):
    from datapack_emulator.settings.main import find_checkout, user_dirs

    other = tmp_path / "other_project"
    (other / "src" / "whatever").mkdir(parents=True)
    (other / "pyproject.toml").write_text("")
    module = other / "venv" / "site-packages" / "datapack_emulator" / "settings" / "main.py"
    assert find_checkout(module) is None

    checkout = tmp_path / "checkout"
    (checkout / "src" / "datapack_emulator" / "settings").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("")
    assert (
        find_checkout(checkout / "src" / "datapack_emulator" / "settings" / "main.py") == checkout
    )

    data, cache = user_dirs("linux", {"XDG_DATA_HOME": "/d", "XDG_CACHE_HOME": "/c"})
    assert (data, cache) == (Path("/d/datapack-emulator"), Path("/c/datapack-emulator"))
    data, cache = user_dirs("win32", {"APPDATA": "/r", "LOCALAPPDATA": "/l"})
    assert data == Path("/r/datapack-emulator") and cache.parent == Path("/l/datapack-emulator")


def test_run_load_alone_reports_load_failures(make_pack):
    from datapack_emulator.emulator import Datapack, Emulator
    from datapack_emulator.emulator.runtime.output import OutputBus

    pack = Datapack.load(make_pack({"data/test/function/bad.mcfunction": "notacommand\n"}))
    output = OutputBus()
    Emulator(pack, version="1.21.4", output=output).run_load()
    assert any("Failed to load function test:bad" in r.message for r in output.records)


def test_hand_edited_tests_do_not_break_the_project():
    from datapack_emulator.emulator.testing import CommandTest

    assert CommandTest.from_dict({"command": "say hi", "at_tick": "soon"}).at_tick == 0
    assert CommandTest.from_dict({"command": "say hi", "at_tick": None}).at_tick == 0
    assert CommandTest.from_dict({"at_tick": "5"}).at_tick == 5


# -- world-state review ----------------------------------------------------------


def _world(make_pack, body: str, ticks: int = 1, **kwargs):
    from datapack_emulator.emulator import Datapack, Emulator

    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": body}))
    emulator = Emulator(pack, version="1.21.4", **kwargs)
    emulator.run(ticks=ticks)
    return emulator


def test_tests_keep_their_records_when_the_bus_rotates(make_pack):
    from datapack_emulator.emulator import Datapack, Emulator
    from datapack_emulator.emulator.runtime.output import OutputBus
    from datapack_emulator.emulator.testing import CommandTest, run_tests

    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "say tick\n"}))
    emulator = Emulator(pack, version="1.21.4", output=OutputBus(limit=40))
    tests = [CommandTest("say hello", at_tick=t, expect="hello") for t in range(50, 70)]
    results = run_tests(pack, tests, emulator=emulator)
    assert all(result.passed for result in results), [r.reason for r in results if not r.passed]


def test_data_modify_reaches_into_lists(make_pack):
    emulator = _world(
        make_pack,
        "summon minecraft:marker 1 2 3\n"
        "data modify entity @e[type=minecraft:marker,limit=1] Pos[0] set value 5.0d\n"
        "data modify storage t:m list set value [1, 2, 3]\n"
        "data modify storage t:m list[-1] set value 9\n"
        "data remove storage t:m list[0]\n"
        "data modify storage t:m list[7] set value 1\n"
        "execute store result entity @e[type=minecraft:marker,limit=1] Pos[1] double 1 run "
        "data get storage t:m list[1]\n",
    )
    (marker,) = [entity for entity in emulator.world.entities if not entity.is_player]
    assert marker.position == [5.0, 9.0, 3.0]
    assert emulator.world.storage["t:m"]["list"] == [2, 9]
    errors = [r.message for r in emulator.output.records if r.failure]
    assert "Found no elements matching list[7]" in errors


def test_tags_survive_removing_the_tags_key_and_rotation_is_normalised(make_pack):
    emulator = _world(
        make_pack,
        'summon minecraft:marker 1 2 3 {Tags:["a"]}\n'
        "data remove entity @e[type=minecraft:marker,limit=1] Tags\n"
        "tp @e[type=minecraft:marker] ~ ~ ~ 400 100\n",
    )
    (marker,) = [entity for entity in emulator.world.entities if not entity.is_player]
    assert marker.tags == {"a"}
    assert marker.rotation == [40.0, 90.0]


def test_reset_locks_a_trigger_and_killed_entities_lose_their_scores(make_pack):
    from datapack_emulator.emulator.commands.parser import Command

    emulator = _world(
        make_pack,
        "scoreboard objectives add t trigger\n"
        "scoreboard players enable Player1 t\n"
        "scoreboard players reset Player1 t\n"
        "summon minecraft:marker\n"
        "scoreboard players set @e[type=minecraft:marker] t 3\n"
        "kill @e[type=minecraft:marker]\n"
        "scoreboard objectives setdisplay bogus t\n",
    )
    board = emulator.world.scoreboard
    trigger = Command.parse("execute as Player1 run trigger t")
    assert not emulator.run_command(trigger, emulator.root_context()).success
    assert board.tracked() == [] and not any(key[0] != "Player1" for key in board.history)
    errors = [r.message for r in emulator.output.records if r.failure]
    assert "Unknown display slot 'bogus'" in errors


# -- ui-polish review ---------------------------------------------------------------


def test_nbt_paths_accept_quoted_keys(make_pack):
    emulator = _world(
        make_pack,
        'data merge storage t:s {"a.b":1,"minecraft:custom_data":1,"with space":1}\n'
        'data modify storage t:s "a.b" set value 42\n'
        'data modify storage t:s "minecraft:custom_data" set value 42\n'
        "data modify storage t:s 'with space' set value 42\n",
    )
    assert emulator.world.storage["t:s"] == {
        "a.b": 42,
        "minecraft:custom_data": 42,
        "with space": 42,
    }


def test_score_graph_segments_leave_gaps_for_resets():
    from collections import deque

    from datapack_emulator.emulator.runtime.world import Scoreboard
    from datapack_emulator.window.score_graph import step_segments

    board = Scoreboard()
    board.history[("p", "o")] = deque([(1, 5), (3, None), (5, 7), (8, None)])
    assert step_segments(board, "p", "o", 10) == [([1, 3], [5]), ([5, 8], [7])]
    board.history[("p", "o")] = deque([(1, 5), (4, 6)])
    assert step_segments(board, "p", "o", 10) == [([1, 4, 10], [5, 6])]


def test_unknown_commands_and_bad_ranges_are_described_honestly():
    from datapack_emulator.emulator.analysis.explain import explain_line
    from datapack_emulator.emulator.testing import _valid_range

    rows = dict(explain_line("foo bar", "1.21.4"))
    assert rows["in the emulator"].startswith("not a command in any version")
    assert not _valid_range("4..1") and not _valid_range("nan") and not _valid_range("1..2..3")
    assert _valid_range("1..4") and _valid_range("..-3") and _valid_range("5")
