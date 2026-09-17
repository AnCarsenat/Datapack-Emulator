# Plans

Improvements that are not built yet, roughly in the order they are worth doing.
Each entry says what it would bring, what it touches, and how it could be done.
Nothing here is promised; cross an entry out (or delete it) once it lands.

## Recommended next

Nothing is queued here right now; take the next item from the lists below.

## Reported

* **Open the last project on launch** (bug / feature) — the window starts on
  the first sample pack even when a project was open last time. Reopen the
  most recent project (the first entry of *open recent project*, as
  *open last project* does) when it still exists, and fall back to the
  sample only when there is none; a command-line flag or setting could turn
  it off.

## Emulation gaps

* **Entity data from the game** — maximum health and base attributes by
  type are a table in the code; the game has them only in code, so a
  generated table per version (like `version_data.py`) would be the next
  step. Regeneration, hunger and effects' side effects stay out of scope.
* **More of the game's context** — killers, damage sources, enchantment
  levels and biomes, so `killed_by_player`, `damage_source_properties`,
  `enchantment_active_check`, bonus-based loot and biome locations can be
  answered; advancement triggers beyond `tick` and `location`.
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

* **Schema checks** — the problems list checks JSON resources by hand
  (conditions, item functions, loot entries, advancement criteria); validating
  recipes, loot tables, predicates, item modifiers and worldgen against the
  version's schemas (Spyglass mcdoc) would catch every field.
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

* **Type-check the window** — `mypy` covers the Qt-free code; the window
  (`src/datapack_emulator/window`) needs PySide6's stubs and a pass of its own.
* **Coverage** — CI reports it (about 82% of the Qt-free code); the
  command handlers with the least coverage are the next tests to write.

## Needs a repository owner

* **CI does not start on its own** — pushes and pull requests have not
  triggered the workflow; runs were started by hand (`gh workflow run CI`).
  Check the repository's *Settings › Actions* (workflow permissions, whether
  Actions are allowed to run on pull requests).
* **Let the version-data workflow open pull requests** — *Settings › Actions
  › General › Workflow permissions* needs "Allow GitHub Actions to create and
  approve pull requests", or the weekly refresh can only push its branch. A
  pull request opened with the default token starts no CI: add a
  `VERSION_DATA_TOKEN` secret (a fine-grained token or a GitHub App token
  with contents and pull-request write access) so it does, or close and
  reopen it. The schedule only runs once the workflow is on `main`, and
  GitHub pauses schedules after 60 days without activity.
