# Contributing

Contributions are welcome — bug reports, new command handlers, UI work, docs.

## Setup

```sh
git clone git@github.com:AnCarsenat/Datapack-Emulator.git && cd Datapack-Emulator
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # editable install with the gui and dev extras
.venv/bin/datapack-emulator
```

The package is `datapack_emulator`, in `src/` (the standard src layout).
Imports are absolute: `from datapack_emulator.emulator import Datapack`. The
editable install means edits take effect without reinstalling; pytest finds
the package without it.

## Ground rules

Read [docs/architecture.md](docs/architecture.md) first. In short:

1. **Version logic goes through `src/datapack_emulator/emulator/versions.py`.** Don't compare
   version strings or pack formats anywhere else.
2. **Don't hardcode base-game facts.** Command availability comes from the
   generated `version_data.py`; ids, tags and message strings come from a
   client jar via `src/datapack_emulator/emulator/vanilla.py`.
3. **Never edit `src/datapack_emulator/emulator/version_data.py` by hand.** Regenerate it with
   `python tools/generate_version_data.py` and commit the result.
4. **All layout lives in `src/datapack_emulator/window/*.ui`.** Add widgets and actions in Qt
   Designer, then find them by object name in Python.
5. **Keep `src/datapack_emulator/emulator` free of Qt.**
6. **Keep game output and diagnostics apart.** A handler reports what
   Minecraft would say with `context.game_error("<real.translation.key>", …)`;
   what the emulator notices goes through `context.note(…)`.
7. **Keep the command line as capable as the window.** A window feature
   comes with its `datapack-emulator-cli` subcommand or option; shared logic
   goes in `emulator/` or `project.py`, not in a controller.
8. **Say when something is a model.** Cost numbers and anything approximated
   must be labelled as such in code and docs.

## Adding a command

1. Write `cmd_<name>(command, context) -> CommandResult` in the module of
   its group under `src/datapack_emulator/emulator/commands/` (`chat.py`,
   `scoreboard.py`, `entities.py`, `data.py`, `execute.py`, `items.py`, … —
   shared helpers are in `helpers.py`); report failures with vanilla
   translation keys (add missing ones to `runtime/messages.py`).
2. Register it in `HANDLERS` (`commands/handlers.py`). Availability per
   version is already known.
3. Give it a cost in `src/datapack_emulator/emulator/costs.py` if the default does not fit.
4. Document it in [docs/emulation.md](docs/emulation.md).

## Before opening a pull request

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest
```

CI runs the same checks (plus a CLI smoke run) on every pull request.

* lint and format clean, tests green
* new behaviour comes with a test in `tests/` — the fixtures in
  `tests/conftest.py` build synthetic datapacks and a synthetic client jar,
  so no test needs the network or a Minecraft install
* if you touched the window, launch it and exercise what you changed
* update `docs/` and `readme.md` in the same pull request whenever behaviour,
  options or commands change
* one topic per pull request and per commit; commit messages follow
  [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`)
* branch from `main`, open a pull request, fill in the template

## Reporting a bug

Use the *Bug report* issue template. It asks for the Minecraft version
selected, whether a client jar was loaded, the smallest datapack that
reproduces it, and the output of
`datapack-emulator-cli run <pack> --version <v> --level debug`.
