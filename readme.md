# Datapack Emulator

Loads a Minecraft datapack, runs its functions, estimates what each one costs,
draws the call graph — and does all of that **for any Minecraft version you
pick**, so you can see what your pack does on 1.16 and on 26.2 side by side.

UI: PySide6, laid out entirely in `src/window/*.ui` (edit those in Qt Designer).
Graphs: pyqtgraph. No NetworkX — the DAG is in `src/emulator/analysis/graph.py`.

```sh
# the window, from the project root:
python ./src/main.py

# headless: one version
python -m src.emulator run samples/hat --version 1.21.4 --ticks 20 --dot

# headless: a version matrix
python -m src.emulator matrix samples/hat --from 1.20.4 --to 26.2 --boundaries
python -m src.emulator versions
```

## layout

| path | holds |
| --- | --- |
| `src/emulator/versions.py` | version ordering, ranges, pack-format lookup, feature queries |
| `src/emulator/version_data.py` | **generated** table of releases + per-version command availability |
| `src/emulator/common.py` | tokenising, SNBT, NBT paths, text components, ranges |
| `src/emulator/costs.py` | the cost model (tune it here) |
| `src/emulator/resources.py` | `Resource`, `Function`, `Tag`, `Recipe`, … |
| `src/emulator/namespace.py` | `Namespace`, `DirectoryNode`, singular/plural registry folders |
| `src/emulator/datapack.py` | `Datapack`, `pack.mcmeta`, `pack.png`, overlays, `PackView` |
| `src/emulator/commands/parser.py` | `Command`, `Selector`, `execute` chains, macros |
| `src/emulator/commands/handlers.py` | what each command actually does |
| `src/emulator/commands/registry.py` | which commands exist in which version |
| `src/emulator/runtime/world.py` | entities, scoreboard, storage, selector resolution |
| `src/emulator/runtime/output.py` | the log bus: app / emulator / game records |
| `src/emulator/runtime/messages.py` | vanilla's own error strings, with their translation keys |
| `src/emulator/runtime/emulator.py` | load/tick loop, dispatch, macros, schedules |
| `src/emulator/analysis/` | profiler + HTML report, call graph + DOT |
| `src/emulator/engine.py` | `TestEngine`: run the pack across versions, compare |
| `src/window/window.ui`, `src/window/engine.ui` | **every** widget, dock, menu and action |
| `src/window/main.py`, `src/window/engine_window.py` | wiring only: load the .ui, find widgets by name |
| `src/window/panels/` | graph canvas, explorer model, inspector rows, log model, highlighters |
| `tools/generate_version_data.py` | rebuilds `version_data.py` from mcmeta |
| `generated/` | profiler report, matrix report, exported .dot files |

## versions

Everything version-dependent goes through `src/emulator/versions.py`. The data
behind it is generated from [misode/mcmeta](https://github.com/misode/mcmeta) —
the real command tree of every release from 1.14 to 26.2 — so "does this
version have `/return run`" is answered from Mojang's own Brigadier export, not
from memory:

```sh
python tools/generate_version_data.py      # run from tools/'s working copy of mcmeta data
```

What the version decides:

* **which commands exist** — a command the version lacks gets vanilla's
  `Unknown or incomplete command. See below for error` with the `<--[HERE]`
  marker, and the emulator notes which release added it
* **sub-features** — `execute on`, `if items`, `return run`, `schedule clear`,
  `store storage`, `function … with` (macros) all carry their own since-version
* **folder names** — `functions/` before 1.21, `function/` from 1.21; files in
  the wrong spelling are reported as ignored
* **overlays** — read only from 1.20.2 on
* **which overlay applies** — by pack format, so the same pack can run a
  different body per version

## overlays

`pack.mcmeta`'s `overlays.entries` are loaded as separate layers.
`Datapack.view_for(version)` merges the base pack with the overlays whose
format range covers that version (later layers win) and hands back a
`PackView`; the emulator, the explorer and the call graph all read that view,
so switching the version in the toolbar switches the code that runs.

## output

Three separate sources, filterable in the logs dock and in the engine window:

* **app** — what this program does: loaded a pack, wrote a report, ran a matrix
* **emulator** — what the engine notices: unimplemented command, macro without
  an argument, tick over budget, a command that does not exist in this version
* **game** — what Minecraft itself would print: `say`/`tellraw`/`title` chat,
  command feedback, and the red errors (`No entity was found`,
  `Unknown scoreboard objective 'x'`, `Target does not have this tag`, …).
  Those strings are the real ones from `assets/minecraft/lang/en_us.json`, kept
  with their translation keys in `src/emulator/runtime/messages.py`.

The **Game chat** tab shows only the game side, as a player would have seen it.

## the emulator

`Emulator.run(ticks)` runs `#minecraft:load` once and `#minecraft:tick` every
tick. Emulated with real state: scoreboards, tags, entities and selectors,
`execute` (as / at / positioned / rotated / in / if / unless / store / run),
`function` including `$` macros and `with storage|entity`, `schedule`,
`return`, `data` on entities and storage, `summon`, `kill`, `teleport`,
`trigger`. Everything else vanilla knows is dispatched and costed but changes
no state. Recursion is capped by `MAX_DEPTH` and `maxCommandChainLength`.

## about the timings

Mojang publishes no per-command execution times, and the community benchmark
packs only produce relative numbers tied to one machine. So
`src/emulator/costs.py` is a **model**, not measurements: base cost per command,
plus per-`execute`-subcommand, per-condition and per-selector costs (an `@e`
scan scales with the entity count, `type=` discounts it, `nbt=` is charged as an
NBT read). The magnitudes follow the qualitative guidance in
[Tutorial:Optimizing a data pack](https://minecraft.wiki/w/Tutorial:Optimizing_a_data_pack)
and [datapack.wiki](https://datapack.wiki/guide/performance/how-to-measure).

## the DAG

`CallGraph.from_pack(view)` walks every parsed command and records `function`,
`schedule`, `execute if function` and tag edges. It gives you `cycles()`
(recursion — the graph stops being a DAG), `topological_order()`, `depths()`,
`unreachable()` (dead functions), `missing()` (calls to functions that do not
exist), `layout()` (layered positions with a barycentre pass) and `to_dot()`.
