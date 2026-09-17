# Command line

```sh
datapack-emulator-cli [--quiet] COMMAND ...
src/main.sh --cli     [--quiet] COMMAND ...   # from a checkout
# same as: python -m datapack_emulator.cli ...  (or python -m datapack_emulator.emulator ...)
```

The command line does what the window does, so a pack can be checked from a
terminal or in CI. `--quiet` goes *before* the command and silences the Python
logger (records from the emulator still print).

| command | does | window equivalent |
| --- | --- | --- |
| [`run`](#run--one-version) | emulate one version, profile it (and run the tests during the run) | run all (F5), run emulator (F6), profiler tab, *run tests during runs*, speed |
| [`matrix`](#matrix--many-versions) | run across versions | engine window |
| [`test`](#test--a-projects-tests) | run a project's tests, exit 1 on failure | run tests (F8), engine *run tests* |
| [`world`](#world--the-world-after-a-run) | run, then print scores, entities, storage, a score's history | world dock, *graph over time* |
| [`shell`](#shell--an-open-world) | type commands in an open world; step, run, tests, views | logs dock command line, step (F7), environment tab |
| [`info`](#info--the-inspector) | what a pack or a resource is | inspector dock, version note |
| [`explain`](#explain--analyze-a-line) | what a command line does | analyze line (Ctrl+I) |
| [`search`](#search) | text in functions, or ids | search in pack (Ctrl+Shift+F), quick open (Ctrl+P) |
| [`graph`](#graph--the-call-graph) | the call graph, callers and calls | call graph tab, *show callers and calls*, export .dot |
| [`project`](#project--dpemu-files) | create, show and edit projects | *file › new / save project*, environment tab, notes |
| [`versions`](#versions) | list known versions | the version combo |
| [`vanilla`](#vanilla--client-jars) | list, download, inspect client jars | *file › load / download client jar* |

Only what needs a screen has no command: opening files in an editor or file
manager, the graph's drawing, and the window layout.

Exit status: `0` success, `1` something failed (a test, or `matrix --strict`),
`2` a usage problem (missing pack, unknown version, unreadable jar, report
that cannot be written, nothing to test), `3` an internal error (a bug —
please report it with the traceback).

Reports (`--html`) default to `generated/` in the working directory.

## Packs or a project

Every command that reads a pack takes either **datapack folders** (analyzed
together in the order given, like a world's datapack list) or **one project**
(`.dpemu`, or a legacy `.json`, see [projects](projects.md)). A project brings
its packs and its settings: the version, ticks, players, seed, the client jar it
was saved with, the versions ticked in the engine window and its tests.
Options given on the command line win over the project's.

Without a version, a pack runs in the newest release it declares whose server
reads its `pack.mcmeta` cleanly — the same default the window picks.

## Client jars

As in the window, the client jar of the emulated version is used when one is
installed (in the jar cache or a launcher's folder, see
[vanilla-assets.md](vanilla-assets.md)); otherwise a project's saved jar. The
multi-version commands use each version's installed jar, like the engine
window.

| option | |
| --- | --- |
| `--vanilla VERSION\|JAR` | (`run`, `test`) use that jar instead |
| `--no-vanilla` | use no jar at all |
| `--download` | fetch the jar from Mojang when it is not installed |

## `run` — one version

```sh
datapack-emulator-cli run samples/hat --version 1.21.4 --ticks 20
datapack-emulator-cli run projects/hat.dpemu --level debug
```

| option | default | |
| --- | --- | --- |
| `source` | | datapack folders, or one project |
| `--version` | project's, else the pack's newest | release id (`1.21` means 1.21 itself); a prefix that is not a release, like `26`, means its newest release |
| `--ticks` | 20 (project's) | a project's endless `-1` runs 20 |
| `--players` | 1 (project's) | fake players `Player1…` |
| `--seed` | 0 (project's) | for `@r`, `sort=random` and loot |
| `--tests` / `--no-tests` | project's *run tests during runs* | run the project's enabled tests, each in its tick, and print their results; exit `1` when one fails |
| `--show-records` | off | with tests: print the records of failed tests |
| `--realtime` / `--no-realtime` | project's speed | 20 ticks per second instead of as fast as possible |
| `--level` | info | `debug`, `info`, `warn`, `error` — lowest level printed |
| `--sources` | all | any of `app emulator game` |
| `--vanilla`, `--no-vanilla`, `--download` | installed jar | see [client jars](#client-jars) |
| `--html` | `generated/index.html` | profiler report |
| `--dot` | off | also write the call graph as Graphviz, next to the report |

Prints every record as `[tick] source/level function:line: message`, then a
per-function table (calls, commands, self and total ms for the run, and ms per
tick), the report path and call-graph findings (recursion, missing functions,
functions nothing calls).

## `matrix` — many versions

```sh
datapack-emulator-cli matrix samples/hat --from 1.20.4 --to 26.2 --boundaries
datapack-emulator-cli matrix samples/hat --declared --vanilla
datapack-emulator-cli matrix projects/hat.dpemu --tests --strict
```

Version selection (first match wins): `--versions A B C`, `--from/--to`,
`--declared` (the range `pack.mcmeta` claims), `--all`, otherwise the versions
a project ticked in the engine window, otherwise every known version.
`--boundaries` then keeps only the first release of each pack format.

| option | |
| --- | --- |
| `--ticks`, `--players`, `--seed` | as for `run` |
| `--tests` | also run the project's enabled tests in every version, each in its tick (a note says when some come after the last tick) |
| `--junit PATH` | a JUnit XML report of the tests; implies `--tests` |
| `--strict` | exit with `1` when a version has errors or failed tests |
| `--no-vanilla`, `--download` | see [client jars](#client-jars) (`--vanilla` is accepted and is the default) |
| `--html` | matrix report, default `generated/matrix.html` |

Output is one row per version: format, status, commands, total ms, worst ms,
warnings, errors, tests passed, unknown commands, overlays. See
[engine.md](engine.md).

## `test` — a project's tests

```sh
datapack-emulator-cli test projects/hat.dpemu
datapack-emulator-cli test projects/hat.dpemu --declared --boundaries --junit generated/tests.xml
datapack-emulator-cli test samples/hat --test "function hat:tick" --test "5:scoreboard players get #global hat"
datapack-emulator-cli test samples/hat --test "say hi" --expect hi
```

Runs the tests saved in a project (the environment tab's, see
[the window](ui.md)) and those given with `--test`, in a fresh world per
version, and prints one line per test and version:

```
PASS 1.21.4     tick 0    function hat:tick  — passed (value 9)
FAIL 1.21.4     tick 3    scoreboard players get #x nope  — Unknown scoreboard objective 'nope'

1/2 passed in 1.21.4
```

Timing and pass rules are the window's *run tests*: the world ticks until the
last test has run, a test at tick N runs after that tick's functions, and it
passes when the command succeeds without a visible error and its expectations
hold.

| option | |
| --- | --- |
| `--version` | one version (default: the project's, else the pack's newest) |
| `--versions`, `--from/--to`, `--declared`, `--all`, `--boundaries` | several versions, as for `matrix` (not together with `--version`) |
| `--engine` | the versions the project ticked in the engine window (needs a project) |
| `--ticks` | default: one past the last test's tick; fewer ticks leave later tests *not reached* |
| `--players`, `--seed` | as for `run` |
| `--test [TICK:]COMMAND` | add a test (repeatable): `5:function hat:tick` runs in tick 5 |
| `--expect TEXT`, `--expect-value RANGE` | with exactly one `--test`: its expected output / value range (`5`, `1..`, `..3`, `1..4`) |
| `--only` | run only the `--test` tests (needs at least one) |
| `--include-disabled` | also run tests unticked in the project |
| `--junit PATH` | write a JUnit XML report: one test suite per version, the records of each test in its `system-out` |
| `--show-records` | print the records of failed tests |
| `--verbose` | print every record while the tests run (filtered by `--level`, `--sources`) |
| `--vanilla`, `--no-vanilla`, `--download` | see [client jars](#client-jars) |

Exit status `0` when every test passed, `1` when one failed, `2` when there
was nothing to run or an option was wrong (an `--expect-value` that is not a
range, `--only` without `--test`, …).

### In CI

A datapack's own repository can check every commit. With the project saved
next to the pack (GitHub Actions):

```yaml
- uses: actions/setup-python@v7
  with:
    python-version: "3.13"
- run: pip install "datapack-emulator @ git+https://github.com/AnCarsenat/Datapack-Emulator"
- run: datapack-emulator-cli --quiet test my-pack.dpemu --declared --boundaries --junit tests.xml
- uses: actions/upload-artifact@v7
  if: always()
  with:
    name: datapack-tests
    path: tests.xml
```

## `world` — the world after a run

```sh
datapack-emulator-cli world samples/hat --ticks 5
datapack-emulator-cli world projects/hat.dpemu -c "execute as Player1 run trigger hat" --scores
datapack-emulator-cli world samples/hat --entities --nbt --holder armor_stand
datapack-emulator-cli world samples/hat --history Player1 hat
datapack-emulator-cli world samples/hat --json > world.json
```

Runs the pack for `--ticks` ticks (a fresh world, like run all), then each
`--command` as a typed command, and prints what the world dock shows:

* the **scoreboard grid** — one row per holder (players first), one column per
  objective with its criterion and display slot; `*` marks an enabled
  trigger, `(enabled)` one with no score yet;
* the **entities** — name, type, position, tags, items carried, UUID and the
  tick it was summoned; `--nbt` adds the full NBT, as `data get entity` shows
  it in that version;
* **command storage**, as SNBT.

| option | |
| --- | --- |
| `--version`, `--ticks`, `--players`, `--seed` | as for `run` |
| `-c`, `--command COMMAND` | run after the ticks (repeatable) |
| `--scores`, `--entities`, `--storage` | print only these (default: all three) |
| `--nbt` | entities with their NBT |
| `--holder TEXT`, `--objective TEXT` | filters, like the dock's |
| `--history HOLDER OBJECTIVE` | every value the score took and at which game time (the *graph over time* data) |
| `--json` | everything as JSON: objectives, scores, enabled triggers, entities with NBT, storage, gamerules |
| `--level`, `--sources` | records printed while it runs (default `warn`) |
| `--vanilla`, `--no-vanilla`, `--download` | see [client jars](#client-jars) |

## `shell` — an open world

```sh
datapack-emulator-cli shell projects/hat.dpemu
datapack-emulator-cli shell samples/hat --script session.txt --strict
printf 'function hat:tick\n.scores\n' | datapack-emulator-cli shell samples/hat
```

Keeps one world open and runs what you type as the server console would, like
the logs dock's command line: `execute as Player1 run trigger hat` acts as a
player, a leading `/` is optional, and a world that has not ticked yet runs its
first tick first. Lines starting with a dot control the session:

| line | does |
| --- | --- |
| `.step [N]` | N more ticks (step, F7) |
| `.run [N]` | a fresh world for N ticks, default `--ticks` (run all, F5) |
| `.reset` | a fresh world |
| `.tick` | the game time |
| `.scores [FILTER]`, `.entities [FILTER]`, `.nbt [FILTER]`, `.storage [FILTER]`, `.world`, `.json` | the world, as `world` prints it |
| `.history HOLDER OBJECTIVE` | a score over time |
| `.explain COMMAND` | analyze a line |
| `.profile` | the profiler tab's per-tick call tree |
| `.version [V]`, `.players [N]`, `.seed [N]` | show or change them (a fresh world) |
| `.ticks [N]` | the ticks of `.run` |
| `.realtime on\|off` | 20 ticks per second |
| `.step-on-command on\|off` | one more tick after every command |
| `.during-runs on\|off` | tests run in their tick during `.step` and `.run` |
| `.tests`, `.test [TICK:]COMMAND` | list, add tests |
| `.expect N TEXT`, `.expect-value N RANGE` | test N's expectations |
| `.enable N`, `.disable N`, `.remove N`, `.move N up\|down` | edit tests |
| `.runtests [N …]` | the enabled tests, or those, in a fresh world (F8) |
| `.save [PATH]` | save the project with the session's settings and tests |
| `.help`, `.quit` | (Ctrl+D quits too) |

Input comes from the keyboard (with line editing and history where Python has
`readline`), from `--script` files, or from standard input. Mistakes print
`error: …` and the session carries on; with `--strict` the exit status is `1`
when a dot-command failed or a test run had a failure.

| option | |
| --- | --- |
| `--version`, `--ticks`, `--players`, `--seed` | as for `run` |
| `--run` | run all before reading commands |
| `--script FILE` | read lines from FILE (repeatable) |
| `--step-on-command`, `--realtime` | start with those on (a project's settings count too) |
| `--strict` | see above |
| `--level`, `--sources`, `--vanilla`, `--no-vanilla`, `--download` | as for `run` |

## `info` — the inspector

```sh
datapack-emulator-cli info samples/hat_v2
datapack-emulator-cli info samples/hat_v2 --version 1.20.4 -r hat:tick -r "#minecraft:tick"
```

Without `--resource`: what the version makes of the pack (compatible, folder
spelling, overlays), its formats, overlays, namespaces, function counts, icon,
the version's features and — for a project — its tests and notes. With
`-r ID` (repeatable): a function's commands, macro use, estimated cost, calls,
callers, the features the version lacks, and your note; tags and other
resources by id. `--json` prints the rows as JSON.

## `explain` — analyze a line

```sh
datapack-emulator-cli explain samples/hat -l "execute as @a[tag=x] run function hat:tick"
datapack-emulator-cli explain samples/hat --at hat:tick:3
datapack-emulator-cli explain samples/hat --at hat:tick      # every command of the function
```

The same analysis as *analyze line*: each `execute` step, selectors,
references (checked against the pack), ids (against the client jar), SNBT and
text components, macro arguments, how much the emulator runs, whether the
function loads in the version, and the cost. `-l` and `--at` repeat; `--json`
prints the rows as JSON.

## `search`

```sh
datapack-emulator-cli search samples/hat trigger            # function lines containing it
datapack-emulator-cli search samples/hat tick --ids         # functions and tags by id
```

Searches the emulated version's view (base pack and active overlays),
case-insensitively. `--paths` adds file paths, `--json` prints JSON; the exit
status is `1` when nothing matched.

## `graph` — the call graph

```sh
datapack-emulator-cli graph samples/hat_v2
datapack-emulator-cli graph samples/hat_v2 -f hat:tick --dot generated/hat.dot
```

Every node in call order, indented by depth, with its kind (function, tag,
missing, macro, overlay) and its outgoing edges (`call`, `macro`, `schedule`,
`condition`, `tag`), then recursion, missing functions and functions nothing
calls. `-f ID` (repeatable) shows callers and calls of a function, with your
note. `--dot PATH` writes Graphviz; `--json` prints nodes, edges, cycles and
the topological order.

## `project` — .dpemu files

```sh
datapack-emulator-cli project new projects/hat.dpemu samples/hat --version 1.21.4 --test "5:function hat:tick"
datapack-emulator-cli project show projects/hat.dpemu
datapack-emulator-cli project set projects/hat.dpemu --ticks 40 --tests-during-runs on
datapack-emulator-cli project packs projects/hat.dpemu --add ../extras --move 2 up
datapack-emulator-cli project tests projects/hat.dpemu --add "say hi" --expect 2 hi --disable 1
datapack-emulator-cli project note projects/hat.dpemu hat:tick "swaps the hat"
datapack-emulator-cli project list
```

| action | |
| --- | --- |
| `new FILE PACK…` | a project from datapack folders (`--force` replaces a file); takes the settings below and `--test` |
| `show FILE` | everything it holds; `--json` for `project.json` |
| `set FILE` | settings: `--name`, `--version` (`''` = the pack's), `--ticks` (`-1` = until stopped), `--players`, `--seed`, `--speed fast\|realtime`, `--engine-versions V…`, `--vanilla-jar JAR` (`''` = none), `--tests-during-runs on\|off`, `--step-on-command on\|off`, `--notes TEXT` / `--notes-file FILE` |
| `packs FILE` | list; `--add DIR` (repeatable), `--remove N`, `--move N up\|down` (load order) |
| `tests FILE` | list; `--add [TICK:]COMMAND`, `--expect N TEXT`, `--expect-value N RANGE`, `--tick N TICK`, `--enable N`, `--disable N`, `--duplicate N`, `--move N up\|down`, `--remove N` (all repeatable except move and duplicate; numbers are as listed before the change) |
| `note FILE FUNCTION [TEXT]` | read, write or (with `''`) remove the note on a function or `#tag` |
| `list` | the projects in `projects/` |

Every change is written back to the file, datapacks included, as *save* does.

## `versions`

Lists every known release with its pack format and data version.

## `vanilla` — client jars

```sh
datapack-emulator-cli vanilla                    # list jars found on this machine
datapack-emulator-cli vanilla --download 1.21.4  # fetch into the jar cache
datapack-emulator-cli vanilla --inspect 26.2     # what the jar contains
datapack-emulator-cli vanilla --inspect path/to/client.jar
```

## Regenerating version data

```sh
python tools/generate_version_data.py [--work-dir .mcmeta-cache]
```

See [versions.md](versions.md#regenerating).
