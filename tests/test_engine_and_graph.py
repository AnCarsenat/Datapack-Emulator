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


def test_engine_runs_can_be_cancelled(make_pack):
    from datapack_emulator.emulator import Datapack
    from datapack_emulator.emulator.engine import TestEngine

    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "say hi\n"}))
    engine = TestEngine(pack, ticks=10)
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 4

    runs = engine.run(["1.20.4", "1.21.4"], cancelled=cancelled)
    assert len(runs) == 1
    assert runs[0].cancelled and runs[0].status == "cancelled" and runs[0].ticks == 3
    assert any("cancelled after 3 tick(s)" in record.message for record in runs[0].records)
    assert engine.run(["1.20.4"], cancelled=lambda: True) == []


def test_cancelled_runs_skip_their_tests_and_report(make_pack):
    from datapack_emulator.cli.junit import junit_tree
    from datapack_emulator.emulator import Datapack
    from datapack_emulator.emulator.engine import TestEngine
    from datapack_emulator.emulator.testing import CommandTest

    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "say hi\n"}))
    tests = [CommandTest("say a", at_tick=0), CommandTest("say b", at_tick=5)]
    engine = TestEngine(pack, ticks=10, tests=tests)
    calls = []
    runs = engine.run(["1.21.4"], cancelled=lambda: len(calls.append(1) or calls) > 3)
    run = runs[0]
    assert run.cancelled and run.ticks_summary == "2/10"
    assert run.tests_passed == 1 and run.tests_failed == 0 and run.tests_skipped == 1
    assert run.tests_summary == "1/1 (1 skipped)" and run.status == "cancelled"
    assert run.tests[1].reason == "skipped: the run was cancelled after 2 tick(s)"
    html = TestEngine.to_html(runs, "p", not_run=2)
    assert "<td>2/10</td>" in html and "2 more version(s) were not run" in html
    xml = junit_tree(runs, "p", ["1.21.5"]).getroot()
    assert xml.get("skipped") == "1" and xml.get("failures") == "0"
    assert xml.find("testsuite/testcase/skipped") is not None
    assert [suite.get("name") for suite in xml.findall("testsuite")] == ["p @ 1.21.4", "p @ 1.21.5"]

    # a cancel between versions marks the last run
    flags = iter([False, True])
    runs = TestEngine(pack, ticks=0).run(["1.20.4", "1.21.4"], cancelled=lambda: next(flags, True))
    assert len(runs) == 1 and runs[0].cancelled

    # failures still show through a cancel
    failing = TestEngine(pack, ticks=3, tests=[CommandTest("say a", expect="nope")])
    run = failing.run(["1.21.4"], cancelled=lambda: len(calls.append(1) or calls) > 6)[0]
    assert run.cancelled and run.status == "tests failed"
