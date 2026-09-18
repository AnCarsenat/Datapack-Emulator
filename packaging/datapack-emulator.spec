# PyInstaller spec for the window: one folder per platform.
#
#   pip install -e .[gui,release]
#   pyinstaller packaging/datapack-emulator.spec         # dist/datapack-emulator/
#
# The command line is built from the same spec as a second executable, so a
# copy ships both (see docs/releases.md).

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SOURCE = Path(SPECPATH).parent / "src"

# window.ui and the starter pack are read at runtime, so they travel along
DATA = [
    (str(SOURCE / "datapack_emulator/window/window.ui"), "datapack_emulator/window"),
    (str(SOURCE / "datapack_emulator/window/engine.ui"), "datapack_emulator/window"),
    (str(SOURCE / "datapack_emulator/window/download.ui"), "datapack_emulator/window"),
    (str(SOURCE / "datapack_emulator/window/search.ui"), "datapack_emulator/window"),
    (str(SOURCE / "datapack_emulator/window/score_graph.ui"), "datapack_emulator/window"),
]
# the pack's own path under src/ is where it is read from at runtime
# (settings.PATHS.PACKAGED_SAMPLES), so the destination is that path as is
SAMPLES = [
    (str(path), str(path.parent.relative_to(SOURCE)))
    for path in sorted((SOURCE / "datapack_emulator/samples").rglob("*"))
    if path.is_file()
]
DATA += SAMPLES
# pyqtgraph's own hook knows what it needs (its colour maps are .csv files)
DATA += collect_data_files("pyqtgraph")
# window.ui holds a QWebEngineView, which QUiLoader builds through Qt's
# designer plugins; PySide6's PyInstaller hook does not collect those, and
# without them the window starts with a missing widget and dies
import PySide6  # noqa: E402

DESIGNER = Path(PySide6.__file__).parent / "Qt" / "plugins" / "designer"
DATA += [(str(plugin), "PySide6/Qt/plugins/designer") for plugin in sorted(DESIGNER.glob("*"))]

window = Analysis(
    [str(SOURCE / "datapack_emulator/app.py")],
    pathex=[str(SOURCE)],
    datas=DATA,
    hiddenimports=["datapack_emulator.window", "pyqtgraph.graphicsItems"],
    excludes=["tkinter", "pytest", "PySide6.QtQuick", "PySide6.Qt3DCore"],
    noarchive=False,
)
window_pyz = PYZ(window.pure)
window_exe = EXE(
    window_pyz,
    window.scripts,
    [],
    exclude_binaries=True,
    name="datapack-emulator",
    console=False,  # the window has no terminal
)

cli = Analysis(
    [str(SOURCE / "datapack_emulator/cli/__main__.py")],
    pathex=[str(SOURCE)],
    datas=SAMPLES,  # the command line reads the starter pack, nothing Qt
    excludes=["tkinter", "pytest", "PySide6", "pyqtgraph", "numpy"],
    noarchive=False,
)
cli_pyz = PYZ(cli.pure)
cli_exe = EXE(
    cli_pyz,
    cli.scripts,
    [],
    exclude_binaries=True,
    name="datapack-emulator-cli",
    console=True,
)

collected = COLLECT(
    window_exe,
    window.binaries,
    window.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    name="datapack-emulator",
)

if sys.platform == "darwin":  # a folder is not something macOS double-clicks
    BUNDLE(
        collected,
        name="Datapack Emulator.app",
        bundle_identifier="com.ancarsenat.datapack-emulator",
    )
