from datapack_emulator.emulator import CallGraph, Datapack, TestEngine, versions


def test_matrix_flags_commands_a_version_lacks(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "damage @s 1\n",
                "data/test/functions/tick.mcfunction": "damage @s 1\n",
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
            },
            mcmeta={"pack": {"pack_format": 61, "supported_formats": [6, 61]}},
        )
    )
    engine = TestEngine(pack, ticks=1)
    old, new = engine.run([versions.parse("1.19"), versions.parse("1.21.4")])
    # 1.19 cannot parse `damage`, so the function and the tick tag fail to load
    assert "damage" in old.unknown_commands and old.status == "warnings"
    assert old.failed_functions == ["test:tick"] and old.failed_tags == ["#minecraft:tick"]
    assert not new.unknown_commands and not new.failed_functions


def test_matrix_flags_folder_spelling(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/functions/tick.mcfunction": "say hi\n",
                "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
            },
            mcmeta={"pack": {"pack_format": 61, "supported_formats": [15, 61]}},
        )
    )
    run = TestEngine(pack, ticks=1).run_version("1.21.4")
    assert any("reads 'function/'" in record.message for record in run.records)


def test_unsupported_status(make_pack):
    pack = Datapack.load(
        make_pack(
            {  # the folders 1.16.5 reads, so only the metadata is off
                "data/test/functions/tick.mcfunction": "say hi\n",
                "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
            }
        )
    )
    assert TestEngine(pack, ticks=1).run_version("1.16.5").status == "unsupported"


def test_format_boundaries_keeps_first_of_each_format():
    chosen = TestEngine.format_boundaries(versions.version_range("1.21", "1.21.4"))
    assert [v.id for v in chosen] == ["1.21", "1.21.2", "1.21.4"]


def test_call_graph_findings(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": "function test:a\n",
                "data/test/function/a.mcfunction": "function test:b\nfunction test:missing\n",
                "data/test/function/b.mcfunction": "function test:a\n",
                "data/test/function/dead.mcfunction": "say never\n",
            }
        )
    )
    graph = CallGraph.from_datapack(pack)
    assert graph.missing() == ["test:missing"]
    assert graph.unreachable() == ["test:dead"]
    assert any(set(cycle) == {"test:a", "test:b"} for cycle in graph.cycles())
    assert not graph.is_dag
    order = graph.topological_order()
    assert order.index("#minecraft:tick") < order.index("test:tick")
    assert set(graph.layout()) == set(graph.nodes)
    assert '"test:a" -> "test:b"' in graph.to_dot()


def test_multi_version_packs_are_not_warned_for_their_legacy_forms(make_pack):
    from datapack_emulator.emulator.runtime.output import LogLevel

    files = {
        "data/test/functions/tick.mcfunction": "say hi\n",
        "data/minecraft/tags/functions/tick.json": {"values": ["test:tick"]},
        "data/test/function/tick.mcfunction": "say hi\n",
        "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
        "data/test/loot_tables/thing.json": {},
    }
    spanning = Datapack.load(
        make_pack(files, mcmeta={"pack": {"pack_format": 15, "supported_formats": [15, 61]}})
    )
    only_modern = Datapack.load(make_pack(files, mcmeta={"pack": {"pack_format": 61}}))

    def folder_notes(pack):
        run = TestEngine(pack, ticks=1).run_version("1.21.4")
        return [r.level for r in run.records if "loot_tables/" in r.message]

    assert folder_notes(spanning) == [LogLevel.INFO]
    assert folder_notes(only_modern) == [LogLevel.WARNING]
