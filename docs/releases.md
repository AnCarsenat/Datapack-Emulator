# Releases: installed copies

The repository runs from a checkout (`src/main.sh`, `.venv/bin/datapack-emulator`).
This page is about the three ways to give someone a copy that is not a
checkout: a wheel from PyPI, a folder built with PyInstaller, and the starter
pack both of them carry.

## The starter pack

A checkout opens the first pack of `samples/`. An installed copy has no such
folder, so a small pack ships **inside** the package, at
`datapack_emulator/samples/starter/`: a load function, a tick function and one
function called every five seconds. `project.samples()` prefers the
checkout's `samples/` and falls back to the packaged one, so
`default_sample()`, the window's cold start and *file › add datapack…* all
work in an installed copy.

Keep it small and valid in every version the emulator covers: it is the first
thing a new user runs.

## A wheel (PyPI)

```sh
python -m pip install -e .[release]
python -m build                        # dist/datapack_emulator-*.whl and .tar.gz
python -m twine check dist/*
python -m twine upload dist/*          # needs a PyPI token
```

What a wheel installs:

* `datapack-emulator` (the window; needs the `gui` extra) and
  `datapack-emulator-cli` (no Qt),
* `window/*.ui` and the starter pack, through `[tool.setuptools.package-data]`,
* nothing else: the emulator core is pure standard library.

Check a built wheel before publishing it:

```sh
python -m zipfile -l dist/datapack_emulator-*.whl | grep samples
python -m venv /tmp/try && /tmp/try/bin/pip install dist/datapack_emulator-*.whl
/tmp/try/bin/datapack-emulator-cli run "$(/tmp/try/bin/python -c 'import datapack_emulator.project as p; print(p.default_sample())')"
```

An installed copy keeps its projects, cache and reports in the per-user data
folder (`~/.local/share/DatapackEmulator`, `%APPDATA%\DatapackEmulator`,
`~/Library/Application Support/DatapackEmulator`) — see
[settings](architecture.md) and `settings/main.py`.

## A folder to double-click (PyInstaller)

```sh
python -m pip install -e .[gui,release]
pyinstaller packaging/datapack-emulator.spec     # dist/datapack-emulator/
```

The spec builds both executables into one folder: `datapack-emulator`
(windowed) and `datapack-emulator-cli` (console). It carries the `.ui` files,
the starter pack and pyqtgraph's data files, and drops Qt modules the window
never uses.

Per platform, from a machine of that platform (PyInstaller does not
cross-compile):

| platform | what to run | what to ship |
| --- | --- | --- |
| Linux | `pyinstaller packaging/datapack-emulator.spec` | `dist/datapack-emulator/` as a `.tar.gz` |
| Windows | the same | `dist\datapack-emulator\` as a `.zip` |
| macOS | the same | `dist/datapack-emulator/`; a signed `.app` needs an Apple certificate |

Smoke-test a build before shipping it:

```sh
dist/datapack-emulator/datapack-emulator-cli versions
dist/datapack-emulator/datapack-emulator-cli run <a pack> --ticks 5
dist/datapack-emulator/datapack-emulator --no-last-project
```

## Version numbers

`pyproject.toml`'s `version` is the only place a release number lives. Bump
it, run the tests, tag (`git tag v0.2.0`), then build and upload.
