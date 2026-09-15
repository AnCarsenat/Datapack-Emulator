# Command line

```sh
datapack-emulator-cli [--quiet] {run,matrix,versions,vanilla} ...
src/main.sh --cli    [--quiet] {run,matrix,versions,vanilla} ...   # from a checkout
# same as: python -m datapack_emulator.emulator ...
```

Run from the project root. `--quiet` goes *before* the subcommand and silences
the Python logger (records from the emulator still print).

## `run` — one version

```sh
python -m datapack_emulator.emulator run samples/hat --version 1.21.4 --ticks 20
```

| option | default | |
| --- | --- | --- |
| `datapack` | | one or more folders holding `pack.mcmeta`, analyzed together in that order |
| `--version` | latest | release id (`1.21` means 1.21 itself); a prefix that is not a release, like `26`, means its newest release |
| `--ticks` | 20 | |
| `--players` | 1 | |
| `--level` | info | `debug`, `info`, `warn`, `error` — lowest level printed |
| `--sources` | all | any of `app emulator game` |
| `--vanilla [VERSION\|JAR]` | off | check ids and message wording against a client jar; no value = the emulated version |
| `--download` | off | fetch the jar from Mojang if it is not installed |
| `--html` | `generated/index.html` | profiler report |
| `--dot` | off | also write the call graph as Graphviz |

Prints every record as `[tick] source/level function:line: message`, then a
per-function table (calls, commands, self and total ms for the run, and ms per
tick), the report path and call-graph findings (recursion, missing functions,
functions nothing calls).

## `matrix` — many versions

```sh
python -m datapack_emulator.emulator matrix samples/hat --from 1.20.4 --to 26.2 --boundaries
python -m datapack_emulator.emulator matrix samples/hat --declared --vanilla
```

Selection (first match wins): `--versions A B C`, `--from/--to`, `--declared`
(the range `pack.mcmeta` claims), otherwise every known release.
`--boundaries` then keeps only the first release of each pack format.

| option | |
| --- | --- |
| `--ticks`, `--players` | as for `run` |
| `--vanilla` | use each version's client jar when installed |
| `--download` | fetch missing jars (with `--vanilla`) |
| `--html` | matrix report, default `generated/matrix.html` |

Output is one row per version: format, status, commands, total ms, worst ms,
warnings, errors, unknown commands, overlays. See [engine.md](engine.md).

## `versions`

Lists every known release with its pack format and data version.

## `vanilla` — client jars

```sh
python -m datapack_emulator.emulator vanilla                    # list jars found on this machine
python -m datapack_emulator.emulator vanilla --download 1.21.4  # fetch into .cache/vanilla
python -m datapack_emulator.emulator vanilla --inspect 26.2     # what the jar contains
python -m datapack_emulator.emulator vanilla --inspect path/to/client.jar
```

## Regenerating version data

```sh
python tools/generate_version_data.py [--work-dir .mcmeta-cache]
```

See [versions.md](versions.md#regenerating).
