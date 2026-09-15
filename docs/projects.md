# Projects

A project remembers what you were working on: the datapack, the environment
tab's settings and its tests. It is saved as one **`.dpemu` file** in
`projects/`, next to `samples/` (in the per-user data folder when the package
is installed without a checkout, see [architecture](architecture.md)).

## The `.dpemu` format

A `.dpemu` file is a zip archive:

```
hat.dpemu
├── project.json        settings and tests
└── datapack/           a copy of the pack: pack.mcmeta, data/, overlays…
```

`.git`, `__pycache__`, `.DS_Store` and symbolic links (logged as a warning)
are left out, and so is the archive itself when it is saved inside the pack
folder. Rename the file to `.zip` to look inside with any archive tool.

`project.json`:

```json
{
  "name": "hat",
  "datapack": "datapack",
  "version": "1.21.4",
  "ticks": 20,
  "players": 1,
  "seed": 0,
  "engine_versions": ["1.20.4", "1.21.4"],
  "vanilla_jar": "",
  "speed": "fast",
  "tests": [
    {"command": "function hat:tick", "at_tick": 5, "expect": "", "enabled": true}
  ],
  "archive_format": 1
}
```

| field | meaning |
| --- | --- |
| `name` | shown in the window title |
| `datapack` | `datapack` when the archive holds the pack, empty when the project has none |
| `version` | the version selected in the environment tab |
| `ticks`, `players`, `seed` | run settings |
| `speed` | `fast` or `realtime` |
| `tests` | the environment tab's tests: `command`, `at_tick`, `expect`, `enabled` (results are not saved) |
| `engine_versions` | versions ticked in the engine window when the project was saved |
| `vanilla_jar` | a client jar picked by hand; empty when it was found automatically. Jars are never put in the archive |
| `archive_format` | layout version; archives from a newer emulator are refused rather than misread |

### Opening and saving

Opening a `.dpemu` unpacks it into the cache
(`.cache/projects/<name>-<hash>/`, or the per-user cache folder) and loads the
datapack from there. Saving writes the archive again from the datapack that is
open, so edits made to the unpacked files (for example with *open in external
editor*) are kept once you save. Reopening an archive replaces its unpacked
copy: **the `.dpemu` file is what counts.**

The archive is written to `<name>.dpemu.part` first and then swapped in, so a
failed save leaves the previous file intact (and no `.part` behind). Names
that would unpack outside the cache folder are refused, and so are archives
whose `project.json` is not a valid project.

### Older `.json` projects

Projects saved before archives existed are plain `project.json` files that
point at the pack where it is. They still open; saving one writes a `.dpemu`
beside it (`hat.json` → `hat.dpemu`) and leaves the `.json` alone.

## In the window

| menu | does |
| --- | --- |
| file › new project (Ctrl+N) | asks for a name, saves the current state under it |
| file › open project… (Ctrl+Shift+O) | restores settings and tests, then opens the pack |
| file › save project (Ctrl+S) | writes the file the project came from; a project never saved goes to `projects/<name>.dpemu` (the pack name if untitled), or asks for a name when that file already belongs to another project |
| file › save project as… (Ctrl+Shift+S) | same, under another name or path; if it fails, the project keeps its current file |

The shortcuts work while the main window has focus.

The title bar shows the project name, `(unsaved)` until the first save, the
file it lives in, and a `*` when settings, tests, the datapack or the client
jar changed since the last save. Closing the window or opening another project with unsaved
changes asks whether to save them first. A test cell still being typed in when
you save is included.

`projects/` is git-ignored except for its `.gitkeep`: projects are personal.

## From code

```python
from datapack_emulator.project import Project, list_projects

project = Project(name="hat", datapack=Path("samples/hat"), version="1.21.4")
project.save()                      # projects/hat.dpemu, datapack included
again = Project.load(list_projects()[0])
again.datapack                      # .cache/projects/hat-<hash>/datapack
```
