# Contributing

Contributions are welcome — bug reports, new command handlers, UI work, docs.

## Setup

```sh
git clone git@github.com:AnCarsenat/Datapack-Emulator.git && cd Datapack-Emulator
python -m venv src/.venv
src/.venv/bin/pip install -r requirements-dev.txt
src/.venv/bin/python ./src/main.py
```

Work from the project root: imports are `src.…` absolute.

## Ground rules

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

## Adding a command

1. Write `cmd_<name>(command, context) -> CommandResult` in
   `src/emulator/commands/handlers.py`; report failures with vanilla
   translation keys (add missing ones to `runtime/messages.py`).
2. Register it in `HANDLERS`. Availability per version is already known.
3. Give it a cost in `src/emulator/costs.py` if the default does not fit.
4. Document it in [docs/emulation.md](docs/emulation.md).

## Before opening a pull request

```sh
src/.venv/bin/ruff check .
src/.venv/bin/ruff format --check .
src/.venv/bin/python -m pytest
```

CI runs the same checks (plus a CLI smoke run) on every pull request.

* lint and format clean, tests green
* new behaviour comes with a test in `tests/` — the fixtures in
  `tests/conftest.py` build synthetic datapacks and a synthetic client jar,
  so no test needs the network or a Minecraft install
* if you touched the window, launch it and exercise what you changed
* update `docs/` when behaviour or options change
* one topic per pull request and per commit; commit messages follow
  [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`)
* branch from `main`, open a pull request, fill in the template

## Reporting a bug

Use the *Bug report* issue template. It asks for the Minecraft version
selected, whether a client jar was loaded, the smallest datapack that
reproduces it, and the output of
`python -m src.emulator run <pack> --version <v> --level debug`.
