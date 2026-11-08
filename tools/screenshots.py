"""Take the screenshots shown in the readme and the docs.

Opens the window on ``samples/hat_v2``, runs it, and saves one picture per
tab and window into ``docs/screenshots/``:

    python tools/screenshots.py [--out docs/screenshots] [--platform xcb]

Needs the gui extra (PySide6, pyqtgraph). The window state (layout, recent
lists) goes to a temporary folder, so your own is left alone; downloaded
client jars in ``.cache/vanilla`` are still used when present. The pack is
copied there too, so no path of your own shows in the pictures.
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
    parser.add_argument("--version", default="1.21.4", help="Minecraft version to emulate")
    arguments = parser.parse_args()
    arguments.out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    qt_args = [sys.argv[0]]
    if arguments.platform:
        qt_args += ["-platform", arguments.platform]
    app = QApplication(qt_args)

    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.settings import PATHS

    scratch = Path(tempfile.mkdtemp(prefix="dpemu-shots-"))
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
        widget.grab().save(str(target))
        print(f"wrote {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")

    pack = shutil.copytree(PACK, scratch / PACK.name)
    window = MainWindow(start_path=pack, open_last=False)
    window.resize(1440, 900)
    window.show()
    settle(1.0)

    index = window.combo_version.findData(arguments.version)
    if index >= 0:
        window.combo_version.setCurrentIndex(index)
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
    deadline = time.monotonic() + 120
    while engine.running and time.monotonic() < deadline:
        settle(0.2)
    shoot(engine, "engine")

    window.projects.modified = False
    engine.close()
    window.close()
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
