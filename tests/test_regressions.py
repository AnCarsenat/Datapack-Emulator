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
