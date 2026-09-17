# PyInstaller spec for the window: one folder per platform.
#
#   pip install -e .[gui,release]
#   pyinstaller packaging/datapack-emulator.spec         # dist/datapack-emulator/
#
# The command line is built from the same spec as a second executable, so a
# copy ships both (see docs/releases.md).

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
for path in sorted((SOURCE / "datapack_emulator/samples").rglob("*")):
    if path.is_file():
        DATA.append((str(path), str(Path("datapack_emulator") / path.parent.relative_to(SOURCE))))
DATA += collect_data_files("pyqtgraph", includes=["**/*.ui", "**/*.png", "**/*.svg"])

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
    datas=[entry for entry in DATA if "samples" in entry[0]],
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

COLLECT(
    window_exe,
    window.binaries,
    window.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    name="datapack-emulator",
)
