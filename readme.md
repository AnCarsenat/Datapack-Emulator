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
python -m venv .venv
.venv/bin/pip install -e ".[gui]"

.venv/bin/datapack-emulator          # the window; press F5
```

The first pack in `samples/` opens on startup. Headless:

```sh
.venv/bin/datapack-emulator-cli run    samples/hat_v2 --version 1.21.4 --vanilla
.venv/bin/datapack-emulator-cli matrix samples/hat_v2 --declared --boundaries
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
src/datapack_emulator/emulator/   model, commands, runtime, analysis, version engine (no Qt)
src/datapack_emulator/window/     window.ui, engine.ui, download.ui, and the code that wires them
src/datapack_emulator/project.py  projects/ files
tests/          pytest suite (no network, no Minecraft install needed)
tools/          generate_version_data.py
docs/           documentation
samples/        example datapacks
projects/       your saved projects      (git-ignored)
generated/      reports and .dot files   (git-ignored)
.cache/         downloaded client jars   (git-ignored)
```

## Contributing

Bug reports, command handlers, UI work and docs are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, ground rules and the checks CI
runs.

## License

GPL-3.0 — see [LICENSE](LICENSE).
