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
        "summon minecraft:marker 10 20 30 {Tags:[\"target\"]}\n"
        "execute as @a run tp @e[tag=target,limit=1]\n",
    )
    player = emulator.world.players[0]
    assert player.position == [10.0, 20.0, 30.0]


def test_tellraw_accepts_snbt_components(make_pack):
    emulator = run(make_pack, 'tellraw @a {text:"hi",color:"red"}\ntellraw @a ["",{text:"a"},"b"]\n')
    assert chat(emulator.output.records) == ["[Player1] hi", "[Player1] ab"]
    assert not game_errors(emulator.output.records)
