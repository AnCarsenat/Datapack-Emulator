# The window

Every widget, dock, menu, action and shortcut is declared in
`src/datapack_emulator/window/window.ui` (main window), `src/datapack_emulator/window/engine.ui` (engine
window) and `src/datapack_emulator/window/download.ui` (client-jar download popup). The Python side only loads those files and wires behaviour, so
layout changes are made in Qt Designer, never in code.

## Main window

### Toolbar

| control | does |
| --- | --- |
| pack label | loaded pack and the version being emulated; says when that version marks the pack incompatible (made for an older or newer version) or cannot read its `pack.mcmeta` |
| client.jar label | which base-game jar is in use (tooltip: what it contains) |
| tick label | the current tick, and whether a run is going |
| run all (F5) | a fresh world for the configured ticks, then profiler and call graph |
| run emulator (F6) | the same without rebuilding the call graph |
| step (F7) | one more tick in the current world (starts one if there is none) |
| stop (Shift+F5) | ends a run and refreshes the profiler; the only way to end `∞` runs |
| engine… | open the version test engine |

### Tabs

* **Environment** (scrolls when the window is short)
  * *world*: the Minecraft version (on load, the newest stable release the
    pack declares whose server reads its `pack.mcmeta` cleanly); players
    online, meaning fake players `Player1…` present from the start (they are what `@a`, `@p` and `@r`
    select, what `execute as @a` runs as and who receives `tellraw`); and a
    random seed for `@r` and `sort=random`.
  * *run*: ticks (`-1` = until stopped; 20 ticks are one second) and speed —
    as fast as possible, or real time at 20 ticks per second (switchable
    while running). Runs tick in small batches, so the window stays
    responsive and logs keep flowing.
  * *tests*: commands run as the server in a fresh world after
    `#minecraft:load` (tick 0) or at a later tick — `function hat:tick`,
    `say hi`, `scoreboard players get …`. A test passes when the command
    succeeds without a visible error and, if *expect output* is set, that
    text appears in the game output. Untick a row to skip it; hover a result
    for the records it produced. Tests and every setting here are saved in
    the project's `.dpemu` file (Ctrl+S); the title shows `*` while there
    are unsaved changes, and closing asks to save them. See
    [projects](projects.md).

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
| explorer row | open in source view · open in external editor · open in external file manager · copy path |
| call-graph node | same, plus *show in inspector*; tags open their `.json` |
| profiler row | same as a graph node |
| log record | copy error message (or copy message) · copy with details · open file in source view, at the line the record came from; double-click opens the file too |

"External editor" and "external file manager" use the desktop's default handler
(`QDesktopServices`), so they open whatever your system associates with the
file type or folders.

### Shortcuts

| key | action |
| --- | --- |
| F5 | run all |
| F6 | run emulator |
| F7 | step one tick |
| Shift+F5 | stop |
| F8 | run tests |
| Ctrl+E | version engine |
| Ctrl+O | import datapack |
| Ctrl+R | reload datapack |
| Ctrl+N / Ctrl+Shift+O | new / open project |
| Ctrl+S / Ctrl+Shift+S | save / save project as |
| Ctrl+Return | open selection in external editor |
| Ctrl+Shift+Return | open selection in external file manager |
| Ctrl+Q | quit |

## Client-jar download popup

*file › download client jar for this version*. Confirm with *download*; the
popup then shows the progress bar, MiB received out of the total, the
transfer rate, and *cancel*. Closing the popup or pressing Escape also
cancels. On success the jar is loaded straight away; on failure the popup
says why and the log records it. If the jar for that version is already
installed, it is loaded without downloading.

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
