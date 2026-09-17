# Datapack Emulator

Load a Minecraft datapack, run its functions, see what they print, what they
cost and how they call each other — for **any Minecraft version you pick**,
checked against the real base game read out of a `client.jar`.

* emulates `#minecraft:load` / `#minecraft:tick`, scoreboards, selectors,
  `execute`, macros, schedules, storage, entity NBT and inventories
* knows which commands exist in each version from 1.13 to 26.3 (26.3-rc-3), and which
  `pack.mcmeta` overlays apply
* prints Minecraft's own error messages, kept apart from the app's own logs
* shows the world as it runs — a scoreboard grid with each score's history,
  entities with their full NBT, command storage — and runs commands typed
  into it
* command tests at any tick, in the window, during runs and across versions —
  and in CI: `datapack-emulator-cli test` exits non-zero on a failure and
  writes JUnit XML
* explains any command line: execute steps, selectors, references, version
  support and cost; your own notes on the project and its functions
* profiler with per-function estimated cost, call-graph DAG, version matrix
* reads registries, tags and message strings straight from a `client.jar`

## Quick start

```sh
python -m venv .venv
.venv/bin/pip install -e ".[gui]"

.venv/bin/datapack-emulator          # the window; press F5
```

Or, from a checkout, `src/main.sh` (uses `.venv/` or `src/.venv/` if present,
no install needed beyond the requirements).

The first pack in `samples/` opens on startup. The [command line](docs/cli.md) does everything the window does, on packs or
saved projects:

```sh
.venv/bin/datapack-emulator-cli run    samples/hat_v2 --version 1.21.4 --vanilla
.venv/bin/datapack-emulator-cli matrix samples/hat_v2 --declared --boundaries
.venv/bin/datapack-emulator-cli test   projects/hat.dpemu --junit generated/tests.xml
src/main.sh --cli run samples/hat_v2 --version 26.3      # same runner through the script
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
src/datapack_emulator/cli/       the command line: datapack-emulator-cli (no Qt)
src/datapack_emulator/project.py  .dpemu project files
src/main.sh     starts the window (or --cli) from a checkout
tests/          pytest suite (no network, no Minecraft install needed)
tools/          generate_version_data.py
docs/           documentation
samples/        example datapacks
projects/       your saved .dpemu projects (git-ignored)
generated/      reports and .dot files   (git-ignored)
.cache/         downloaded client jars   (git-ignored)
```

## Contributing

Bug reports, command handlers, UI work and docs are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, ground rules and the checks CI
runs.

## License

GPL-3.0 — see [LICENSE](LICENSE).
