# The version test engine

`TestEngine` runs one pack against a list of versions and keeps one
`VersionRun` per version.

```python
from datapack_emulator.emulator import Datapack, TestEngine
from datapack_emulator.emulator.testing import CommandTest
from datapack_emulator.emulator.vanilla import default_library

pack = Datapack.load("samples/hat")
engine = TestEngine(
    pack,
    ticks=20,
    players=1,
    seed=0,
    library=default_library(),
    tests=[CommandTest("execute as Player1 run trigger hat", at_tick=1)],
)

chosen = TestEngine.format_boundaries(pack.declared_versions())
for run in engine.run(chosen):
    print(run.version.id, run.status, run.tests_summary, sorted(run.unknown_commands))
```

## What each run does

1. Build the `PackView` for the version (base + matching overlays).
2. Load that version's client jar if a library is given and one is available.
3. **Static checks**, no ticks needed:
   * how the version reads `pack.mcmeta` (`Datapack.compatibility`): what the
     server logs about invalid metadata, and whether the pack list would call
     it incompatible → `unsupported`
   * registry folders spelled the way this version does not read
     (`functions/` on 1.21+, `function/` before)
   * overlays declared for a version that predates them
   (the last two are `info` for packs whose declared range spans the change)
4. Run a fresh `Emulator` for the requested ticks, with its own world and bus.
   When the engine was given `tests` (a list of `CommandTest`), each enabled
   test runs in its tick, after that tick's functions, exactly as in the
   environment tab; tests the run does not reach fail as "not reached".
   Its `FunctionLibrary` drops every function with a line the version cannot
   parse (unknown commands, `execute on` before 1.19.4, macros before 1.20.2,
   …) and every tag missing a required entry, and logs each as a `game`
   warning; `unknown_commands` / `failed_functions` summarise them.
5. Build the call graph: missing functions, unreachable functions, recursion.

## `VersionRun`

| field | |
| --- | --- |
| `version`, `supported`, `overlays`, `vanilla` | context |
| `ticks`, `commands`, `total_us`, `worst_tick_us` | cost |
| `records` | every log record of that run |
| `unknown_commands`, `missing_features` | what the functions that failed to load needed |
| `failed_functions`, `failed_tags` | what the version's server refused to load |
| `missing_functions`, `unreachable`, `cycles` | from the call graph |
| `profiler`, `graph` | the full objects |
| `tests`, `tests_passed`, `tests_summary` | the command tests' results, and `"3/4"` (or `-` without tests) |
| `cancelled`, `planned_ticks`, `ticks_summary` | the run was cancelled (`ticks` is how many ran, `ticks_summary` reads `7/20`) |
| `tests_failed`, `tests_skipped` | tests that failed, and tests a cancel stopped (`skipped`, "skipped: the run was cancelled after N tick(s)"); `tests_summary` counts only the ones that ran |
| `status` | `errors` › `tests failed` › `cancelled` › `warnings` › `unsupported` › `ok` — `unsupported` only means the metadata does not claim that version; the pack still loads |
| `warnings`, `errors`, `chat`, `count(source, level)` | summaries |

## Choosing versions

| helper | gives |
| --- | --- |
| `pack.declared_versions()` | what `pack.mcmeta` claims |
| `TestEngine.version_range(a, b)` | inclusive range |
| `TestEngine.format_boundaries(list)` | first release of each pack format |

`engine.run(versions, progress=…, output=…, cancelled=…)` runs them in order.
`cancelled` is asked before each version and between ticks; once it answers
`True`, the current run stops (it is kept, marked `cancelled`) and the rest
are skipped; a cancel that comes between two versions marks the last run
that finished. `run_version` takes the same callback. The engine window runs
the engine on a worker thread and its *cancel* button sets it; the command
line's first Ctrl+C does.

`engine.run()` with no list uses the declared versions, or the newest release
at or below `pack_format` (`versions.closest_to_pack_format`) when nothing is
declared.

From a terminal: `datapack-emulator-cli matrix` (add `--tests` for a
project's tests) and `datapack-emulator-cli test` — see [cli.md](cli.md).

## Reports

`TestEngine.write_html(results, path, pack_name, not_run=0)` writes the
matrix table (with the ticks each run reached, and a note when `not_run`
versions never ran).
The engine window's *export html* and `datapack-emulator-cli matrix` both use
it.

## Performance note

Runs are sequential: a large selection with many ticks takes as long as all
its versions together. The window runs them on a worker thread, so it stays
usable and can cancel. *one per format* covers 1.13 → 26.3 in 23 runs.
Emulators on several threads share the process: the recursion limit is only
ever raised, and each client jar is read once (`VanillaLibrary.load` locks
per version).
