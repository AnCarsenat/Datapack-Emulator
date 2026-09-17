# Command line

```sh
datapack-emulator-cli [--quiet] COMMAND ...
src/main.sh --cli     [--quiet] COMMAND ...   # from a checkout
# same as: python -m datapack_emulator.cli ...  (or python -m datapack_emulator.emulator ...)
```

The command line is meant to do what the window does, so a pack can be
checked from a terminal or in CI. `--quiet` goes *before* the command and
silences the Python logger (records from the emulator still print).

| command | does | window equivalent |
| --- | --- | --- |
| [`run`](#run--one-version) | emulate one version, profile it (and run the tests during the run) | run all (F5) |
| [`matrix`](#matrix--many-versions) | run across versions | engine window |
| [`test`](#test--a-projects-tests) | run a project's tests, exit 1 on failure | run tests (F8), engine *run tests* |
| [`versions`](#versions) | list known versions | the version combo |
| [`vanilla`](#vanilla--client-jars) | list, download, inspect client jars | *file › load / download client jar* |

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
