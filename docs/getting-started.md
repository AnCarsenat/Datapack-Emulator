# Getting started

## Requirements

* Python 3.10 or newer (developed on 3.14)
* for the window: PySide6 (with QtWebEngine, for the profiler tab), pyqtgraph,
  numpy — the emulator core and the CLI need nothing beyond the standard
  library

```sh
python -m venv .venv
.venv/bin/pip install -e ".[gui]"                   # the window
.venv/bin/pip install -r requirements-dev.txt       # plus pytest and ruff
```

Optional: a Minecraft **client.jar** for the version you target. If you have
the vanilla launcher, Prism or MultiMC installed, the jars it downloaded are
found automatically; otherwise the app can fetch one from Mojang. See
[vanilla-assets.md](vanilla-assets.md).

## Launch the window

Always from the project root:

```sh
.venv/bin/datapack-emulator      # or: python -m datapack_emulator
src/main.sh                      # same, without installing the package
```

`src/main.sh` picks `.venv/` or `src/.venv/` when one exists (else `python3`)
and puts `src/` on `PYTHONPATH`; `src/main.sh --cli …` runs the
[command line](cli.md) instead.

On a cold start the first datapack in `samples/` opens by itself (and again
whenever no project is open and the last datapack is removed), the version
combo jumps to the newest stable release the pack declares whose server reads
its `pack.mcmeta` cleanly (hat_v2: 1.21.8), and a matching client jar is
picked up if one is installed.

## First run

1. Press **F5** (*run all*). This emulates `#minecraft:load` once, then
   `#minecraft:tick` for the number of ticks set in the Environment tab (on
   1.16.1–1.19.2 the first tick runs before load, as it did then), and refreshes
   everything that depends on the run.
2. **Profiler** tab — estimated time per function, worst tick against the 50 ms
   budget. Right-click a row to open that function.
3. **Call graph** tab — the function DAG. Right-click a node to open it.
4. **Logs** dock — everything, filterable by source and level. Untick *app*
   and *emulator* to see only what a player would have seen: `say`,
   `tellraw`, `title`, and the red command errors.

## Try another version

Pick a version in the **Environment** tab and press F5 again. The command set, the
active overlays and (if a jar is installed) the message wording all follow
the selection.

To compare many versions at once, open the **engine** window (Ctrl+E) — see
[engine.md](engine.md).

## Use your own pack

*file › add datapack* (Ctrl+O) and pick the folder holding `pack.mcmeta`;
add more to run several packs together, and remove them one by one from
*file › remove datapack* or the explorer. Then *file › save project* (Ctrl+S)
to find them again next time: one `.dpemu` file holds the settings, the tests
and a copy of every datapack — see
[projects.md](projects.md).

## Without the window

```sh
.venv/bin/datapack-emulator-cli run samples/hat --version 1.21.4
.venv/bin/datapack-emulator-cli matrix samples/hat_v2 --declared --boundaries
```

See [cli.md](cli.md).
