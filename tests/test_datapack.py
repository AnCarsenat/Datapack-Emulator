import struct
import zlib

from src.emulator import versions
from src.emulator.datapack import Datapack, PackMCMETA


def _mcmeta(tmp_path, pack):
    import json

    path = tmp_path / "pack.mcmeta"
    path.write_text(json.dumps({"pack": pack}), encoding="utf-8")
    return PackMCMETA(path)


def test_pack_format_forms(tmp_path):
    from src.emulator.datapack import ANY_MINOR

    assert _mcmeta(tmp_path, {"pack_format": 61}).format_tuple == (61, 0)
    assert _mcmeta(tmp_path, {"pack_format": 107.1}).format_tuple == (107, 1)
    ranged = _mcmeta(tmp_path, {"pack_format": 15, "supported_formats": [10, 20]})
    assert ranged.format_range == ((10, 0), (20, ANY_MINOR))
    objected = _mcmeta(tmp_path, {"supported_formats": {"min_inclusive": 18, "max_inclusive": 41}})
    assert objected.format_range == ((18, 0), (41, ANY_MINOR))
    modern = _mcmeta(tmp_path, {"min_format": [88, 0], "max_format": [94, 1]})
    assert modern.format_range == ((88, 0), (94, 1))


def test_whole_number_bounds_follow_vanilla(tmp_path):
    from src.emulator.datapack import ANY_MINOR, format_label

    meta = _mcmeta(tmp_path, {"min_format": 88, "max_format": [94]})
    assert meta.format_range == ((88, 0), (94, ANY_MINOR))
    assert format_label(meta.format_range[1]) == "94.*"
    assert meta.format_tuple == (94, 1)  # newest real release of format 94
    legacy = _mcmeta(tmp_path, {"supported_formats": {"min_inclusive": 48, "max_inclusive": 57}})
    assert legacy.format_range == ((48, 0), (57, ANY_MINOR))


def test_description_flattens_text_components(tmp_path):
    meta = _mcmeta(tmp_path, {"pack_format": 61, "description": [{"text": "A"}, {"text": "B"}]})
    assert meta.description == "AB"


def test_loads_namespaces_and_tree(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "say hi\n",
                "data/test/function/sub/helper.mcfunction": "say helper\n",
                "data/test/recipe/thing.json": {"type": "minecraft:crafting_shaped"},
            }
        )
    )
    assert not pack.errors
    assert set(pack.functions) == {"test:tick", "test:sub/helper"}
    assert "recipe" in pack.namespaces["test"].registries
    assert pack.namespaces["test"].tree.find("function/sub/helper.mcfunction") is not None


def test_plural_and_singular_folders_both_load_singular_wins(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/functions/tick.mcfunction": "say plural\n",
                "data/test/function/tick.mcfunction": "say singular\n",
            }
        )
    )
    function = pack.function("test:tick")
    assert function is not None
    assert "function" in function.path.parts


def test_missing_files_are_reported(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    pack = Datapack.load(empty)
    assert "missing pack.mcmeta" in pack.errors
    assert "missing data/ folder" in pack.errors


def test_overlay_applies_by_version_and_is_ignored_before_1_20_2(make_pack):
    root = make_pack(
        {
            # before 1.21 the game reads functions/, from 1.21 on function/
            "data/test/function/tick.mcfunction": "say modern\n",
            "data/test/functions/tick.mcfunction": "say modern\n",
            "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "legacy/data/test/functions/tick.mcfunction": "say legacy\n",
        },
        mcmeta={
            "pack": {"pack_format": 61, "supported_formats": [10, 71]},
            "overlays": {
                "entries": [
                    {"formats": {"min_inclusive": 18, "max_inclusive": 40}, "directory": "legacy"}
                ]
            },
        },
    )
    pack = Datapack.load(root)

    def body(version: str) -> str:
        return pack.view_for(versions.parse(version)).function("test:tick").lines[0]

    assert body("1.21.4") == "say modern"
    assert body("1.20.4") == "say legacy"  # format 26, inside the overlay range
    assert body("1.19") == "say modern"  # overlays did not exist yet
    assert pack.view_for(versions.parse("1.20.4")).active_overlays == ["legacy"]


def test_declared_versions_and_support(make_pack):
    pack = Datapack.load(
        make_pack({}, mcmeta={"pack": {"pack_format": 48, "supported_formats": [48, 57]}})
    )
    ids = [v.id for v in pack.declared_versions()]
    assert ids[0] == "1.21" and ids[-1] == "1.21.3"
    assert pack.supports(versions.parse("1.21.2"))
    assert not pack.supports(versions.parse("1.21.4"))


def test_png_size_is_read_from_ihdr(make_pack):
    root = make_pack({})
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 64, 32)
    (root / "pack.png").write_bytes(
        header + b"\x08\x06\x00\x00\x00" + struct.pack(">I", zlib.crc32(b""))
    )
    pack = Datapack.load(root)
    assert pack.icon is not None and pack.icon.size == (64, 32)
