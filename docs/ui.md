# The window

Every widget, dock, menu, action and shortcut is declared in
`src/window/window.ui` (main window) and `src/window/engine.ui` (engine
window). The Python side only loads those files and wires behaviour, so
layout changes are made in Qt Designer, never in code.

## Main window

### Toolbar

| control | does |
| --- | --- |
| pack label | loaded pack and the version being emulated; warns if the pack does not declare support for it |
| client.jar label | which base-game jar is in use (tooltip: what it contains) |
| version | Minecraft version to emulate — decides commands, overlays, message wording |
| players / ticks | world size and run length |
| run all (F5) | emulate + profiler + call graph |
| run emulator | emulate and refresh the profiler only |
| engine… | open the version test engine |

### Tabs

* **Profiler** — HTML report (`generated/index.html`): calls, commands, self
  and total estimated time per function, share of the run, worst tick.
* **Call graph** — layered DAG of `function`, `schedule`, `execute if function`
  and tag edges. Purple nodes are tags, blue ones come from an overlay, orange
  ones are macros, red ones are called but missing.
* **Source** — the selected file with syntax highlighting for `.mcfunction`
  (commands, subcommands, selectors, resource locations, NBT, macros,
  comments) and JSON/`pack.mcmeta`.

### Docks

All three are open by default and can be toggled from *view*.

* **explorer** — the pack as it sits on disk: `pack.mcmeta`, `pack.png`,
  `data/`, and one subtree per overlay directory with its format range.
* **inspector** — facts about the selection: for the pack, its declared
  formats and active overlays for the current version; for a function, its
  calls, estimated cost, and which features it uses that the current version
  lacks.
* **logs** — one row per record: tick, source, level, where
  (`function:line`), message. Tooltips show the command, the vanilla
  translation key and the version.

### Right-click

| where | menu |
| --- | --- |
| explorer row | open in source view · open in external editor · show containing folder · copy path |
| call-graph node | same, plus *show in inspector*; tags open their `.json` |
| profiler row | same as a graph node |

"External editor" and "containing folder" use the desktop's default handler
(`QDesktopServices`), so they open whatever your system associates with the
file type or folders.

### Shortcuts

| key | action |
| --- | --- |
| F5 | run all |
| F6 | run emulator |
| Ctrl+E | version engine |
| Ctrl+O | import datapack |
| Ctrl+R | reload datapack |
| Ctrl+N / Ctrl+Shift+O | new / open project |
| Ctrl+S / Ctrl+Shift+S | save / save project as |
| Ctrl+Return | open selection in external editor |
| Ctrl+Shift+Return | show selection's folder |
| Ctrl+Q | quit |

## Engine window

Left: version selection — a *from/to* range, the range `pack.mcmeta`
declares, *one per format* (the first release of each `pack_format`, a cheap
way to cover a wide range), all/none, or tick versions by hand; plus ticks,
players and seed.

Right, top: one row per version — format, status (`ok`, `warnings`,
`errors`, `unsupported`), commands run, total and worst-tick time, counts,
unknown commands, active overlays.

Right, bottom: the records of the selected version, with the same source /
level / text filters as the logs dock. *export html* writes
`generated/<pack>-matrix.html`.
