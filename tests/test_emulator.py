from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
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
    longest = max(len(path) for path in emulator.profiler.tree)
    assert longest <= emulator.profiler.MAX_PATH
