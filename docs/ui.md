# The window

Everything here, except what needs a screen (opening files elsewhere, the
graph's drawing, the layout), can also be done from the
[command line](cli.md): each command there names the window feature it
matches.

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
| file | new / open / save / save as project ([projects](projects.md)) · save file / revert file / rename function… / complete here (the source view) · open recent project (numbered, with *clear the list*) · open last project (Ctrl+Alt+O) · open the last project on launch (a tick: the window starts on that project instead of the sample datapack) · add datapack… · add a recent datapack · remove datapack · reload datapacks · load client jar… · download client jar for this version · quit |
| edit | for the explorer selection: open in source view · open in external editor · open in external file manager · copy path; quick open… · search in pack… · analyze line at cursor · show in call graph (the source view's function) |
| run | run all · run emulator · step one tick · stop · run tests · run profiler (rebuild the report of the current world without running) · run graphview (rebuild the call graph for the current version) · check pack (the problems dock) · version engine… · export call graph (.dot) |
| debug | toggle breakpoint · remove all breakpoints · continue · step into · step over · step out · pause (see the [debugger](#debugger-dock)) |
| view | explorer · inspector · logs · world · debugger · problems (show or hide each dock) · reset layout · environment / profiler / call graph / source tab · command line · add command line as test |

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
      `1..4` — e.g. `scoreboard players get` returns the score); *checks*,
      what must hold in the world afterwards, one per line (double-click to
      edit: `score #global counter = 3`, `storage ns:mem x = 1b`,
      `@e[type=pig] = 2`, `block 0 64 0 = stone`, `if entity @a[tag=won]`,
      `!=` to invert — see [checks](cli.md#checks); with checks the command
      may be empty; a line that cannot be read reopens the editor with what
      you typed and why); and the *result*: hover it for the records it
      produced, double-click it to select them in the logs. Editing a test
      clears its result ("edited: run it again").

    A test passes when the command succeeds without a visible error and the
    expectations that are set hold; a test with only checks passes when they
    all hold. *run tests* (F8) runs them in the
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
  * *keep as baseline* keeps this run's numbers; the report then opens on a
    table of what each function, and each line, costs per tick before and
    after, biggest change first. *clear baseline* forgets them. A new pack or
    version leaves the baseline alone, so a run before a change can be
    compared with a run after it; the report names the pack and version the
    kept run came from. The baseline lives in the window and is not saved
    with the project — the shell's `.baseline save FILE` / `.baseline load
    FILE` (and `run --save-profile` / `--baseline`) keep one in a file.
  * *HTML report* (`generated/index.html`): the comparison (when a baseline
    is kept), a flame graph of the call tree (each bar a call path, its width
    its share of its caller, the roots' share of the whole run; paths under
    0.2% of their caller are left in it), the same call tree, the dearest
    lines (the single commands that cost the most, with how often the line
    ran and its ms per tick), and a table per function (calls, commands, self
    and total ms per tick, share of a tick, calls and total ms for the whole
    run). Right-clicking a bar or a row of any of them gives the same menu as
    a call-graph node (open the function, callers and calls, note…).
* **Call graph** — layered DAG of `function`, `schedule`, `execute if function`
  and tag edges. Purple nodes are tags, blue ones come from an overlay, orange
  ones are macros, red ones are called but missing. *Show in call graph*
  (Ctrl+Shift+G, from the source view, the edit menu or a file's menu)
  centres the view on a function or function tag and rings it in red, with
  a thinner ring on its direct callers and calls; a busy node keeps a
  readable zoom and the rest is a pan away. For a file the version does not
  use (another overlay's copy), the id its folders give is shown.
* **Source** — the selected file with syntax highlighting for `.mcfunction`
  (commands, subcommands, selectors, resource locations, NBT, macros,
  comments) and JSON/`pack.mcmeta`, **editable**: the label and the tab show
  `●` while there are unsaved edits, Ctrl+S (or *file › save file*,
  Ctrl+Alt+S) writes the file — while the view has unsaved edits Ctrl+S saves
  it wherever the focus is, otherwise it saves the project — and reloads the
  datapacks, which stops a run; *file › revert file* reads the file again.
  The file's line endings are kept, the breakpoints of the file follow the
  lines they were on, and saving asks first when another program wrote the
  file meanwhile. Saving is refused while the debugger is stopped (the
  reload would pull the world from under it). Opening another file, opening
  a project or closing the window with unsaved edits asks to save, discard or
  stay; clicking the file that is already open, or the debugger stopping in
  it, keeps the edits without asking. A file saved inside a project's packs
  marks the project changed (saving the project keeps the edit). While you
  type, lines the emulated version would refuse are underlined in red with an
  amber mark in the gutter — unknown or missing commands and subcommands,
  unknown selector options, macro lines that are not templates (or macros
  before 1.20.2), JSON that does not parse — and hovering one says why; the
  problems dock reports the first line that stops each function from loading,
  so it shows fewer of them ([`check --lines`](cli.md#check--problems) prints
  the same list as the view). **Ctrl+Space** completes what is being typed:
  the version's commands, `execute` subcommands and conditions, `execute
  store` targets and selector options, the pack's function and `#tag` ids
  after `function`, `schedule function`, `schedule clear` and `execute … run
  function` (a leading `/` and a macro line's `$` are read past), and the
  client jar's ids after
  `summon`, `setblock`, `give`, `clear`, `playsound`, `particle`, `effect`
  and `enchant` when a jar is loaded (the same list as
  [`complete`](cli.md#complete--what-can-be-typed-next)). **F2** renames the
  function shown: its file moves to the new id's path and every reference to
  the id in the pack's `data/` follows, after a confirmation saying how many
  lines change ([`rename`](cli.md#rename--a-function-and-its-references) does
  the same, and says what it reads and what it leaves alone). The function's
  note and its breakpoints follow the new id. It is refused while the debugger
  is stopped, and unsaved edits are only asked about once the rename is
  agreed. Images are shown read-only. Right-click a line to:
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

All six are open by default and can be toggled from *view*.

* **problems** — what the emulated version refuses or cannot run in the
  pack, found without running ([command line](cli.md#check--problems):
  `check`, which lists every kind): functions and tags that do not load,
  unknown selector options, SNBT and (with a client jar) ids the version
  does not have, calls to missing functions, broken JSON resources and
  unknown conditions or item functions in them, and — as notes — unused
  functions and recursion. With a client jar loaded it also reports JSON
  fields the version's own files never use — a typo like `resutl`, or a value
  of a kind that never appears there — read out of the jar's own data pack
  ([`schema`](cli.md#schema--what-a-versions-own-files-hold) prints what it
  learned, and says what such a check can and cannot claim). It is checked
  again once after a pack loads or
  the version or client jar changes (while the dock is hidden, when it is
  next shown), or with *check again* (Ctrl+Shift+K); if a check fails, the
  label says why and the pack still loads.
  Filter by severity or text; double-click a problem to open its file at
  the line, right-click to analyze the line or copy the problem.
* **explorer** — every analyzed pack as it sits on disk, one root per pack in
  load order: `pack.mcmeta`, `pack.png`, `data/`, and one subtree per overlay
  directory with its format range. Right-click a pack's root to remove it from
  the project or to load it earlier or later; *add datapack…* adds another.
  It follows what you look at: *show in inspector* (from the call graph, the
  profiler, notes or the source view), *show callers and calls*, *analyze*
  from a log record, the problems dock or the source view, and opening a
  file in the source view select that file's row, expanding the folders
  above it (a `#tag` selects the tag's `.json`, not a function of the same
  name). The inspector comes to the front, and a hidden explorer is shown.
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
  * *seen by*: `everyone` shows every record; a player shows only the chat
    that player reads — `tellraw`, `title` and `msg` sent to them, and `say`
    or `me` sent to everyone;
  * a text filter matched against the message;
  * the record counts, and *clear*, which empties the log.

  Below the table, a **command line** (Ctrl+L) runs any command in the
  current world, as the server console would (`execute as Player1 run
  trigger hat` to act as a player). Enter runs it, ↑/↓ walk the history, a
  leading `/` is optional; *add as test* (Ctrl+T) turns it into a test, and
  *step on sent command* runs one more tick after each command, like step
  (F7). A
  world that has not ticked yet runs its first tick first, like a server
  that is up. The command's feedback and errors appear in the logs, and the
  world dock updates.
* **world** — the current world (scores, entities, storage, blocks; the label
  above them gives the game time, counts, the time of day and the weather),
  refreshed after runs, steps, tests and typed
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
  * *blocks*: every block a command placed (the rest is air), by position
    (and dimension outside the overworld) with its state and item count, and
    its block entity data as a tree; double-click a value to change it
    (`data modify block`).
  * *snapshots*: worlds kept aside. *take* keeps the world as it is (entities,
    blocks, scores, storage, server state, advancement progress, the
    schedules and the profiler); double-click a row to name it. *rewind* puts
    the selected one back and the world goes on from there: the pack is not
    reloaded (the version stays), and the profiler's ticks go back with the
    world, so its averages match what is on screen. *compare* shows what
    changed between two selected snapshots, or between one and the world now
    — scores, objectives, triggers, entities and their NBT, storage, blocks
    and their block entity data, gamerules and each field of the server
    state, as *before → after*. Snapshots belong to the world they were taken
    in: anything that builds a new world (a run, a reload, another pack or
    another version) forgets them, as the shell does; they are not saved with
    the project. Taking one or rewinding is refused while the debugger is
    stopped. Each snapshot is a full copy of the world, so a big world costs
    memory (and a moment) per snapshot; *remove* frees them.

* **debugger** <a id="debugger-dock"></a> — breakpoints, stepping and
  watches ([command line](cli.md#the-debugger): the shell's `.break`,
  `.watch` and stop prompt, `project debug`). Click the source view's gutter
  (or press F9 on a line) to set or remove a breakpoint: a red dot, stopping
  **before** that line (a comment or blank line moves it to the next
  command). When a run, a step, a test or a typed command reaches one, the
  window stops there — the line is yellow in the source view, and the
  world dock, the logs and the watches show the world as it is at that
  point. The buttons answer:
  * *continue* (Ctrl+F5) runs to the next breakpoint;
  * *into* (F11) runs the line and stops at the next function line, inside
    calls too;
  * *over* (F10) stops at the next line of this function (or of its caller
    once it ends);
  * *out* (Shift+F11) stops at the next line of the caller;
  * *pause* (Ctrl+F6) stops a running emulation at its next function line;
  * *stop* (and the toolbar's stop, Shift+F5, which is enabled while
    stopped) abandons the rest of the tick; the world keeps what already
    ran, and the tick still counts (the game time moves on).

  A step ends with its tick. While stopped, the command line runs commands
  as the stopped line would (same executor and position), and run, step,
  run tests (the buttons and a test's *run this test*), the engine and the
  version choice wait; reloading or replacing the world abandons the tick
  first. A pause that nothing reached is forgotten when the run ends. The tabs:
  * *call stack*: the running functions, innermost first, with each one's
    line and executor; double-click to open one;
  * *context*: the stopped line's function, command, tick, executor,
    position, rotation, dimension and depth;
  * *watches*: expressions evaluated at every stop and after every run —
    `score HOLDER OBJECTIVE` (`@s` works), `storage ID [PATH]`,
    `entity SELECTOR [PATH]`, `block X Y Z [PATH]`, `if …`/`unless …`
    (`if function` is refused: watches only read), `executor`, `position`,
    `rotation`, `dimension`; `@s` is the stopped line's executor, and the
    server once nothing is stopped; double-click to change one;
  * *breakpoints*: every breakpoint with its hit count; untick to disable
    one, double-click the condition column to give it an `execute`
    condition (`if score @s x matches 5`), double-click the location to open
    it.

  Breakpoints and watches are saved with the project. When a project opens
  or the world is rebuilt, each breakpoint moves to its function's first
  command on or after its line, and the logs warn about the ones the
  version does not have. *next* and *out* follow the call stack's depth,
  so past the end of a tick function they stop in whatever runs next.

### Right-click

| where | menu |
| --- | --- |
| explorer row | open in source view · show in call graph (functions and function tags) · open in external editor · open in external file manager · copy path |
| call-graph node | same, plus *show in explorer* and *show in inspector*; tags open their `.json` |
| profiler row | same as a graph node |
| explorer file, call-graph node, profiler row of a function | also *edit note…* |
| source view | the editor's own menu · save file · revert file · rename function… (F2) · complete here (Ctrl+Space) · analyze this line · run this line · add this line as a test · toggle breakpoint · run this function · show callers and calls · show in call graph · show in inspector · edit note on this function |
| source view gutter | click: set or remove a breakpoint |
| log record | copy error message (or copy message) · copy with details · open file in source view, at the line the record came from (double-click too) · analyze the command · add the command as a test |
| test | run this test · show its records in the logs · analyze the command · edit checks… · duplicate · move up / down · remove · add test |
| world › score | set… · add 1 · remove 1 · reset · enable trigger · graph over time… · copy value · new objective… |
| world › entity | change value… (on an NBT value) · teleport… · add tag… · give item… and clear inventory (players) · set item in slot… · kill · run a command as this entity (starts `execute as @e[nbt={UUID:[I;…]},limit=1] at @s run `, or the player's name) · copy UUID · copy data (SNBT) |
| world › storage value | change value… · remove · copy value |
| world › block | change value… and remove (on a block entity value) · replace block… · set item in slot… · remove block · run a command here · copy block state |

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
| Ctrl+Alt+O | open last project |
| Ctrl+S / Ctrl+Shift+S | save the project (the source view's file when it has the focus and unsaved edits) / save project as |
| Ctrl+Alt+S | save the source view's file |
| Ctrl+Return | open selection in external editor |
| Ctrl+Shift+Return | open selection in external file manager |
| Ctrl+P | quick open |
| Ctrl+Shift+F | search in pack |
| Ctrl+I | analyze the source view's line |
| Ctrl+Shift+G | show the source view's function in the call graph |
| Ctrl+L | command line |
| Ctrl+T | add the command line's command as a test |
| F9 | toggle a breakpoint on the source view's line |
| Ctrl+F5 | continue (debugger) |
| F10 / F11 / Shift+F11 | step over / into / out (debugger) |
| Ctrl+F6 | pause a running emulation (debugger) |
| Ctrl+Space | complete what is being typed (the source view) |
| F2 | rename the function in the source view |
| Ctrl+1 … Ctrl+4 | environment / profiler / call graph / source tab |
| Ctrl+Shift+K | check the pack (problems dock) |
| Alt+1 … Alt+6 | show or hide explorer / inspector / logs / world / debugger / problems |
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

*run matrix* runs the versions one after another on a background thread,
so the window stays usable: rows appear as each version finishes, and the
progress bar and status bar follow. *cancel* stops after the current tick —
the versions that ran stay, the one running shows as `cancelled` with the
ticks it reached (`7/20` in the *ticks* column and the detail line), and the
rest are skipped; its tests that never ran count as skipped. While a run
goes, the version list, the selection buttons, the range and the settings
are locked. Closing the window cancels the run and waits for it; switching
the pack cancels it, clears the table and lets it end in the background. If
a version crashes the run, the status bar names it and how many versions
did not run.

Right, top: one row per version — format, status (`ok`, `warnings`,
`errors`, `tests failed`, `unsupported`, `cancelled`), ticks, commands run,
total and worst-tick time, counts, tests passed (`3/4`, with the skipped
ones), unknown commands, active overlays.

Right, bottom: the records of the selected version, with the same source /
level / text filters as the logs dock. *export html* writes
`generated/<pack>-matrix.html`.
