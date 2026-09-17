# Plans

Improvements that are not built yet, roughly in the order they are worth doing.
Each entry says what it would bring, what it touches, and how it could be done.
Nothing here is promised; cross an entry out (or delete it) once it lands.

## Recommended next

### 1. Function debugger

Breakpoints on function lines, then step command by command (step into a
called function, step over, continue) while watching scores, NBT, the
executor and the position.

* **Touches:** `runtime/emulator.py` (a hook before each command), the source
  view (a breakpoint gutter), a new debug dock in `window.ui` (call stack,
  context, watches), `controllers/`.
* **How:** the emulator already dispatches one command at a time; run it on a
  worker (a generator or a thread with an event) that pauses at breakpoints and
  hands the context to the UI.
* **Size:** medium to large.

### 2. Run the engine window in the background

A large version matrix runs on the UI thread and freezes the window.

* **Touches:** `window/engine_window.py`, `ui` progress and a cancel button.
* **How:** a `QThread` worker like the client-jar download; results come back
  version by version through signals; the output bus of each run stays
  per-worker.
* **Size:** small to medium.

## Reported

* **Show in inspector does not reveal the node** (bug) — *show in
  inspector* from the profiler tree, the call graph and the other views
  fills the inspector but does not expand the explorer to that function or
  tag and highlight it. Select the matching explorer row (expanding its
  parents and scrolling to it) whenever something is shown in the inspector.
* **Open the last project on launch** (bug / feature) — the window starts on
  the first sample pack even when a project was open last time. Reopen the
  most recent project (the first entry of *open recent project*, as
  *open last project* does) when it still exists, and fall back to the
  sample only when there is none; a command-line flag or setting could turn
  it off.

## Emulation gaps

* **Predicates and advancements** — `execute if predicate`, the `predicate=`
  and `advancements=` selector arguments, and the `advancement` command:
  evaluate predicate JSON from the pack (and the jar) for the conditions the
  world model can answer (scores, entity properties, NBT, random chance), and
  keep per-player advancement progress.
* **Loot conditions and functions** — evaluate loot table conditions (random
  chance, entity/score checks, `match_tool` with the `mine` tool) and the
  common functions beyond `set_count`,
  `set_components`, `set_nbt` (`set_name`, `set_lore`, `enchant_randomly`,
  `copy_components`, `set_damage`, …); share them with item modifiers.
* **Entity data from the game** — maximum health and base attributes by
  type are a table in the code; the game has them only in code, so a
  generated table per version (like `version_data.py`) would be the next
  step. Regeneration, hunger and effects' side effects stay out of scope.
* **Default block states** — read each block's default state (the jar's
  blockstates only list the combinations) so `if block …[facing=north]`
  matches blocks placed without properties; block updates and drops stay
  out of scope.
* **Time of day clocks (26.x)** — the clock and timeline subcommands of
  `time` in 26.1+, and the world clocks they read.
* **Compare against a real server** — start a Fabric + Carpet server for a
  version, run the same pack and tests over RCON with Carpet fake players and
  `/tick step`, and diff what loaded, what failed and what the tests saw
  against the emulator (see the discussion of server-backed tests: server
  jar download, Java lookup from the launchers, world setup, EULA dialog,
  play sessions where you can join).

## Tooling

* **Richer test expectations** — besides a command and its output, check a
  score (`#global counter = 3`), an NBT path (`storage ns:mem x = 1b`), or an
  entity count (`@e[type=pig] = 2`) without writing a command; show a diff when
  it fails.
* **Problems panel** — static diagnostics for the whole pack in one dock:
  selector arguments that do not exist, invalid NBT, ids the version does not
  have, missing functions, unused functions, recursion; validate recipes, loot
  tables, predicates and item modifiers against the version's schemas
  (Spyglass mcdoc).
* **Edit in the source view** — it is read-only today; editing with save
  (Ctrl+S in the view) and an automatic reload, with the analysis updating as
  you type.
* **World snapshots** — save the world at a tick, rewind to it, and compare two
  snapshots (scores, entities, storage, inventories) to see what a tick
  changed.
* **Profiler** — the most expensive individual commands, comparing two runs
  (before/after a change), and a flame graph of the call tree.
* **Installed copies** — ship a default pack inside the package (today the
  sample only exists in a source checkout), Windows/macOS builds (PyInstaller),
  and a PyPI release.

## Code health

* **Type checking and coverage in CI** — run pyright or mypy and publish a
  coverage report with the tests.
* **Refresh version data on a schedule** — a scheduled workflow that runs
  `tools/generate_version_data.py` when a new release or pre-release appears
  and opens a pull request.

## Needs a repository owner

* **CI does not start on its own** — pushes and pull requests have not
  triggered the workflow; runs were started by hand (`gh workflow run CI`).
  Check the repository's *Settings › Actions* (workflow permissions, whether
  Actions are allowed to run on pull requests).
* **Node 20 deprecation** — GitHub warns that `actions/checkout@v4`,
  `actions/setup-python@v5` and `actions/upload-artifact@v4` run on Node 20;
  bump them to their current major versions.
