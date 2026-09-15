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

### Menus

| menu | entries |
| --- | --- |
| file | new / open / save / save as project ([projects](projects.md)) · recent projects · add datapack… · add a recent datapack · remove datapack · reload datapacks · load client jar… · download client jar for this version · quit |
| edit | for the explorer selection: open in source view · open in external editor · open in external file manager · copy path; quick open… · search in pack… · analyze line at cursor |
| run | run all · run emulator · step one tick · stop · run tests · run profiler (rebuild the report of the current world without running) · run graphview (rebuild the call graph for the current version) · version engine… · export call graph (.dot) |
| view | explorer · inspector · logs · world (show or hide each dock) · reset layout · environment / profiler / call graph / source tab · command line · add command line as test |

Hovering a menu entry explains it in the status bar; buttons, filters,
column headers and inspector rows explain themselves in tooltips.

The window remembers its size, position and dock layout between sessions
(*view › reset layout* restores the default), and the last ten projects and
datapacks (*file › recent …*).

### Search

* *edit › quick open…* (Ctrl+P): type part of a function or function tag id,
  ↑/↓ to pick, Enter to open it in the source view.
* *edit › search in pack…* (Ctrl+Shift+F): every function line containing
  the text, opened at that line.

Both search the version being emulated (base pack and active overlays).

*export call graph (.dot)* writes `generated/<pack>-<version>.dot` for
Graphviz, from the graph on screen or, if none was built yet, from the
current version.

### Tabs

* **Environment** (scrolls when the window is short)
  * *world*: the Minecraft version (on load, the newest stable release the
    pack declares whose server reads its `pack.mcmeta` cleanly) with a note
    on what that version makes of the pack (compatible or not, unreadable
    metadata, folder spelling, active overlays); players online, meaning fake
    players `Player1…` present from the start (they are what `@a`, `@p` and
    `@r` select, what `execute as @a` runs as and who receives `tellraw`); a
    random seed for `@r` and `sort=random`; and *summon…*, *set score…* and
    *show world* to change the current world.
  * *run*: ticks (`-1`, shown as *∞ until stopped*, runs for an infinite
    number of ticks until you press stop; 20 ticks are one second) and speed —
    as fast as possible, or real time at 20 ticks per second (switchable
    while running). Runs tick in small batches, so the window stays
    responsive and logs keep flowing. *run tests during runs* makes run all,
    run emulator and step also run the tests below, each in its tick and in
    the run's world, filling the result column as ticks pass (tests a run
    does not reach say so).
  * *tests*: commands run on the server console in a fresh world —
    `function hat:tick`, `say hi`, `scoreboard players get …`, or
    `execute as Player1 run trigger hat` for what a player would type.
    Columns:
    * *tick*: the server tick it runs in. As in game, a command arrives after
      that tick's functions: tick 0 is the first tick, where
      `#minecraft:load` and `#minecraft:tick` have both run. Untick the box to
      skip the test.
    * *command*; *expect output*, text the game output must contain; *expect
      value*, a range the command's result must be in (`5`, `1..`, `..3`,
      `1..4` — e.g. `scoreboard players get` returns the score); and the
      *result*: hover it for the records it produced, double-click it to
      select them in the logs.

    A test passes when the command succeeds without a visible error and the
    expectations that are set hold. *run tests* (F8) runs them in the
    window's world, so the world dock shows what they did; *run selected*
    runs only the selected ones. *duplicate* and ↑/↓ reorder them, and a
    right-click on a test runs it, shows its records, analyzes its command,
    duplicates, moves or removes it. Tests can also be added from the command
    line (*add as test*, Ctrl+T), from a log record or from a line of the
    source view, at the tick they ran in.
  * *notes*: your own notes on the project. Notes on single functions are
    written from their right-click menu (*edit note…*) and shown in the
    inspector.

    Tests, notes and every setting here are saved in the project's `.dpemu`
    file (Ctrl+S); the title shows `*` while there are unsaved changes, and
    closing asks to save them. See [projects](projects.md).

* **Profiler** — what one average tick costs, as estimated by the cost model:
  * *per tick*: a tree of call paths — `#minecraft:tick` › `hat:tick` › the
    functions it calls, `#minecraft:load`, `<schedule>` for scheduled
    functions — with, per path, total ms/tick (including what it calls),
    share of a tick, self ms/tick (its own lines), calls/tick and
    commands/tick: the run's totals divided by its ticks. Columns sort;
    double-click a function to open it, right-click for its menu. Recursion
    deeper than 32 calls is added up in one `…` row. The summary gives the
    average and worst tick against the 50 ms budget.
  * *HTML report* (`generated/index.html`): the same call tree, and a table
    per function (calls, commands, self and total ms per tick, share of a
    tick, calls and total ms for the whole run).
* **Call graph** — layered DAG of `function`, `schedule`, `execute if function`
  and tag edges. Purple nodes are tags, blue ones come from an overlay, orange
  ones are macros, red ones are called but missing.
* **Source** — the selected file with syntax highlighting for `.mcfunction`
  (commands, subcommands, selectors, resource locations, NBT, macros,
  comments) and JSON/`pack.mcmeta`. Right-click a line to:
  * **analyze this line** (also Ctrl+I): the inspector explains it without
    running it — what the command does, each `execute` step in words (who,
    where, which condition), what each selector matches, the functions and
    tags it references (and what a tag resolves to), entity/item/block ids
    against the client jar, whether its SNBT and text components parse, the
    macro arguments it needs, whether the emulator runs it fully, whether the
    function loads in this version (and what it would need), and its cost;
  * run this line or this function in the current world, or add the line as
    a test;
  * show callers and calls (from the call graph, with each edge's kind);
  * edit the function's note.

### Docks

All four are open by default and can be toggled from *view*.

* **explorer** — every analyzed pack as it sits on disk, one root per pack in
  load order: `pack.mcmeta`, `pack.png`, `data/`, and one subtree per overlay
  directory with its format range. Right-click a pack's root to remove it from
  the project or to load it earlier or later; *add datapack…* adds another.
* **inspector** — facts about the selection: for the pack, its declared
  formats and active overlays for the current version; for a function, its
  calls, estimated cost, and which features it uses that the current version
  lacks.
* **logs** — one row per record: tick, source, level, where
  (`function:line`), message. Tooltips show the command, the vanilla
  translation key and the version. Above the table:
  * *app* / *emulator* / *game* checkboxes: which sources to show — what this
    program does, what the emulation engine notices, what Minecraft itself
    would print;
  * *level*: the lowest level shown (`debug`, `info` — the default —,
    `warning`, `error`); silent failures inside functions are `debug`;
  * a text filter matched against the message;
  * the record counts, and *clear*, which empties the log.

  Below the table, a **command line** (Ctrl+L) runs any command in the
  current world, as the server console would (`execute as Player1 run
  trigger hat` to act as a player). Enter runs it, ↑/↓ walk the history, a
  leading `/` is optional; *add as test* (Ctrl+T) turns it into a test. A
  world that has not ticked yet runs its first tick first, like a server
  that is up. The command's feedback and errors appear in the logs, and the
  world dock updates.
* **world** — the current world, refreshed after runs, steps, tests and typed
  commands (a few times a second during a run), with separate filters for
  holders/entities and objectives, and *summon…* / *objective…* buttons.
  Every change made here runs as a command through the command line, so it
  is logged and behaves as in game (player data stays read-only):
  * *scoreboard*: a grid with one row per score holder and one column per
    objective (criterion and display slot in the header). Cells that changed
    since the last refresh are yellow, enabled triggers are blue, and
    hovering a value lists the values the score took and at which game time.
    Double-click a cell to set it; right-click to set, add or remove 1,
    reset, enable a trigger, copy, or **graph the score over time**.
  * *entities*: every entity — type, position, tags and how many items it
    carries — with its full NBT (inventory included) as a tree (what
    `data get entity` shows, built when expanded; at most
    1000 listed). Double-click a value to change it.
  * *storage*: every command storage and its contents; double-click a value
    to change it, right-click to remove or copy it.

### Right-click

| where | menu |
| --- | --- |
| explorer row | open in source view · open in external editor · open in external file manager · copy path |
| call-graph node | same, plus *show in inspector*; tags open their `.json` |
| profiler row | same as a graph node |
| explorer file, call-graph node, profiler row of a function | also *edit note…* |
| source view | the editor's own menu · analyze this line · run this line · add this line as a test · run this function · show callers and calls · edit note on this function |
| log record | copy error message (or copy message) · copy with details · open file in source view, at the line the record came from (double-click too) · analyze the command · add the command as a test |
| test | run this test · show its records in the logs · analyze the command · duplicate · move up / down · remove · add test |
| world › score | set… · add 1 · remove 1 · reset · enable trigger · graph over time… · copy value · new objective… |
| world › entity | change value… (on an NBT value) · teleport… · add tag… · give item… and clear inventory (players) · set item in slot… · kill · run a command as this entity (starts `execute as @e[nbt={UUID:[I;…]},limit=1] at @s run `, or the player's name) · copy UUID · copy data (SNBT) |
| world › storage value | change value… · remove · copy value |

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
| Ctrl+O | add datapack |
| Ctrl+R | reload datapacks |
| Ctrl+N / Ctrl+Shift+O | new / open project |
| Ctrl+S / Ctrl+Shift+S | save / save project as |
| Ctrl+Return | open selection in external editor |
| Ctrl+Shift+Return | open selection in external file manager |
| Ctrl+P | quick open |
| Ctrl+Shift+F | search in pack |
| Ctrl+I | analyze the source view's line |
| Ctrl+L | command line |
| Ctrl+T | add the command line's command as a test |
| Ctrl+1 … Ctrl+4 | environment / profiler / call graph / source tab |
| Alt+1 … Alt+4 | show or hide explorer / inspector / logs / world |
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
players and seed. *run tests* also runs the environment tab's tests in every
version, each in its tick.

Right, top: one row per version — format, status (`ok`, `warnings`,
`errors`, `tests failed`, `unsupported`), commands run, total and worst-tick
time, counts, tests passed (`3/4`), unknown commands, active overlays.

Right, bottom: the records of the selected version, with the same source /
level / text filters as the logs dock. *export html* writes
`generated/<pack>-matrix.html`.
