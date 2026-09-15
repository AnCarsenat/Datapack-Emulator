# Plans

Improvements that are not built yet, roughly in the order they are worth doing.
Each entry says what it would bring, what it touches, and how it could be done.
Nothing here is promised; cross an entry out (or delete it) once it lands.

## Recommended next

### 1. Run a project's tests from the command line

`datapack-emulator-cli test hat.dpemu [--versions … | --declared] [--ticks N]`
runs the tests saved in a project against one or more versions and exits
non-zero when any test fails, so a datapack's own repository can check every
commit in CI.

* **Touches:** `emulator/__main__.py` (new `test` subcommand), `project.py`
  (load the archive headless), `emulator/testing.py`, `emulator/engine.py`
  (already runs tests per version), `docs/cli.md`.
* **How:** load the project, build a `DatapackSet` from its packs, run
  `TestEngine(…, tests=…)` over the chosen versions, print one line per test
  and version, write a JUnit XML report (`--junit path`) for CI dashboards.
* **Size:** small.

### 2. Blocks

A sparse block model so `setblock`, `fill`, `clone`, `execute if block|blocks`,
`data … block`, containers (`item … block`, `loot insert`, `loot … mine`) and
macros `with block` work. It is the largest gap left: `hat_v2`'s 1.16 path and
many real packs move items through blocks.

* **Touches:** a new `runtime/blocks.py` (a dict of `(dimension, x, y, z)` →
  block state and block entity NBT), `commands/handlers.py` and
  `commands/items.py`, `analysis/explain.py` (drop the "not modelled" notes),
  the world dock (a blocks tab), `docs/emulation.md`.
* **How:** only positions a command touches are stored; everything else is
  air. Block states parsed from `stone[facing=up]{…}`; containers reuse
  `Inventory` with `container.N` slots; `fill`/`clone` capped by vanilla's
  32 768 block limit; block tags from the client jar. Loot `mine` needs block
  loot tables, which the jar has.
* **Size:** large.

### 3. Function debugger

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

### 4. Run the engine window in the background

A large version matrix runs on the UI thread and freezes the window.

* **Touches:** `window/engine_window.py`, `ui` progress and a cancel button.
* **How:** a `QThread` worker like the client-jar download; results come back
  version by version through signals; the output bus of each run stays
  per-worker.
* **Size:** small to medium.

## Emulation gaps

* **Predicates and advancements** — `execute if predicate`, the `predicate=`
  and `advancements=` selector arguments, and the `advancement` command:
  evaluate predicate JSON from the pack (and the jar) for the conditions the
  world model can answer (scores, entity properties, NBT, random chance), and
  keep per-player advancement progress.
* **Loot conditions and functions** — evaluate loot table conditions (random
  chance, entity/score checks) and the common functions beyond `set_count`,
  `set_components`, `set_nbt` (`set_name`, `set_lore`, `enchant_randomly`,
  `copy_components`, `set_damage`, …); share them with item modifiers.
* **Teams, game modes and levels** — the `team`, `gamemode` and `experience`
  commands, and the `team=`, `gamemode=` and `level=` selector arguments,
  which currently match everything.
* **Effects, attributes and bossbars** — store them on entities (and in NBT:
  `active_effects`, `attributes`) so `effect`, `attribute … get` and `bossbar`
  queries return real values.
* **Commands that return values** — `time query`, `random value|roll` (seeded
  like the rest of the world), `worldborder get`, `difficulty`, so
  `execute store result` sees what the game would give.
* **Entity relations** — `execute on vehicle|passengers|owner|leasher|…` and
  `ride`: keep vehicle/passenger links and owners on entities.
* **Per-type entity defaults** — `Health`, `Attributes`, `CanPickUpLoot` and
  friends by entity type (from the jar's data where possible), so
  `data get entity` on a zombie looks like the game's.
* **Entity behaviour over time** — item entities merging, despawning after
  6000 ticks and being picked up by players; projectiles and falling blocks
  are probably out of scope.
* **Macros `with block`** — once blocks exist.
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

* **Split `commands/handlers.py`** — about 1 500 lines; split by command
  group (chat, scoreboard, entities, data, execute, control flow) like
  `commands/items.py`.
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
