# Releases: installed copies

The repository runs from a checkout (`src/main.sh`, `.venv/bin/datapack-emulator`).
This page is about the three ways to give someone a copy that is not a
checkout: a wheel from PyPI, a folder built with PyInstaller, and the starter
pack both of them carry.

## The starter pack

A checkout opens the first pack of `samples/`. An installed copy has no such
folder, so a small pack ships **inside** the package, at
`datapack_emulator/samples/starter/`: a load function, a tick function and one
function called every five seconds. `project.samples()` prefers the samples
folder of the checkout — or, for an installed copy, the per-user one
(`<data folder>/samples`, so your own packs win over the packaged pack) — and
falls back to the packaged one.

The packaged pack belongs to pip: an update replaces it, and the folder may be
read-only. So the window's cold start and
[`project samples --install`](cli.md#project--saved-projects-and-recent-files)
**copy** it into the per-user samples folder and open the copy
(`project.default_sample(copy=True)`); an existing copy is never overwritten.
`project samples` on its own only prints where the packs are.

Keep it small and valid on every version its `pack.mcmeta` declares — today
1.21 upwards, because it uses the singular `function/` folders only, which is
what `tests/test_samples.py` checks. Widening it means shipping the
`functions/` spelling too (an overlay, as `samples/hat_v2` does).

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
/tmp/try/bin/datapack-emulator-cli --version
/tmp/try/bin/datapack-emulator-cli run "$(/tmp/try/bin/datapack-emulator-cli project samples | head -1)" --ticks 120
```

On Windows the venv's programs are in `Scripts\` instead of `bin/`:

```bat
py -m venv C:\try && C:\try\Scripts\pip install dist\datapack_emulator-0.1.0-py3-none-any.whl
C:\try\Scripts\datapack-emulator-cli project samples
```

The sdist is built by the same command and carries what a checkout needs to
build and test from it (`MANIFEST.in`): `src/`, `tests/`, `samples/`, `docs/`
and `packaging/`. `python -m pytest -q` passes from an unpacked sdist.

An installed copy keeps its projects and the window's reports in the per-user
**data** folder (`~/.local/share/datapack-emulator`,
`~/Library/Application Support/datapack-emulator`, `%APPDATA%\datapack-emulator`)
and the client jars, unpacked projects and window state in the per-user
**cache** folder (`~/.cache/datapack-emulator`, `~/Library/Caches/datapack-emulator`,
`%LOCALAPPDATA%\datapack-emulator\cache`) — see
[architecture](architecture.md#folders) and `settings/main.py`. The command
line writes its own reports next to where it runs (`./generated/`, or
`--html`), not in those folders.

## A folder to double-click (PyInstaller)

```sh
python -m pip install -e .[gui,release]
pyinstaller packaging/datapack-emulator.spec     # dist/datapack-emulator/
```

The spec builds both executables into one folder: `datapack-emulator`
(windowed) and `datapack-emulator-cli` (console). It carries the `.ui` files,
the starter pack and pyqtgraph's data files, and drops Qt modules the window
never uses (PySide6's own hook still brings the Qt libraries in, so the folder
is large).

Each file travels to the path it is read from inside the package
(`datapack_emulator/samples/starter/…`, `datapack_emulator/window/*.ui`);
`tests/test_project.py` guards that rule, since a wrong destination only shows
up in a built copy. Build one and run it before publishing: two `Analysis`
objects collected into one folder is not something the test suite can check.

Per platform, from a machine of that platform (PyInstaller does not
cross-compile):

| platform | what to run | what to ship |
| --- | --- | --- |
| Linux | `pyinstaller packaging/datapack-emulator.spec` | `dist/datapack-emulator/` as a `.tar.gz` |
| Windows | the same | `dist\datapack-emulator\` as a `.zip` |
| macOS | the same | `dist/Datapack Emulator.app` (the spec's `BUNDLE` step, macOS only); signing it needs an Apple certificate |

Smoke-test a build before shipping it:

```sh
dist/datapack-emulator/datapack-emulator-cli --version
dist/datapack-emulator/datapack-emulator-cli project samples     # the packaged pack
dist/datapack-emulator/datapack-emulator-cli run <a pack> --ticks 5
dist/datapack-emulator/datapack-emulator --no-last-project       # the window opens
```

(on Windows, `dist\datapack-emulator\datapack-emulator-cli.exe …`). The window
one matters: `window.ui` holds a `QWebEngineView`, which Qt builds through its
*designer* plugins — the spec carries them because PySide6's PyInstaller hook
does not, and without them the window starts with a missing widget and dies.

## Version numbers

`pyproject.toml`'s `version` is the only place a release number lives:
`datapack_emulator.__version__` reads it back from the installed
distribution (`0.0.0+checkout` in a checkout that was never installed), and
both programs print it with `--version`. Bump it, run the tests, tag
(`git tag v0.2.0`), then build and upload.
