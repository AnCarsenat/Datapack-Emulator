import pytest
from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.analysis.profiler import Profiler
from datapack_emulator.emulator.runtime.output import LogLevel, LogSource


def run(make_pack, body: str, version: str = "1.21.4", ticks: int = 1, extra=None, **kwargs):
    files = {"data/test/function/tick.mcfunction": body, **(extra or {})}
    pack = Datapack.load(make_pack(files))
    emulator = Emulator(pack, version=version, **kwargs)
    emulator.run(ticks=ticks)
    return emulator


def test_scoreboard_and_execute_store(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add x dummy\n"
        "scoreboard players add #g x 5\n"
        "execute store result score #copy x run scoreboard players get #g x\n"
        "scoreboard players operation #copy x *= #g x\n",
    )
    board = emulator.world.scoreboard
    assert board.get("#g", "x") == 5
    assert board.get("#copy", "x") == 25


def test_execute_as_and_conditions(make_pack):
    emulator = run(
        make_pack,
        "tag @a add seen\n"
        "execute as @a[tag=seen] run say tagged\n"
        "execute unless entity @e[type=minecraft:pig] run say no pigs\n",
        players=2,
    )
    messages = chat(emulator.output.records)
    assert messages.count("[Player1] tagged") == 1 and messages.count("[Player2] tagged") == 1
    assert "[Server] no pigs" in messages


def test_unknown_objective_uses_vanilla_wording(make_pack):
    emulator = run(make_pack, "scoreboard players set @a missing 1\n")
    assert game_errors(emulator.output.records) == ["Unknown scoreboard objective 'missing'"]


def test_return_stops_the_function(make_pack):
    emulator = run(make_pack, "say before\nreturn 1\nsay after\n")
    messages = chat(emulator.output.records)
    assert "[Server] before" in messages and "[Server] after" not in messages


def test_schedule_replace_postpones_and_append_fires(make_pack):
    replace = run(
        make_pack,
        "schedule function test:later 2t\n",
        ticks=4,
        extra={"data/test/function/later.mcfunction": "say fired\n"},
    )
    assert "[Server] fired" not in chat(replace.output.records)

    append = run(
        make_pack,
        "execute unless score #once x matches 1 run schedule function test:later 2t\n"
        "scoreboard objectives add x dummy\n"
        "scoreboard players set #once x 1\n",
        ticks=4,
        extra={"data/test/function/later.mcfunction": "say fired\n"},
    )
    assert chat(append.output.records).count("[Server] fired") == 1


def test_macro_with_storage_and_missing_argument(make_pack):
    emulator = run(
        make_pack,
        'data modify storage test:mem name set value "Alex"\n'
        "function test:greet with storage test:mem\n"
        "function test:greet\n",
        extra={"data/test/function/greet.mcfunction": "$say hello $(name)\n"},
    )
    assert "[Server] hello Alex" in chat(emulator.output.records)
    assert any("Missing argument name" in error for error in game_errors(emulator.output.records))


def test_command_missing_from_version_fails_the_function_load(make_pack):
    from datapack_emulator.emulator.commands.parser import Command

    emulator = run(
        make_pack,
        "",
        version="1.19.4",
        extra={  # 1.19.4 reads the plural folders
            "data/test/functions/tick.mcfunction": "say before\nreturn 1\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        },
    )
    messages = [r.message for r in emulator.output.records if r.source is LogSource.GAME]
    assert (
        "Failed to load function test:tick: Whilst parsing command on line 2: "
        "Unknown or incomplete command, see below for error"
    ) in messages
    assert (
        "Couldn't load tag minecraft:tick as it is missing following references: test:tick"
    ) in messages
    assert "[Server] before" not in chat(emulator.output.records)  # nothing of it runs

    # typed, the same command gets the game's parse error with its marker
    emulator.run_command(Command.parse("return 1"), emulator.root_context())
    errors = game_errors(emulator.output.records)
    assert errors[-1].startswith("Unknown or incomplete command")
    assert errors[-1].endswith("return<--[HERE]")
    notes = [
        r.message
        for r in emulator.output.records
        if r.source is LogSource.EMULATOR and r.level >= LogLevel.WARNING
    ]
    assert any("added in 1.20" in note for note in notes)


def test_recursion_is_capped(make_pack):
    emulator = run(
        make_pack,
        "function test:loop\n",
        extra={"data/test/function/loop.mcfunction": "function test:loop\n"},
    )
    assert emulator.profiler.entries["test:loop"]["calls"] == Emulator.MAX_DEPTH - 1
    notes = [r.message for r in emulator.output.records if "depth limit" in r.message]
    assert notes and "1024" in notes[0]


def test_tag_remove_without_tag_reports_failure(make_pack):
    emulator = run(make_pack, "tag @a remove nothing\n")
    assert game_errors(emulator.output.records) == ["Target does not have this tag"]


def test_trigger_needs_enable(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add t trigger\n"
        "execute as @a run trigger t\n"
        "scoreboard players enable @a t\n"
        "execute as @a run trigger t\n",
    )
    assert game_errors(emulator.output.records) == ["You cannot trigger this objective yet"]
    assert emulator.world.scoreboard.get("Player1", "t") == 1


def test_profiler_charges_time(make_pack):
    emulator = run(make_pack, "say hi\n", ticks=3)
    entry = emulator.profiler.entries["test:tick"]
    assert entry["calls"] == 3 and entry["total_us"] > 0
    assert emulator.profiler.ticks == 3 and len(emulator.profiler.tick_times) == 3


def test_trigger_on_missing_or_wrong_objective(make_pack):
    emulator = run(
        make_pack,
        "execute as @a run trigger nope\n"
        "scoreboard objectives add plain dummy\n"
        "execute as @a run trigger plain\n",
    )
    assert game_errors(emulator.output.records) == [
        "Unknown scoreboard objective 'nope'",
        "You can only trigger objectives that are 'trigger' type",
    ]


def _board(make_pack, body: str, **kwargs):
    emulator = run(make_pack, body, **kwargs)
    return emulator, emulator.world.scoreboard


def test_operation_combines_every_target_with_every_source(make_pack):
    emulator, board = _board(
        make_pack,
        "scoreboard objectives add n dummy\n"
        "scoreboard players set a n 2\n"
        "scoreboard players set b n 3\n"
        "scoreboard players set c n 10\n"
        "scoreboard players set d n 20\n"
        "scoreboard players operation a n += c n\n"
        "scoreboard players operation #sum n = #zero n\n"
        "scoreboard players operation #sum n += * n\n"
        "scoreboard players operation c n >< d n\n"
        "scoreboard players set m n -7\n"
        "scoreboard players set two n 2\n"
        "scoreboard players operation m n /= two n\n"
        "scoreboard players set r n -7\n"
        "scoreboard players operation r n %= two n\n"
        "scoreboard players set big n 2147483647\n"
        "scoreboard players add big n 1\n",
    )
    assert board.get("a", "n") == 12
    assert board.get("#zero", "n") == 0  # a missing source counts as 0 and is created
    # `*` is every tracked holder, #sum included: it is added last, to itself
    assert board.get("#sum", "n") == (12 + 3 + 10 + 20 + 0) * 2
    assert (board.get("c", "n"), board.get("d", "n")) == (20, 10)
    assert board.get("m", "n") == -4 and board.get("r", "n") == 1  # floorDiv, floorMod
    assert board.get("big", "n") == -(2**31)  # Java int overflow


def test_scoreboard_errors_and_feedback_use_vanilla_wording(make_pack):
    emulator, board = _board(
        make_pack,
        "scoreboard objectives add hp health\n"
        "scoreboard objectives add t trigger\n"
        "scoreboard objectives add d dummy\n"
        "scoreboard objectives add bad nonsense\n"
        "scoreboard players set @a hp 5\n"
        "scoreboard players enable @a d\n"
        "scoreboard players set @a d nope\n"
        "scoreboard players operation @a d /= #zero d\n"
        "scoreboard players get @e[type=minecraft:player] d\n",
        players=2,
    )
    assert game_errors(emulator.output.records) == [
        "Unknown criterion 'nonsense'",
        "Scoreboard objective 'hp' is read-only",
        "Enable only works on trigger-objectives",
        "Invalid integer 'nope'",
        "Cannot divide by zero",
        "Only one entity is allowed, but the provided selector allows more than one",
    ]


def test_scoreboard_feedback_and_reset(make_pack):
    emulator, board = _board(
        make_pack,
        'scoreboard objectives add d dummy "Deaths"\n'
        "scoreboard players set @a d 4\n"
        "scoreboard players remove Player1 d 1\n"
        "scoreboard players reset Player2\n"
        "scoreboard objectives setdisplay sidebar d\n"
        "scoreboard players list Player1\n",
        players=2,
    )
    feedback = [r.message for r in emulator.output.records if r.key.startswith("commands.")]
    assert "Set [Deaths] for 2 entities to 4" in feedback
    assert "Removed 1 from [Deaths] for Player1 (now 3)" in feedback
    assert "Reset scores for Player2" in feedback
    assert "Set display slot sidebar to show objective [Deaths]" in feedback
    assert "Player1 has 1 score(s):" in feedback and "[Deaths]: 3" in feedback
    assert board.get("Player2", "d") is None and board.display_slots == {"sidebar": "d"}


def test_scoreboard_remembers_the_values_a_score_takes(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add c dummy\nscoreboard players add #n c 1\n",
        ticks=3,
    )
    history = list(emulator.world.scoreboard.history[("#n", "c")])
    assert [value for _, value in history] == [1, 2, 3]
    assert [tick for tick, _ in history] == [0, 1, 2]


def test_summoned_entities_keep_their_full_nbt(make_pack):
    emulator = run(
        make_pack,
        "scoreboard objectives add v dummy\n"
        "summon minecraft:armor_stand 1 2 3 "
        '{NoGravity:1b,Tags:["a","b"],CustomName:\'"Keeper"\',Rotation:[90f,0f]}\n'
        "tag @e[type=minecraft:armor_stand] add c\n"
        "data merge entity @e[type=minecraft:armor_stand,limit=1] {Pos:[5.0d,6.0d,7.0d],Glowing:1b}\n"
        'data modify entity @e[tag=a,limit=1] Tags append value "d"\n'
        "data remove entity @e[tag=a,limit=1] NoGravity\n"
        "tp @e[tag=a] 8 9 10 45 10\n"
        "execute store result entity @e[tag=a,limit=1] Health float 0.5 run scoreboard players "
        "set #x v 7\n"
        "data get entity @e[tag=a,limit=1]\n"
        "data get entity @e[tag=a,limit=1] Pos[0]\n",
    )
    (stand,) = [entity for entity in emulator.world.entities if not entity.is_player]
    assert stand.position == [8.0, 9.0, 10.0] and stand.rotation == [45.0, 10.0]
    assert stand.tags == {"a", "b", "c", "d"}
    data = stand.data()
    assert data["Glowing"] == 1 and "NoGravity" not in data
    assert data["Health"] == 3.5  # execute store ... float 0.5
    assert data["Pos"] == [8.0, 9.0, 10.0] and data["id"] == "minecraft:armor_stand"
    assert len(data["UUID"]) == 4 and data["Tags"] == ["a", "b", "c", "d"]
    assert stand.display == "Keeper"
    feedback = [r.message for r in emulator.output.records if r.key.startswith("commands.data")]
    assert any(
        m.startswith("Keeper has the following entity data: {Pos: [8.0d, 9.0d, 10.0d]")
        for m in feedback
    )
    assert "Keeper has the following entity data: 8.0d" in feedback


def test_player_data_is_read_only_and_killed_players_stay(make_pack):
    emulator = run(
        make_pack,
        "data merge entity Player1 {Glowing:1b}\n"
        "kill @a\n"
        "execute if data entity Player1 Health run say still here\n",
    )
    assert game_errors(emulator.output.records) == ["Unable to modify player data"]
    assert [entity.name for entity in emulator.world.players] == ["Player1"]
    assert "[Server] still here" in chat(emulator.output.records)


def test_profiler_builds_a_call_tree_with_per_tick_costs(make_pack):
    emulator = run(
        make_pack,
        "function test:a\nfunction test:a\nschedule function test:b 1t\n",
        ticks=4,
        extra={
            "data/test/function/a.mcfunction": "say a\nfunction test:b\n",
            "data/test/function/b.mcfunction": "say b\n",
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
        },
    )
    profiler = emulator.profiler
    tree = profiler.tree
    tick_path = ("#minecraft:tick", "test:tick")
    assert tree[tick_path]["calls"] == 4
    assert tree[(*tick_path, "test:a")]["calls"] == 8
    assert tree[(*tick_path, "test:a", "test:b")]["calls"] == 8
    assert ("<schedule>", "test:b") in tree
    # a path's total includes what it calls; self only its own lines
    a = tree[(*tick_path, "test:a")]
    assert a["total_us"] > a["self_us"]
    one = profiler.per_tick(tree[tick_path])
    assert one["calls"] == 1 and abs(one["total_us"] * 4 - tree[tick_path]["total_us"]) < 1e-6
    roots = [path for path, _ in profiler.children(())]
    assert ("#minecraft:tick",) in roots
    html = profiler.to_html("t")
    assert "Call tree, per tick" in html and "ms/tick" in html


def test_profiler_tree_caps_recursion(make_pack):
    emulator = run(
        make_pack,
        "function test:loop\n",
        extra={"data/test/function/loop.mcfunction": "function test:loop\n"},
    )
    profiler = emulator.profiler
    longest = max(len(path) for path in profiler.tree)
    assert longest == profiler.MAX_PATH + 1  # the "…" node under the 32nd call
    deepest = next(path for path in profiler.tree if len(path) == profiler.MAX_PATH + 1)
    assert deepest[-1] == profiler.DEEPER
    parent = deepest[:-1]
    index = profiler.children_index()
    children_total = sum(profiler.tree[child]["total_us"] for child in index.get(parent, []))
    node = profiler.tree[parent]
    assert abs(node["self_us"] + children_total - node["total_us"]) < 1e-6  # nothing counted twice


def test_profiler_report_does_not_link_schedules(make_pack):
    emulator = run(
        make_pack,
        "schedule function test:b 1t\n",
        ticks=3,
        extra={"data/test/function/b.mcfunction": "say b\n"},
    )
    html = emulator.profiler.to_html("t")
    assert "&lt;schedule&gt;" in html and 'data-function="&lt;schedule&gt;"' not in html


def test_profiler_charges_lines_and_compares_two_runs(make_pack):
    def one(body: str):
        return run(
            make_pack,
            body,
            ticks=2,
            extra={"data/minecraft/tags/function/tick.json": {"values": ["test:tick"]}},
        ).profiler

    before = one("say a\n")
    after = one("say a\nsay b\nsay c\n")

    hot = after.hot_commands()
    assert {(function_id, line) for function_id, line, _ in hot} == {
        ("test:tick", 1),
        ("test:tick", 2),
        ("test:tick", 3),
    }
    lines = {line: stats for _, line, stats in after.hot_commands()}
    assert lines[2]["raw"] == "say b" and lines[2]["runs"] == 2

    rows = {row["function"]: row for row in after.compare(before)}
    tick = rows["test:tick"]
    assert tick["delta_us"] > 0  # three lines cost more than one
    assert tick["before_commands"] == 1 and tick["after_commands"] == 3

    kept = before.snapshot()  # a copy: the run it was taken from may go on
    before.charge("test:tick", 1000.0, line=1, raw="say a")
    assert kept.total_us < before.total_us
    assert Profiler.from_dict(kept.to_dict()).commands == kept.commands

    html = after.to_html("t", baseline=kept)
    assert "Compared with the kept run" in html and "Dearest lines" in html
    assert "Flame graph, per tick" in html and 'class="frame' in html
    assert "say b" in html


def test_profiler_counts_a_line_once_per_run_of_it(make_pack):
    emulator = run(
        make_pack,
        "say plain\nexecute as @a run say each\n",
        ticks=2,
        players=3,
        extra={"data/minecraft/tags/function/tick.json": {"values": ["test:tick"]}},
    )
    lines = {line: stats for _, line, stats in emulator.profiler.hot_commands()}
    # the line ran twice, whatever the selector matched
    assert lines[2]["runs"] == 2 and lines[1]["runs"] == 2
    assert lines[2]["raw"] == "execute as @a run say each"
    # but it is charged for the execute and for each of the three branches
    assert lines[2]["self_us"] > lines[1]["self_us"] * 3
    # the per-line numbers still add up to what the function costs
    per_line = sum(stats["self_us"] for stats in lines.values())
    assert abs(per_line - emulator.profiler.entries["test:tick"]["self_us"]) < 1e-6


def test_a_profile_is_read_back_or_refused(make_pack):
    profiler = run(
        make_pack,
        "say a\n",
        ticks=2,
        extra={"data/minecraft/tags/function/tick.json": {"values": ["test:tick"]}},
    ).profiler
    profiler.pack, profiler.version = "demo", "1.21.4"
    data = profiler.to_dict()
    read = Profiler.from_dict(data)
    assert read.pack == "demo" and read.version == "1.21.4" and read.ticks == profiler.ticks
    assert read.commands == profiler.commands  # runs stay whole numbers
    for wrong in ([], "text", 3, {}, {"kind": "something else"}):
        with pytest.raises(ValueError):
            Profiler.from_dict(wrong)
    with pytest.raises(ValueError):
        Profiler.from_dict({**data, "entries": {"test:tick": {"self_us": "soon"}}})


def test_profiler_reset_forgets_the_lines(make_pack):
    emulator = run(
        make_pack,
        "say a\n",
        extra={"data/minecraft/tags/function/tick.json": {"values": ["test:tick"]}},
    )
    assert emulator.profiler.commands
    emulator.profiler.reset()
    assert emulator.profiler.commands == {} and emulator.profiler.hot_commands() == []


def test_the_flame_graph_shares_a_tick_and_the_report_compares_lines(make_pack):
    def one(body: str):
        return run(
            make_pack,
            body,
            ticks=2,
            extra={"data/minecraft/tags/function/tick.json": {"values": ["test:tick"]}},
        ).profiler

    before = one("say a\n")
    after = one("say a\nfunction test:more\n")
    after.pack, after.version = "demo", "1.21.4"
    rows = {(row["function"], row["line"]): row for row in after.compare_commands(before)}
    assert rows[("test:tick", 2)]["delta_us"] > 0  # the new line costs what it costs
    assert rows[("test:tick", 1)]["delta_us"] == pytest.approx(0)

    html = after.to_html("t", baseline=before)
    assert "The lines that changed" in html
    widths = [float(part.split("%")[0]) for part in html.split('style="width:')[1:]]
    assert widths and max(widths) <= 100.0
