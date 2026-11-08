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


def _busy_graph() -> CallGraph:
    """Fan-out, fan-in, a skipped layer, a cycle, a self-call, lone nodes."""
    graph = CallGraph()
    for tag in ("#minecraft:load", "#minecraft:tick"):
        graph.add_node(tag, kind="tag")
    edges = [
        ("#minecraft:load", "p:load", "tag"),
        ("#minecraft:tick", "p:tick", "tag"),
        ("p:load", "p:util/log", "call"),  # skips two layers
        ("p:tick", "p:a", "call"),
        ("p:tick", "p:b", "call"),
        ("p:tick", "p:c", "condition"),
        ("p:a", "p:x", "call"),
        ("p:b", "p:x", "call"),
        ("p:c", "p:y", "macro"),
        ("p:a", "p:z", "call"),
        ("p:x", "p:util/log", "call"),
        ("p:y", "p:util/log", "call"),
        ("p:z", "p:util/log", "call"),
        ("p:timer", "p:timer", "schedule"),  # a self-call only: a lone node
        ("p:r1", "p:r2", "call"),  # a cycle of its own
        ("p:r2", "p:r1", "call"),
    ]
    for source, target, kind in edges:
        for node in (source, target):
            graph.add_node(node, kind="function", missing=node == "p:z")
        graph.add_edge(source, target, kind)
    for index in range(5):
        graph.add_node(f"p:dead/{index}", kind="function")
    return graph


def test_layered_layout_never_overlaps_and_routes_around_nodes():
    graph = _busy_graph()
    widths = {node: 0.5 + 0.1 * len(node) for node in graph.nodes}
    layout = graph.layered_layout(widths, gap=0.4, y_spacing=2.0)
    assert set(layout.positions) == set(graph.nodes)
    assert set(layout.routes) == set(graph.edges)

    # boxes on the same row keep their widths plus the gap apart
    items = list(layout.positions.items())
    for index, (a, (ax, ay)) in enumerate(items):
        for b, (bx, by) in items[index + 1 :]:
            if abs(ay - by) < 1e-9:
                assert abs(ax - bx) >= (widths[a] + widths[b]) / 2 + 0.4 - 1e-9, (a, b)
            else:
                assert abs(ay - by) >= 2.0 - 1e-9

    # an edge that skips layers bends once per layer, between the boxes there
    route = layout.routes[next(e for e in graph.edges if e.target == "p:util/log")]
    load_y, log_y = layout.positions["p:load"][1], layout.positions["p:util/log"][1]
    assert len(route) == 2 + round((load_y - log_y) / 2.0) - 1
    for bend_x, bend_y in route[1:-1]:
        for node, (x, y) in layout.positions.items():
            if abs(y - bend_y) < 1e-9:
                assert abs(x - bend_x) >= widths[node] / 2, node
    timer = next(e for e in graph.edges if e.source == "p:timer")
    assert layout.routes[timer] == [layout.positions["p:timer"]]

    # the load/tick part comes first (left, top); nodes with no edges sit apart, below
    top_left = min(layout.positions.values(), key=lambda p: (-p[1], p[0]))
    assert top_left == layout.positions["#minecraft:load"]
    lowest_linked = min(layout.positions["p:util/log"][1], layout.positions["p:r2"][1])
    for index in range(5):
        assert layout.positions[f"p:dead/{index}"][1] < lowest_linked
    assert layout.positions["p:timer"][1] < lowest_linked


def test_layered_layout_is_deterministic():
    first = _busy_graph().layered_layout(gap=0.3)
    second = _busy_graph().layered_layout(gap=0.3)
    assert first == second
    assert first.positions == _busy_graph().layered_layout(gap=0.3).positions
    # the plain ``layout`` keeps its shape: node -> (x, y), x_spacing apart
    positions = _busy_graph().layout(x_spacing=3.0, y_spacing=1.6)
    assert set(positions) == set(_busy_graph().nodes)
    rows: dict[float, list[float]] = {}
    for x, y in positions.values():
        rows.setdefault(y, []).append(x)
    for xs in rows.values():
        xs.sort()
        assert all(b - a >= 3.0 - 1e-9 for a, b in zip(xs, xs[1:], strict=False))


def test_layered_layout_orders_layers_to_cut_crossings():
    from datapack_emulator.emulator.analysis.graph import count_crossings

    # a twisted bipartite graph: in name order every pair of edges crosses
    graph = CallGraph()
    tops = [f"t:{index}" for index in range(5)]
    bottoms = [f"b:{index}" for index in range(5)]
    for node in tops + bottoms:
        graph.add_node(node, kind="function")
    for index, top in enumerate(tops):
        graph.add_edge(top, bottoms[4 - index])
        graph.add_edge(top, bottoms[max(3 - index, 0)])
    below = {top: graph.successors(top) for top in tops}
    assert count_crossings([sorted(tops), sorted(bottoms)], below) > 10
    layout = graph.layered_layout()
    assert layout.crossings == 0
    order = sorted(bottoms, key=lambda node: layout.positions[node][0])
    top_order = sorted(tops, key=lambda node: layout.positions[node][0])
    assert count_crossings([top_order, order], below) == 0

    # and on the busy fixture it is no worse than the first, depth-first order
    assert _busy_graph().layered_layout(passes=8).crossings <= (
        _busy_graph().layered_layout(passes=0).crossings
    )


def test_labels_can_drop_the_main_namespace():
    from datapack_emulator.emulator.analysis.graph import short_name

    graph = _busy_graph()
    assert graph.main_namespace() == "p"
    assert short_name("p:util/log", "p") == "util/log"
    assert short_name("#p:hooks", "p") == "#hooks"
    assert short_name("#minecraft:tick", "p") == "#minecraft:tick"
    assert short_name("q:p:odd", "p") == "q:p:odd"
    assert short_name("p:x", None) == "p:x"
    mixed = CallGraph()
    for node in ("a:1", "b:1", "c:1"):
        mixed.add_node(node, kind="function")
    assert mixed.main_namespace() is None


def test_macro_call_targets_are_dynamic_not_missing(make_pack):
    pack = Datapack.load(
        make_pack(
            {
                "data/test/function/tick.mcfunction": (
                    'function test:pick {dust:"red"}\nfunction test:any {f:"test:chosen"}\n'
                ),
                "data/test/function/pick.mcfunction": "$function test:dust/$(dust)\n",
                "data/test/function/any.mcfunction": "$function $(f)\n",
                "data/test/function/dust/red.mcfunction": "say red\n",
                "data/test/function/dust/blue.mcfunction": "say blue\n",
                "data/test/function/chosen.mcfunction": "say chosen\n",
                "data/test/function/other/x.mcfunction": "say never\n",
            }
        )
    )
    graph = CallGraph.from_datapack(pack)
    assert graph.missing() == []
    assert graph.dynamic() == ["minecraft:$(f)", "test:dust/$(dust)"]
    # the template reaches every function its pattern fits
    assert set(graph.successors("test:dust/$(dust)")) == {"test:dust/red", "test:dust/blue"}
    # an id that is all macro reaches the ids the pack writes as strings
    assert graph.successors("minecraft:$(f)") == ["test:chosen"]
    assert graph.unreachable() == ["test:other/x"]


def test_templates_sharing_a_pattern_share_edges_and_make_no_cycles(make_pack):
    files = {
        "data/test/function/tick.mcfunction": 'function test:a {f:"test:a",x:"b"}\n',
        "data/test/function/a.mcfunction": "$function $(f)\n$function test:$(x)\n",
        "data/test/function/b.mcfunction": "$function $(g)\n$function test:$(y)\n",
    }
    graph = CallGraph.from_datapack(Datapack.load(make_pack(files)))
    # the second template of each pattern goes through the first
    assert graph.successors("minecraft:$(g)") == ["minecraft:$(f)"]
    assert graph.successors("test:$(y)") == ["test:$(x)"]
    assert set(graph.successors("test:$(x)")) == {"test:a", "test:b", "test:tick"}
    assert graph.unreachable() == []
    # what a macro call may become is not recursion
    assert graph.cycles() == []


def test_template_pattern():
    from datapack_emulator.emulator.analysis.graph import fully_dynamic, template_pattern

    pattern = template_pattern("ra:a/$(x)/b")
    assert pattern.fullmatch("ra:a/one/b") and pattern.fullmatch("ra:a/one/two/b")
    assert not pattern.fullmatch("ra:a/b") and not pattern.fullmatch("rb:a/one/b")
    assert template_pattern("minecraft:$(f)").fullmatch("other:thing")
    assert fully_dynamic("minecraft:$(f)") and fully_dynamic("$(ns):$(path)") is False
    assert not fully_dynamic("ra:$(x)")
