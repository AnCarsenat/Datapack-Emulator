import functools
import hashlib
import http.server
import json
import threading

import pytest
from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator import vanilla as vanilla_module
from datapack_emulator.emulator.vanilla import DownloadCancelled, VanillaAssets, VanillaLibrary


def test_reads_registries_tags_and_lang(fake_jar):
    assets = VanillaAssets.from_jar(fake_jar)
    assert assets.version_id == "9.9" and assets.pack_format == (107, 1)
    assert assets.knows("block", "stone") is True
    assert assets.knows("item", "minecraft:emerald") is False
    assert assets.knows("sound", "anything") is None  # registry not in the jar
    assert assets.registries["entity_type"] == {
        "minecraft:pig",
        "minecraft:skeleton",
        "minecraft:stray",
    }  # entity.minecraft.villager.farmer is a sub-key, not an entity
    assert assets.resolve_tag("entity_type", "#minecraft:skeletons") == {
        "minecraft:skeleton",
        "minecraft:stray",
    }


def test_emulator_uses_jar_registries_messages_and_tags(make_pack, fake_jar):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": (
                    "summon minecraft:armour_stand\n"
                    "setblock 0 0 0 minecraft:stoen\n"
                    "summon minecraft:stray\n"
                    "execute as @e[type=#minecraft:skeletons] run say rattle\n"
                    "scoreboard players set @a nope 1\n"
                )
            }
        )
    )
    emulator = Emulator(pack, version="1.21.4", vanilla=VanillaAssets.from_jar(fake_jar))
    emulator.run(ticks=1)
    errors = game_errors(emulator.output.records)
    assert "Unknown ID: minecraft:armour_stand" in errors
    assert "Unknown block type 'minecraft:stoen'" in errors
    assert "No such objective: nope" in errors  # wording from the jar, not the built-in table
    assert chat(emulator.output.records) == ["[Stray] rattle"]


def test_library_finds_jars_in_launcher_layout(tmp_path, fake_jar):
    launcher = tmp_path / "versions" / "9.9"
    launcher.mkdir(parents=True)
    fake_jar.rename(launcher / "9.9.jar")
    library = VanillaLibrary(tmp_path / "cache", search_dirs=[tmp_path / "versions"])
    assert library.find("9.9") == launcher / "9.9.jar"
    assert library.load("9.9").version_id == "9.9"
    assert library.load("1.0") is None


@pytest.fixture
def mojang(tmp_path, monkeypatch):
    """A local stand-in for piston-meta serving one 512 KiB 'jar'."""
    payload = bytes(range(256)) * 2048
    served = tmp_path / "served"
    served.mkdir()
    (served / "client.jar").write_bytes(payload)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    monkeypatch.setattr(http.server.SimpleHTTPRequestHandler, "log_message", lambda *a: None)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def publish(sha1: str) -> None:
        (served / "v.json").write_text(
            json.dumps(
                {
                    "downloads": {
                        "client": {"url": f"{base}/client.jar", "size": len(payload), "sha1": sha1}
                    }
                }
            )
        )

    (served / "manifest.json").write_text(
        json.dumps({"versions": [{"id": "9.9", "url": f"{base}/v.json"}]})
    )
    publish(hashlib.sha1(payload).hexdigest())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(vanilla_module, "MANIFEST_URL", f"{base}/manifest.json")
    VanillaLibrary.manifest.cache_clear()
    yield payload, publish
    server.shutdown()
    VanillaLibrary.manifest.cache_clear()


def test_download_streams_and_verifies(tmp_path, mojang):
    payload, _ = mojang
    library = VanillaLibrary(tmp_path / "cache", search_dirs=[])
    progress = []
    path = library.download("9.9", on_bytes=lambda done, total: progress.append((done, total)))
    assert path.read_bytes() == payload
    assert progress[-1] == (len(payload), len(payload))
    assert not list(path.parent.glob("*.part"))


def test_download_cancel_and_bad_checksum_leave_nothing(tmp_path, mojang):
    _, publish = mojang
    library = VanillaLibrary(tmp_path / "cache", search_dirs=[])
    target = tmp_path / "cache" / "9.9" / "minecraft-9.9-client.jar"

    with pytest.raises(DownloadCancelled):
        library.download("9.9", cancelled=lambda: True)
    assert not target.exists() and not list(target.parent.glob("*.part"))

    publish("0" * 40)
    with pytest.raises(OSError, match="corrupt"):
        library.download("9.9")
    assert not target.exists() and not list(target.parent.glob("*.part"))
