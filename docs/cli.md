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
| [`world`](#world--the-world-after-a-run) | run, then print scores, entities, storage, blocks, a score's history | world dock, *graph over time* |
| [`shell`](#shell--an-open-world) | type commands in an open world; step, run, tests, views, the debugger | logs dock command line, step (F7), environment tab, debugger dock |
| [`info`](#info--the-inspector) | what a pack or a resource is | inspector dock, version note |
| [`explain`](#explain--analyze-a-line) | what a command line does | analyze line (Ctrl+I) |
| [`search`](#search) | text in functions, or ids | search in pack (Ctrl+Shift+F), quick open (Ctrl+P) |
| [`graph`](#graph--the-call-graph) | the call graph, callers and calls | call graph tab, *show callers and calls*, export .dot |
| [`project`](#project--dpemu-files) | create, show and edit projects | *file › new / save project*, environment tab, notes |
| [`versions`](#versions) | list known versions | the version combo |
| [`vanilla`](#vanilla--client-jars) | list, download, inspect client jars | *file › load / download client jar* |

Only what needs a screen has no command: opening files in an editor or file
manager, the graph's drawing, and the window layout.

## Records

Commands that print records (`run`, `test --verbose`, `matrix --verbose`,
`world`, `shell`) take the logs dock's filters:

| option | |
| --- | --- |
| `--level` | `debug`, `info`, `warn`, `error` — lowest level printed |
| `--sources` | any of `app emulator game` |
| `--seen-by PLAYER` | only the chat that player reads (*seen by*) |
| `--grep TEXT` | only records whose message contains TEXT |
| `--details` | also each record's command, translation key, version and reader (*copy with details*) |

Exit status: `0` success, `1` something failed (a test, or `matrix --strict`),
`2` a usage problem (missing pack, unknown version, unreadable jar, report
that cannot be written, nothing to test), `3` an internal error (a bug —
please report it with the traceback).

Reports (`--html`) default to `generated/` in the working directory.

## Packs or a project

Every command that reads a pack takes either **datapack folders** (analyzed
together in the order given, like a world's datapack list) or **one project**
(`.dpemu`, or a legacy `.json`, see [projects](projects.md)); `@last` is the
project the window opened last (*open last project*). A project brings
its packs and its settings: the version, ticks, players, seed, the client jar it
was saved with, the versions ticked in the engine window and its tests.
Options given on the command line win over the project's. A project that runs
*until stopped* (ticks `-1`) runs 20 ticks, with a note.

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
| `--ticks` | 20 (project's) | `-1` runs until Ctrl+C, then prints the report as *stop* does |
| `--players` | 1 (project's) | fake players `Player1…` |
| `--seed` | 0 (project's) | for `@r`, `sort=random` and loot |
| `--tests` / `--no-tests` | project's *run tests during runs* | run the project's enabled tests, each in its tick, and print their results; exit `1` when one fails |
| `--show-records [failed\|all]` | off | with tests: print the records of failed (default) or all tests |
| `--realtime` / `--no-realtime` | project's speed | 20 ticks per second instead of as fast as possible |
| `--level`, `--sources`, `--seen-by`, `--grep`, `--details` | info, all | see [records](#records) |
| `--vanilla`, `--no-vanilla`, `--download` | installed jar | see [client jars](#client-jars) |
| `--html` | `generated/index.html` | profiler report |
| `--dot` | off | also write the call graph as Graphviz, next to the report |
| `--break FUNC:LINE[ if COND]`, `--watch EXPR` | none | print each [debugger](#the-debugger) stop with the watches and go on; the number of stops goes to standard error |

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
| `--verbose` | print every version's records as they come (the engine window's lower pane), filtered as in [records](#records) |

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
| `--show-records [failed\|all]` | print the records of failed (default) or all tests |
| `--verbose` | print every record while the tests run (filtered as in [records](#records)) |
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
* **command storage**, as SNBT;
* the **blocks** commands placed: position (and dimension), state and block
  entity data.

| option | |
| --- | --- |
| `--version`, `--ticks`, `--players`, `--seed` | as for `run` |
| `-c`, `--command COMMAND` | run after the ticks (repeatable) |
| `--scores`, `--entities`, `--storage`, `--blocks`, `--state` | print only these (default: all); `--state` is the time, weather, difficulty, world border, spawn, force-loaded chunks and teams |
| `--nbt` | entities with their NBT |
| `--holder TEXT`, `--objective TEXT` | filters, like the dock's |
| `--history HOLDER OBJECTIVE` | every value the score took and at which game time (the *graph over time* data) |
| `--json` | JSON on standard output — objectives, scores and enabled triggers, entities with NBT, storage, blocks, server state (only the parts and filters asked for), gamerules; with `--history`, the score's changes. Everything else (commands, records) goes to standard error |
| `--level`, `--sources`, … | records printed while it runs (default `warn`), see [records](#records) |
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
| `.step [N]` | N more ticks (step, F7); `-1` until Ctrl+C |
| `.run [N]` | a fresh world for N ticks, default `--ticks` (run all, F5); tests during runs that it does not reach are reported |
| `.reset` | a fresh world |
| `.tick` | the game time |
| `.scores [FILTER]`, `.entities [FILTER]`, `.nbt [FILTER]`, `.storage [FILTER]`, `.blocks [FILTER]`, `.state`, `.world`, `.json` | the world, as `world` prints it |
| `.history HOLDER OBJECTIVE` | a score over time |
| `.explain COMMAND` | analyze a line |
| `.profile` | the profiler tab's per-tick call tree |
| `.report [FILE]` | write the HTML profiler report (*run profiler*; default `generated/index.html`) |
| `.dot [FILE]` | write the call graph (*export call graph*; default `generated/<pack>-<version>.dot`) |
| `.version [V]`, `.players [N]`, `.seed [N]` | show or change them (a fresh world) |
| `.ticks [N]` | the ticks of `.run` |
| `.realtime on\|off` | 20 ticks per second |
| `.step-on-command on\|off` | one more tick after every command |
| `.during-runs on\|off` | tests run in their tick during `.step`, `.run` and the first typed command |
| `.packs` | the datapacks, in load order, and the client jar |
| `.add-pack DIR`, `.remove-pack N`, `.move-pack N up\|down`, `.reload` | the explorer's pack menu and *reload datapacks* (each gives a fresh world) |
| `.jar [VERSION\|FILE\|none]` | *load client jar* (no value: the version's installed jar) |
| `.tests` | list the tests, with their last result |
| `.test [TICK:]COMMAND` | add a test |
| `.expect N TEXT`, `.expect-value N RANGE`, `.at N TICK` | test N's expectations and tick |
| `.enable N…`, `.disable N…`, `.remove N…`, `.duplicate N`, `.move N up\|down` | edit tests |
| `.runtests [N …]` | the enabled tests, or those, in a fresh world (F8) |
| `.records N` | the records test N produced in its last run (*show its records*) |
| `.break`, `.unbreak`, `.watch`, `.unwatch`, `.eval` | the [debugger](#the-debugger) |
| `.save [FILE]` | save the project with the tests, breakpoints, watches and the settings changed in the session (without a project, a FILE is needed) |
| `.help`, `.quit` | (Ctrl+D quits too; `.quit` also skips later `--script` files) |

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
| `--break FUNC:LINE[ if COND]`, `--watch EXPR` | more breakpoints and watches (repeatable), besides the project's |
| `--strict` | see above |
| `--level`, `--sources`, `--seen-by`, `--grep`, `--details` | see [records](#records) |
| `--vanilla`, `--no-vanilla`, `--download` | see [client jars](#client-jars) |

### The debugger

The window's debugger dock, in the shell. A breakpoint stops **before** a
function line runs; `FUNC:LINE` on a comment or blank line moves to the next
command. A condition is an `execute` condition checked in the stopped line's
context: `.break hat:tick:4 if score @s hat matches 3..` (`if` may be left
out). Conditions and `if`/`unless` watches only read the world: `if function`
is refused, and what a check prints is not recorded. A project's breakpoints
are checked when the shell opens: each moves to its function's first command
on or after its line, and the ones the version does not have are warned
about (`project debug` warns the same way).

| line | does |
| --- | --- |
| `.break FUNC:LINE [if\|unless COND]` | add a breakpoint (the function must exist in the version) |
| `.break` | list them, with how often each stopped |
| `.unbreak FUNC:LINE\|all` | remove |
| `.enable-break FUNC:LINE`, `.disable-break FUNC:LINE` | a disabled breakpoint never stops |
| `.watch EXPR` / `.watch` | add a watch / list the watches with their values |
| `.unwatch N\|all` | remove |
| `.eval EXPR` | an expression's value now (or where it stopped) |

Expressions: `score HOLDER OBJECTIVE` (a name or a selector such as `@s`),
`storage ID [PATH]`, `entity SELECTOR [PATH]`, `block X Y Z [PATH]`,
`if …` / `unless …` (whether the condition passes), `executor`, `position`,
`rotation`, `dimension`.

A stop (from `.step`, `.run`, `.runtests` or a typed command) prints where
it is and the watches, then reads answers from the same place as the rest
of the session — the keyboard or the script:

| answer | |
| --- | --- |
| `step` / `s` | run the line; stop at the next function line, inside calls too (into, F11) |
| `next` / `n` | stop at the next line of this function, or of its caller once it ends (over, F10) |
| `out` / `o` | stop at the next line of the caller (Shift+F11) |
| `continue` / `c` | run to the next breakpoint (Ctrl+F5) |
| `stop` / `q` | abandon the rest of the tick; the world keeps what already ran |
| `where` / `bt` | the call stack, innermost first |
| `context` / `ctx` | executor, position, rotation, dimension |
| `list` / `l [N]` | the source around the line (`●` breakpoints, `->` the line) |
| a command | runs as the stopped line would (same executor and position); its function calls do not stop. A command named like an answer (`list`, `stop`) needs a leading `/` |
| `.scores`, `.storage`, `.entities`, `.nbt`, `.blocks`, `.state`, `.world`, `.json`, `.history`, `.tick`, the debugger's own dot-commands, `.help` | as in the shell; the others wait until the tick goes on |

*next* and *out* follow the call stack's depth: past the end of a tick
function they stop in whatever runs next at that depth (the next function of
the tag, a schedule). A step ends with its tick (or its test). An abandoned
tick still counts: the game time moves on, so the next step runs the next
tick. When a script runs out while stopped, the run goes on and later stops
are skipped until the next `--script` starts (the breakpoints are kept);
Ctrl+D at the prompt just continues. With `--run`, the stops read from the
first script or standard input. `run --break` prints the stops instead of
asking.

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
resources by id — a tag needs its `#` (`-r "#minecraft:logs"`). `--json`
prints the rows as JSON.

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
case-insensitively; the text cannot be empty. `--paths` adds file paths,
`--json` prints JSON; the exit status is `1` when nothing matched.

## `graph` — the call graph

```sh
datapack-emulator-cli graph samples/hat_v2
datapack-emulator-cli graph samples/hat_v2 -f hat:tick --dot generated/hat.dot
```

Every node in call order, indented by depth, with its kind (function, tag,
missing, macro, overlay) and its outgoing edges (`call`, `macro`, `schedule`,
`condition`, `tag`), then recursion, missing functions and functions nothing
calls. `-f ID` (repeatable) shows callers and calls of a function or `#tag`,
with your note. `--dot [FILE]` writes Graphviz (default
`generated/<pack>-<version>.dot`); `--json` prints nodes, edges, cycles and
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
| `set FILE` | settings: `--name`, `--version` (`''` = the pack's), `--ticks` (`-1` = until stopped), `--players`, `--seed`, `--speed fast\|realtime`, `--engine-versions V…`, `--vanilla-jar JAR` (`''` = none), `--tests-during-runs on\|off`, `--step-on-command on\|off`, `--notes TEXT` or `--notes-file FILE` |
| `packs FILE` | list; `--add DIR`, `--remove N`, `--move N up\|down` (load order; a project keeps at least one pack) |
| `tests FILE` | list; `--add [TICK:]COMMAND`, `--expect N TEXT`, `--expect-value N RANGE`, `--tick N TICK`, `--enable N`, `--disable N`, `--duplicate N`, `--move N up\|down`, `--remove N` |
| `note FILE FUNCTION [TEXT]` | read, write or (with `''`) remove the note on a function or `#tag` (a warning when the packs have no such id) |
| `debug FILE` | list the debugger's breakpoints and watches; `--break FUNC:LINE[ if COND]`, `--unbreak FUNC:LINE\|all`, `--enable FUNC:LINE`, `--disable FUNC:LINE`, `--watch EXPR`, `--unwatch N\|all` (the debugger dock's lists) |
| `list` | the projects in the projects folder the window saves to (`projects/` of the checkout, or the per-user data folder) |
| `recent` | the window's recent projects and datapacks |

`packs` and `tests` options can be repeated and mixed; they apply in the
order given, and every number refers to the list **as it was before the
command** (so `--duplicate 1 --remove 1` keeps only the copy, and
`--remove 1 --expect 1 x` is an error).

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
