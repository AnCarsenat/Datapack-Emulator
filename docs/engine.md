# The version test engine

`TestEngine` runs one pack against a list of versions and keeps one
`VersionRun` per version.

```python
from src.emulator import Datapack, TestEngine
from src.emulator.vanilla import default_library

pack = Datapack.load("samples/hat")
engine = TestEngine(pack, ticks=20, players=1, seed=0, library=default_library())

chosen = TestEngine.format_boundaries(pack.declared_versions())
for run in engine.run(chosen):
    print(run.version.id, run.status, sorted(run.unknown_commands))
```

## What each run does

1. Build the `PackView` for the version (base + matching overlays).
2. Load that version's client jar if a library is given and one is available.
3. **Static checks**, no ticks needed:
   * does `pack.mcmeta` declare support for this format → `unsupported`
   * registry folders spelled the way this version does not read
     (`functions/` on 1.21+, `function/` before)
   * overlays declared for a version that predates them
   * per function, every feature it uses that the version lacks
     (unknown commands, `execute on` before 1.19.4, macros before 1.20.2, …)
4. Run a fresh `Emulator` for the requested ticks, with its own world and bus.
5. Build the call graph: missing functions, unreachable functions, recursion.

## `VersionRun`

| field | |
| --- | --- |
| `version`, `supported`, `overlays`, `vanilla` | context |
| `ticks`, `commands`, `total_us`, `worst_tick_us` | cost |
| `records` | every log record of that run |
| `unknown_commands`, `missing_features` | from the static checks |
| `missing_functions`, `unreachable`, `cycles` | from the call graph |
| `profiler`, `graph` | the full objects |
| `status` | `unsupported` › `errors` › `warnings` › `ok` |
| `warnings`, `errors`, `chat`, `count(source, level)` | summaries |

## Choosing versions

| helper | gives |
| --- | --- |
| `pack.declared_versions()` | what `pack.mcmeta` claims |
| `TestEngine.version_range(a, b)` | inclusive range |
| `TestEngine.format_boundaries(list)` | first release of each pack format |

`engine.run()` with no list uses the declared versions, or the release
matching `pack_format` when nothing is declared.

## Reports

`TestEngine.write_html(results, path, pack_name)` writes the matrix table.
The engine window's *export html* and `python -m src.emulator matrix` both use
it.

## Performance note

Runs are sequential and, in the window, on the UI thread: a large selection
with many ticks keeps the window busy until it finishes. *one per format*
covers 1.13 → 26.2 in 22 runs.
