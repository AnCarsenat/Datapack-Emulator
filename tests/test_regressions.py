"""One test per bug found in review; each failed before its fix."""

from conftest import chat, game_errors

from src.emulator import Datapack, Emulator, versions
from src.emulator.commands.parser import Command
from src.emulator.vanilla import VanillaAssets


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
