# Datapack Emulator

Load a Minecraft datapack, run its functions, see what they print, what they
cost and how they call each other — for **any Minecraft version you pick**,
checked against the real base game read out of a `client.jar`.

* emulates `#minecraft:load` / `#minecraft:tick`, scoreboards, selectors,
  `execute`, macros, schedules, storage
* knows which commands exist in each release from 1.13 to 26.2, and which
  `pack.mcmeta` overlays apply
* prints Minecraft's own error messages, kept apart from the app's own logs
* profiler with per-function estimated cost, call-graph DAG, version matrix
* reads registries, tags and message strings straight from a `client.jar`

## Quick start

```sh
python -m venv src/.venv
src/.venv/bin/pip install PySide6 pyqtgraph numpy

src/.venv/bin/python ./src/main.py       # the window; press F5
```

The first pack in `samples/` opens on startup. Headless:

```sh
src/.venv/bin/python -m src.emulator run    samples/hat --version 1.21.4 --vanilla
src/.venv/bin/python -m src.emulator matrix samples/hat --declared --boundaries
```

## Documentation

Everything is in [`docs/`](docs/README.md):

* [getting started](docs/getting-started.md) · [the window](docs/ui.md) ·
  [projects](docs/projects.md) · [command line](docs/cli.md)
* [versions & overlays](docs/versions.md) ·
  [base game from client.jar](docs/vanilla-assets.md) ·
  [what is emulated](docs/emulation.md) · [version engine](docs/engine.md) ·
  [cost model](docs/costs.md)
* [architecture](docs/architecture.md)

Timings are **estimates from a cost model**, not measurements — see
[costs.md](docs/costs.md).

## Layout

```
src/emulator/   model, commands, runtime, analysis, version engine (no Qt)
src/window/     window.ui + engine.ui, and the code that wires them
src/project.py  projects/ files
tools/          generate_version_data.py
docs/           documentation
samples/        example datapacks
projects/       your saved projects      (git-ignored)
generated/      reports and .dot files   (git-ignored)
.cache/         downloaded client jars   (git-ignored)
```

## Contributing

Contributions are welcome — bug reports, new command handlers, UI work, docs.

### Setup

```sh
git clone <this repository> && cd DatapackEmulator
python -m venv src/.venv
src/.venv/bin/pip install PySide6 pyqtgraph numpy pyflakes
src/.venv/bin/python ./src/main.py
```

Work from the project root: imports are `src.…` absolute.

### Ground rules

Read [docs/architecture.md](docs/architecture.md) first. In short:

1. **Version logic goes through `src/emulator/versions.py`.** Don't compare
   version strings or pack formats anywhere else.
2. **Don't hardcode base-game facts.** Command availability comes from the
   generated `version_data.py`; ids, tags and message strings come from a
   client jar via `src/emulator/vanilla.py`.
3. **Never edit `src/emulator/version_data.py` by hand.** Regenerate it with
   `python tools/generate_version_data.py` and commit the result.
4. **All layout lives in `src/window/*.ui`.** Add widgets and actions in Qt
   Designer, then find them by object name in Python.
5. **Keep `src/emulator` free of Qt.**
6. **Keep game output and diagnostics apart.** A handler reports what
   Minecraft would say with `context.game_error("<real.translation.key>", …)`;
   what the emulator notices goes through `context.note(…)`.
7. **Say when something is a model.** Cost numbers and anything approximated
   must be labelled as such in code and docs.

### Adding a command

1. Write `cmd_<name>(command, context) -> CommandResult` in
   `src/emulator/commands/handlers.py`; report failures with vanilla
   translation keys (add missing ones to `runtime/messages.py`).
2. Register it in `HANDLERS`. Availability per version is already known.
3. Give it a cost in `src/emulator/costs.py` if the default does not fit.
4. Document it in [docs/emulation.md](docs/emulation.md).

### Before opening a pull request

```sh
src/.venv/bin/python -m pyflakes src/*.py src/emulator src/window src/settings tools
src/.venv/bin/python -m src.emulator --quiet run samples/hat --ticks 5
src/.venv/bin/python -m src.emulator --quiet matrix samples/hat --declared --boundaries --ticks 3
```

* lint clean, both commands run without a traceback
* if you touched the window, launch it and exercise what you changed
* update `docs/` when behaviour or options change
* one topic per commit, messages in the `type(scope): summary` form used in
  the history (`feat`, `fix`, `docs`, `refactor`, `chore`)

### Reporting a bug

Include the Minecraft version selected, whether a client jar was loaded, the
smallest datapack that reproduces it, and the output of
`python -m src.emulator run <pack> --version <v> --level debug`.

## License

GPL-3.0 — see [license](license).
