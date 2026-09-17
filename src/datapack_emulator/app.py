"""The window's entry point: ``datapack-emulator`` or ``python -m datapack_emulator``.

With a path it opens that project or datapack; without one it opens the
project used most recently (``--no-last-project`` for the sample datapack,
which is also the window's *file › open the last project on launch*).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="datapack-emulator",
        description="The emulator's window. datapack-emulator-cli is the command line.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="a project (.dpemu) or a datapack folder or zip to open",
    )
    last = parser.add_mutually_exclusive_group()
    last.add_argument(
        "--last-project",
        dest="open_last",
        action="store_true",
        default=None,
        help="open the project used most recently (the default)",
    )
    last.add_argument(
        "--no-last-project",
        dest="open_last",
        action="store_false",
        help="start on the sample datapack instead",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from datapack_emulator.window import MainWindow

    arguments = parse_arguments(argv)
    app = QApplication(sys.argv[:1])
    window = MainWindow(start_path=arguments.path, open_last=arguments.open_last)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
