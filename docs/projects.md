# Projects

A project remembers what you were working on: the datapacks it analyzes, the
environment tab's settings, its tests and your notes. It is saved as one **`.dpemu` file** in
`projects/`, next to `samples/` (in the per-user data folder when the package
is installed without a checkout, see [architecture](architecture.md)).

## The `.dpemu` format

A `.dpemu` file is a zip archive:

```
hat.dpemu
├── project.json        settings, tests, notes
└── datapacks/          a copy of every analyzed pack, in load order
    ├── 0/hat/          pack.mcmeta, data/, overlays…
    └── 1/extras/
```

`.git`, `__pycache__`, `.DS_Store` and symbolic links (logged as a warning)
are left out, and so is the archive itself when it is saved inside the pack
folder. Rename the file to `.zip` to look inside with any archive tool.

`project.json`:

```json
{
  "name": "hat",
  "datapacks": ["datapacks/0/hat", "datapacks/1/extras"],
  "version": "1.21.4",
  "ticks": 20,
  "players": 1,
  "seed": 0,
  "engine_versions": ["1.20.4", "1.21.4"],
  "vanilla_jar": "",
  "speed": "fast",
  "tests": [
    {"command": "scoreboard players get #global t", "at_tick": 5, "expect": "", "enabled": true, "expect_value": "1.."}
  ],
  "tests_during_runs": false,
  "notes": "check the armor stand",
  "function_notes": {"hat:tick": "swaps the hat"},
  "archive_format": 1
}
```

| field | meaning |
| --- | --- |
| `name` | shown in the window title |
| `datapacks` | the folders of the packs inside the archive, in load order (empty when the project has none) |
| `version` | the version selected in the environment tab |
| `ticks`, `players`, `seed` | run settings |
| `speed` | `fast` or `realtime` |
| `tests` | the environment tab's tests: `command`, `at_tick`, `expect`, `expect_value`, `enabled` — results are not saved |
| `notes`, `function_notes` | your notes on the project, and per function id |
| `tests_during_runs` | whether runs and steps also run the tests |
| `engine_versions` | versions ticked in the engine window when the project was saved; ticked again when it is opened |
| `vanilla_jar` | the client jar in use when the project was saved (picked by hand or found automatically); loaded again on open if the file still exists. Jars are never put in the archive |
| `archive_format` | layout version; archives from a newer emulator are refused rather than misread |

### Opening and saving

Opening a `.dpemu` unpacks it into the cache
(`.cache/projects/<name>-<hash>/`, or the per-user cache folder) and loads its
datapacks from there, **in place of whatever was open** — the sample pack
included. Saving writes the archive again from the datapacks that are open,
so edits made to the unpacked files (for example with *open in external
editor*) are kept once you save. Reopening an archive replaces its unpacked
copy: **the `.dpemu` file is what counts.**

The archive is written to `<name>.dpemu.part` first and then swapped in, so a
failed save leaves the previous file intact (and no `.part` behind). Names
that would unpack outside the cache folder are refused, and so are archives
whose `project.json` is not a valid project.

### Older `.json` projects

Projects saved before archives existed are plain `project.json` files that
point at the pack where it is. They still open; saving one writes a `.dpemu`
beside it (`hat.json` → `hat.dpemu`) and leaves the `.json` alone. Archives
made before several datapacks were supported (`"archive_format": 1`, one
`datapack/` folder) open too and are saved in the new layout.

## In the window

| menu | does |
| --- | --- |
| file › new project (Ctrl+N) | asks for a name, saves the current state under it |
| file › open recent project | the last ten projects opened or saved that still exist (*clear the list* empties it) |
| file › open last project (Ctrl+Alt+O) | the most recent of them |
| file › open project… (Ctrl+Shift+O) | restores settings, tests and notes, then opens the project's datapacks in place of the open ones |
| file › add datapack… (Ctrl+O) | adds a pack to the ones analyzed; it loads after them |
| file › remove datapack | removes one pack (also: right-click its root in the explorer); with no project open, removing the last one opens the default pack |
| file › save project (Ctrl+S) | writes the file the project came from; a project never saved goes to `projects/<name>.dpemu` (the pack name if untitled), or asks for a name when that file already belongs to another project |
| file › save project as… (Ctrl+Shift+S) | same, under another name or path; if it fails, the project keeps its current file |

The shortcuts work while the main window has focus.

The title bar shows the project name, `(unsaved)` until the first save, the
file it lives in, and a `*` when settings, tests, notes, the datapacks or the
client jar changed since the last save. Closing the window or opening another project with unsaved
changes asks whether to save them first. A test cell still being typed in when
you save is included.

`projects/` is git-ignored except for its `.gitkeep`: projects are personal.

## From code

```python
from pathlib import Path

from datapack_emulator.project import Project, list_projects

project = Project(name="hat", datapacks=[Path("samples/hat")], version="1.21.4")
project.save()                      # projects/hat.dpemu, datapacks included
again = Project.load(list_projects()[0])
again.datapacks                     # [.cache/projects/hat-<hash>/datapacks/0/hat]
```
