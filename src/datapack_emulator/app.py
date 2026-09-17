"""The window's entry point: ``datapack-emulator`` or ``python -m datapack_emulator``.

With a path it opens that project or datapack; without one it opens the
project used most recently (``--no-last-project`` for the sample datapack,
which is also the window's *file › open the last project on launch*).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Qt's own options that take a value (``-platform offscreen``)
QT_WITH_VALUE = (
    "-platform",
    "-platformpluginpath",
    "-platformtheme",
    "-plugin",
    "-style",
    "-stylesheet",
    "-session",
    "-display",
    "-geometry",
    "-qwindowgeometry",
    "-qwindowicon",
    "-qwindowtitle",
    "-graphicssystem",
)


def split_qt_arguments(argv: list[str]) -> tuple[list[str], list[str]]:
    """Qt's own single-dash options (and their values) go to QApplication; the
    rest is this program's."""
    mine: list[str] = []
    for_qt: list[str] = []
    rest = list(argv)
    while rest:
        argument = rest.pop(0)
        if argument.startswith("-") and not argument.startswith("--") and len(argument) > 1:
            for_qt.append(argument)
            if argument.split("=")[0] in QT_WITH_VALUE and "=" not in argument and rest:
                for_qt.append(rest.pop(0))
        else:
            mine.append(argument)
    return mine, for_qt


def parse_arguments(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="datapack-emulator",
        description="The emulator's window. datapack-emulator-cli is the command line.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="a project (.dpemu) or a datapack folder to open",
    )
    last = parser.add_mutually_exclusive_group()
    last.add_argument(
        "--last-project",
        dest="open_last",
        action="store_true",
        default=None,
        help="open the project used most recently (the default; see docs/projects.md)",
    )
    last.add_argument(
        "--no-last-project",
        dest="open_last",
        action="store_false",
        help="start on the sample datapack instead",
    )
    mine, for_qt = split_qt_arguments(sys.argv[1:] if argv is None else argv)
    return parser.parse_args(mine), for_qt


def main(argv: list[str] | None = None) -> int:
    # parsed before Qt is imported, so --help and a typo answer at once
    arguments, for_qt = parse_arguments(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from datapack_emulator.window import MainWindow

    app = QApplication([sys.argv[0], *for_qt])
    window = MainWindow(start_path=arguments.path, open_last=arguments.open_last, defer_start=True)
    window.show()
    # the window is on screen before a big project is opened
    QTimer.singleShot(0, window.start_session)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
