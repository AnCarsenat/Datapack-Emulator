"""Running the emulator, the profiler report, the call graph, the engine."""

from __future__ import annotations

from PySide6.QtCore import QUrl

from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.settings import PATHS
from datapack_emulator.window.controllers.base import TAB_GRAPH, TAB_PROFILER, Controller
from datapack_emulator.window.engine_window import EngineWindow


class RunController(Controller):
    def connect(self) -> None:
        window = self.window
        window.run_button.clicked.connect(self.run_emulator)
        window.run_all_button.clicked.connect(self.run_all)
        window.engine_button.clicked.connect(self.open_engine)

    def run_emulator(self) -> None:
        window = self.window
        if not self.need_datapack():
            return
        window.datapacks.rebuild_emulator()
        assert window.emulator is not None
        ticks = window.spin_ticks.value()
        window.log_view.clear()
        self.status(f"running {ticks} tick(s) on {window.version.id}…")
        profiler = window.emulator.run(ticks=ticks)
        self.status(
            f"{window.version.id}: {ticks} tick(s), {profiler.total_us / 1000:.2f} ms estimated, "
            f"worst tick {profiler.worst_tick_us / 1000:.2f} ms"
        )
        self.run_profiler()

    def run_all(self) -> None:
        """F5: emulate, refresh the profiler, rebuild the graph."""
        window = self.window
        if not self.need_datapack():
            return
        self.run_emulator()
        self.run_graphview()
        window.tabs.setCurrentIndex(TAB_PROFILER)
        assert window.emulator is not None
        profiler = window.emulator.profiler
        self.status(
            f"run all on {window.version.id}: {len(profiler.tick_times)} tick(s), "
            f"{profiler.total_us / 1000:.2f} ms estimated, "
            f"worst tick {profiler.worst_tick_us / 1000:.2f} ms, "
            f"{len(window.call_graph.nodes) if window.call_graph else 0} graph node(s)"
        )

    def run_profiler(self) -> None:
        """Regenerate the HTML report and show it in the profiler tab."""
        window = self.window
        if window.emulator is None:
            self.status("load a datapack first")
            return
        name = window.datapack.name if window.datapack else "datapack"
        report = window.emulator.profiler.write_html(
            PATHS.GENERATED / "index.html",
            f"Function profiler — {name}",
            f"{window.version.id} (pack_format {window.version.format_string})",
        )
        window.output.app(f"wrote {report}")
        window.web_view.setUrl(QUrl.fromLocalFile(str(report)))
        window.web_view.reload()
        window.tabs.setCurrentIndex(TAB_PROFILER)

    def run_graphview(self) -> None:
        window = self.window
        if not self.need_datapack():
            return
        view = window.datapack.view_for(window.version)
        graph = window.call_graph = CallGraph.from_pack(view)
        window.graph_widget.set_graph(graph)
        cycles = graph.cycles()
        missing = graph.missing()
        unreachable = graph.unreachable()
        window.graph_label.setText(
            f"{window.version.id}: {len(graph.nodes)} node(s), {len(graph.edges)} edge(s) — "
            + ("DAG" if not cycles else f"{len(cycles)} cycle(s)")
            + (f", {len(missing)} missing" if missing else "")
            + (f", {len(unreachable)} unreachable" if unreachable else "")
        )
        for cycle in cycles:
            window.output.emulator("recursion: " + " -> ".join(cycle), level=LogLevel.WARNING)
        for name in missing:
            window.output.emulator(f"calls missing function {name}", level=LogLevel.ERROR)
        for name in unreachable:
            window.output.emulator(
                f"{name} is never called from #minecraft:load/tick", level=LogLevel.WARNING
            )
        window.tabs.setCurrentIndex(TAB_GRAPH)

    def export_dot(self) -> None:
        window = self.window
        if not self.need_datapack():
            return
        graph = window.call_graph or CallGraph.from_pack(window.datapack.view_for(window.version))
        target = PATHS.GENERATED / f"{window.datapack.name}-{window.version.id}.dot"
        graph.write_dot(target)
        self.status(f"wrote {target}")
        window.output.app(f"call graph exported to {target}")

    def open_engine(self) -> None:
        window = self.window
        if not self.need_datapack():
            return
        if window.engine_window is None:
            window.engine_window = EngineWindow(
                window.datapack, parent=window, library=window.library
            )
        else:
            window.engine_window.set_datapack(window.datapack)
        window.engine_window.show()
        window.engine_window.raise_()
        window.engine_window.activateWindow()
