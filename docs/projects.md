# Projects

A project remembers what you were working on. It is one JSON file in
`projects/`, next to `samples/` (in the per-user data folder when the package
is installed without a checkout, see [architecture](architecture.md)):

```json
{
  "name": "hat",
  "datapack": "samples/hat",
  "version": "1.21.4",
  "ticks": 20,
  "players": 1,
  "seed": 0,
  "engine_versions": ["1.20.4", "1.21.4"],
  "vanilla_jar": ""
}
```

| field | meaning |
| --- | --- |
| `name` | shown in the window title |
| `datapack` | the pack folder; relative to the repository root when inside it, absolute otherwise |
| `version` | the version selected in the environment tab |
| `ticks`, `players`, `seed` | run settings |
| `engine_versions` | versions ticked in the engine window when the project was saved |
| `vanilla_jar` | a client jar picked by hand; empty when it was found automatically |
| `speed` | `fast` or `realtime` |
| `tests` | the environment tab's tests: `command`, `at_tick`, `expect`, `enabled` |

Relative paths keep a copied repository working on another machine.

## In the window

| menu | does |
| --- | --- |
| file › new project (Ctrl+N) | asks for a name, saves the current state under it |
| file › open project… (Ctrl+Shift+O) | restores version, ticks, players, jar, then opens the pack |
| file › save project (Ctrl+S) | writes `projects/<name>.json` (the pack name if untitled) |
| file › save project as… (Ctrl+Shift+S) | same, under another name or path |

The title bar shows the project name, `(unsaved)` until the first save, and
the file it lives in.

`projects/` is git-ignored except for its `.gitkeep`: projects are personal.

## From code

```python
from datapack_emulator.project import Project, list_projects

project = Project(name="hat", datapack=Path("samples/hat"), version="1.21.4")
project.save()                      # projects/hat.json
again = Project.load(list_projects()[0])
```
