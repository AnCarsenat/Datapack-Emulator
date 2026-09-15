"""Running the emulator — finite, endless or one tick at a time — plus the
profiler report, the call graph, the dot export and the engine window."""

from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import QTimer, QUrl

from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.emulator.testing import TestSchedule
from datapack_emulator.settings import PATHS
from datapack_emulator.window.controllers.base import TAB_GRAPH, TAB_PROFILER, Controller
from datapack_emulator.window.engine_window import EngineWindow
from datapack_emulator.window.panels.profile import fill_profile_tree
from datapack_emulator.window.panels.profile import summary as profile_summary

#: speed combo entries in window.ui, in order
SPEEDS = ("fast", "realtime")


class RunController(Controller):
    #: time one timer slot may spend ticking before the UI gets a turn
    BATCH_BUDGET_S = 0.03
    #: one Minecraft tick in real time
    REALTIME_INTERVAL_MS = 50

    def __init__(self, window):
        super().__init__(window)
        self.timer = QTimer(window)
        self.timer.timeout.connect(self._on_timer)
        #: ticks left in the current run; None while running until stopped
        self.remaining: int | None = 0
        self.running = False
        self._rebuild_graph = False
        #: tests waiting for their tick in the current run ("run tests during runs")
        self.schedule: TestSchedule | None = None

    def connect(self) -> None:
        window = self.window
        window.run_button.clicked.connect(self.run_emulator)
        window.run_all_button.clicked.connect(self.run_all)
        window.step_button.clicked.connect(self.step)
        # clicked(bool) would pass checked=False as `refresh`
        window.stop_button.clicked.connect(lambda: self.stop())
        window.engine_button.clicked.connect(self.open_engine)
        window.combo_speed.currentIndexChanged.connect(self.on_speed_changed)

    # -- settings -----------------------------------------------------------

    @property
    def speed(self) -> str:
        return SPEEDS[max(0, self.window.combo_speed.currentIndex())]

    @speed.setter
    def speed(self, value: str) -> None:
        self.window.combo_speed.setCurrentIndex(SPEEDS.index(value) if value in SPEEDS else 0)

    @property
    def interval_ms(self) -> int:
        return self.REALTIME_INTERVAL_MS if self.speed == "realtime" else 0

    def on_speed_changed(self, _index: int) -> None:
        """Switching speed during a run takes effect on the next timer slot."""
        if self.timer.isActive():
            self.timer.setInterval(self.interval_ms)

    # -- starting and stopping ----------------------------------------------

    def run_emulator(self) -> None:
        self.start(graph=False)

    def run_all(self) -> None:
        """F5: a fresh world for the configured ticks, then profiler and graph."""
        self.start(graph=True)

    def start(self, graph: bool) -> None:
        window = self.window
        if not self.need_datapack():
            return
        self.stop(refresh=False)
        window.log_view.clear()
        window.datapacks.rebuild_emulator()
        assert window.emulator is not None
        window.emulator.start()
        ticks = window.spin_ticks.value()
        self.remaining = None if ticks < 0 else ticks
        self._rebuild_graph = graph
        self.schedule = self._test_schedule()
        self._set_running(True)
        if self.remaining == 0:
            window.emulator.run(ticks=0)  # just start the server and run load
            self.finish()
            return
        self.timer.start(self.interval_ms)
        self.status(
            f"running {'until stopped' if self.remaining is None else f'{ticks} tick(s)'} "
            f"on {window.version.id}…"
        )

    def stop(self, refresh: bool = True) -> None:
        """Stop a running emulation (and, by default, show its results)."""
        if not self.running:
            return
        if refresh:
            self.finish(stopped=True)
            return
        self.timer.stop()
        self._set_running(False)
        self.schedule = None
        self.window.log_view.flush()
        self.show_tick()
        if self.window.emulator is not None:
            self.status(f"stopped at tick {self.window.emulator.world.tick}")

    def wait(self) -> None:
        """Drive a finite run to its end without the event loop (tests, scripts)."""
        while self.running and self.remaining is not None:
            self._on_timer()

    def step(self) -> None:
        """One more tick in the current world; starts one if there is none."""
        window = self.window
        if not self.need_datapack():
            return
        self.stop(refresh=False)
        if window.emulator is None:
            window.datapacks.rebuild_emulator()
        assert window.emulator is not None
        window.emulator.start()
        tick = window.emulator.world.tick
        window.emulator.run_tick()
        self._run_tests_at(tick)
        window.log_view.flush()
        self.show_tick()
        window.world_view.refresh()
        self.run_profiler(switch_tab=False)
        self.status(f"stepped to tick {window.emulator.world.tick} on {window.version.id}")

    # -- ticking ------------------------------------------------------------

    def _on_timer(self) -> None:
        emulator = self.window.emulator
        if emulator is None:
            self.stop(refresh=False)
            return
        deadline = time.monotonic() + self.BATCH_BUDGET_S
        realtime = self.speed == "realtime" and self.timer.isActive()
        while self.remaining is None or self.remaining > 0:
            tick = emulator.world.tick
            emulator.run_tick()
            if self.schedule is not None and self.schedule.after_tick(emulator, tick):
                self.window.environment.show_results(
                    self.schedule.by_index(include_unreached=False), pending="waiting for its tick"
                )
            if self.remaining is not None:
                self.remaining -= 1
            if realtime or time.monotonic() >= deadline:
                break
        self.show_tick()
        self.window.world_view.refresh_if_due()
        if self.remaining == 0:
            self.finish()

    def finish(self, stopped: bool = False) -> None:
        window = self.window
        self.timer.stop()
        self._set_running(False)
        window.log_view.flush()
        if self.schedule is not None:
            window.environment.show_results(self.schedule.by_index())
            self.schedule = None
        if window.emulator is None:
            return
        profiler = window.emulator.profiler
        self.run_profiler()
        window.world_view.refresh()
        if self._rebuild_graph:
            self.run_graphview()
            window.tabs.setCurrentWidget(window.tab_page(TAB_PROFILER))
        self.show_tick()
        self.status(
            f"{'stopped' if stopped else 'finished'} on {window.version.id}: "
            f"{profiler.ticks} tick(s), {profiler.total_us / 1000:.2f} ms estimated, "
            f"worst tick {profiler.worst_tick_us / 1000:.2f} ms"
            + (
                f", {len(window.call_graph.nodes)} graph node(s)"
                if self._rebuild_graph and window.call_graph
                else ""
            )
        )

    # -- tests during runs ----------------------------------------------------

    def _test_schedule(self) -> TestSchedule | None:
        window = self.window
        if not window.check_tests_during_runs.isChecked():
            return None
        tests = window.environment.tests()
        if not any(test.enabled for test in tests):
            return None
        window.environment.show_results({}, pending="waiting for its tick")
        return TestSchedule(tests)

    def _run_tests_at(self, tick: int) -> None:
        """Step: the enabled tests of exactly this tick run after it."""
        window = self.window
        if not window.check_tests_during_runs.isChecked() or window.emulator is None:
            return
        tests = [
            test if test.at_tick == tick else replace(test, enabled=False)
            for test in window.environment.tests()
        ]
        schedule = TestSchedule(tests)
        if schedule.after_tick(window.emulator, tick):
            window.environment.show_results(
                schedule.by_index(include_unreached=False), pending=f"not at tick {tick}"
            )

    def _set_running(self, running: bool) -> None:
        window = self.window
        self.running = running
        window.stop_button.setEnabled(running)
        for button in (window.run_button, window.run_all_button, window.step_button):
            button.setEnabled(not running)
        for name in ("actionrun_all", "actionrun_emulator", "actionstep_tick"):
            action = window._action(name)
            if action is not None:
                action.setEnabled(not running)
        action = window._action("actionstop")
        if action is not None:
            action.setEnabled(running)

    def show_tick(self) -> None:
        window = self.window
        emulator = window.emulator
        if emulator is None:
            window.tick_label.setText("idle")
            return
        suffix = " (running)" if self.running else ""
        window.tick_label.setText(f"tick {emulator.world.tick}{suffix}")

    # -- results ------------------------------------------------------------

    def run_profiler(self, switch_tab: bool = True) -> None:
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
        fill_profile_tree(window.tree_profile, window.emulator.profiler)
        window.profile_summary.setText(profile_summary(window.emulator.profiler))
        if switch_tab:
            window.tabs.setCurrentWidget(window.tab_page(TAB_PROFILER))

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
        window.tabs.setCurrentWidget(window.tab_page(TAB_GRAPH))

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
                window.datapack,
                parent=window,
                library=window.library,
                tests_provider=window.environment.tests,
            )
            if window.project.engine_versions:
                window.engine_window.select_ids(window.project.engine_versions)
        elif window.engine_window.datapack is not window.datapack:
            # a different pack: start from what it declares; the same pack
            # keeps the versions ticked last time
            window.engine_window.set_datapack(window.datapack)
        window.engine_window.show()
        window.engine_window.raise_()
        window.engine_window.activateWindow()
