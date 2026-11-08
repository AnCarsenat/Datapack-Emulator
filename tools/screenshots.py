"""Take the screenshots shown in the readme and the docs.

Opens the window on ``samples/hat_v2``, runs it, and saves one picture per
tab and window into ``docs/screenshots/``:

    python tools/screenshots.py [--out docs/screenshots] [--platform xcb]
                                [--minecraft 1.21.4]

Needs the gui extra (PySide6, pyqtgraph). The window state (layout, recent
lists) goes to a fixed temporary folder, ``<tmp>/datapack-emulator-shots``,
emptied before and after; downloaded client jars in ``.cache/vanilla`` are
still used when present. The pack is copied there too, so the pictures show
that neutral path (the same on every run) instead of your checkout's.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PACK = ROOT / "samples" / "hat_v2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    parser.add_argument("--platform", default="", help="Qt platform (xcb, offscreen, …)")
    parser.add_argument("--minecraft", default="1.21.4", help="Minecraft version to emulate")
    arguments = parser.parse_args()

    from datapack_emulator.emulator import versions

    try:
        minecraft = versions.parse(arguments.minecraft).id
    except KeyError as exc:
        parser.error(str(exc.args[0]))
    arguments.out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    qt_args = [sys.argv[0]]
    if arguments.platform:
        qt_args += ["-platform", arguments.platform]
    app = QApplication(qt_args)

    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.settings import PATHS

    scratch = Path(tempfile.gettempdir()) / "datapack-emulator-shots"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    vanilla = PATHS.VANILLA_CACHE
    PATHS.CACHE = scratch / ".cache"
    PATHS.PROJECTS = scratch / "projects"
    PATHS.GENERATED = scratch / "generated"
    PATHS.VANILLA_CACHE = vanilla

    from datapack_emulator.window import MainWindow
    from datapack_emulator.window.controllers.base import (
        TAB_ENVIRONMENT,
        TAB_GRAPH,
        TAB_PROFILER,
        TAB_SOURCE,
    )

    def settle(seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def shoot(widget, name: str) -> None:
        settle()
        target = arguments.out / f"{name}.png"
        if not widget.grab().save(str(target)):
            raise SystemExit(f"could not write {target}")
        print(f"wrote {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")

    window = engine = None
    try:
        pack = shutil.copytree(PACK, scratch / PACK.name)
        window = MainWindow(start_path=pack, open_last=False)
        window.resize(1440, 900)
        window.show()
        settle(1.0)

        window.combo_version.setCurrentIndex(window.combo_version.findData(minecraft))
        window.spin_ticks.setValue(40)
        window.spin_players.setValue(2)
        window.environment.set_tests(
            [
                CommandTest("execute as Player1 run trigger hat", at_tick=5),
                CommandTest("scoreboard players get Player1 hat", at_tick=6, expect_value="0"),
                CommandTest("execute if entity @e[tag=hat_saver]", at_tick=10, expect_value="1"),
            ]
        )
        window.check_tests_during_runs.setChecked(True)
        window.runs.run_all()
        window.runs.wait()
        settle(1.0)

        def show_tab(name: str) -> None:
            window.tabs.setCurrentWidget(window.tab_page(name))

        show_tab(TAB_ENVIRONMENT)
        shoot(window, "window")
        show_tab(TAB_PROFILER)
        shoot(window, "profiler")
        show_tab(TAB_GRAPH)
        settle(2.0)  # the graph is drawn by a web view
        shoot(window, "call-graph")
        window.navigation.open_function("hat:tick", ask=False)
        show_tab(TAB_SOURCE)
        shoot(window, "source")

        window.runs.open_engine()
        engine = window.engine_window
        engine.resize(1200, 760)
        engine.select_declared()
        engine.run_matrix()
        deadline = time.monotonic() + 300
        while engine.running and time.monotonic() < deadline:
            settle(0.2)
        if engine.running:
            raise SystemExit("the version engine did not finish in 5 minutes")
        shoot(engine, "engine")
    finally:
        if engine is not None:
            engine.close()  # cancels a matrix still running
        if window is not None:
            window.projects.modified = False
            window.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
