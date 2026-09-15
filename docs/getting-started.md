# Getting started

## Requirements

* Python 3 — developed and tested on 3.14
* PySide6 (with QtWebEngine, for the profiler tab), pyqtgraph, numpy

```sh
python -m venv src/.venv
src/.venv/bin/pip install PySide6 pyqtgraph numpy
```

Optional: a Minecraft **client.jar** for the version you target. If you have
the vanilla launcher, Prism or MultiMC installed, the jars it downloaded are
found automatically; otherwise the app can fetch one from Mojang. See
[vanilla-assets.md](vanilla-assets.md).

## Launch the window

Always from the project root:

```sh
src/.venv/bin/python ./src/main.py
```

On a cold start the first datapack in `samples/` opens by itself, the version
combo jumps to the release matching its `pack_format`, and a matching client
jar is picked up if one is installed.

## First run

1. Press **F5** (*run all*). This emulates `#minecraft:load` once, then
   `#minecraft:tick` for the number of ticks in the toolbar, and refreshes
   everything that depends on the run.
2. **Profiler** tab — estimated time per function, worst tick against the 50 ms
   budget. Right-click a row to open that function.
3. **Call graph** tab — the function DAG. Right-click a node to open it.
4. **Logs** dock — everything, filterable by source and level. Untick *app*
   and *emulator* to see only what a player would have seen: `say`,
   `tellraw`, `title`, and the red command errors.

## Try another version

Pick a version in the toolbar combo and press F5 again. The command set, the
active overlays and (if a jar is installed) the message wording all follow
the selection.

To compare many versions at once, open the **engine** window (Ctrl+E) — see
[engine.md](engine.md).

## Use your own pack

*file › import datapack* (Ctrl+O) and pick the folder holding `pack.mcmeta`.
Then *file › save project* (Ctrl+S) to find it again next time — see
[projects.md](projects.md).

## Without the window

```sh
src/.venv/bin/python -m src.emulator run samples/hat --version 1.21.4
src/.venv/bin/python -m src.emulator matrix samples/hat --declared --boundaries
```

See [cli.md](cli.md).
