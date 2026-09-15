# Versions, pack formats and overlays

Every version-dependent decision goes through `src/datapack_emulator/emulator/versions.py`.
Nothing else in the code compares version strings.

## Where the data comes from

`src/datapack_emulator/emulator/version_data.py` is **generated** from
[misode/mcmeta](https://github.com/misode/mcmeta), which republishes Mojang's
data-generator output for every release:

* `summary/versions/data.json` — every release with its data pack format,
  format minor and data version
* `<version>-summary/commands/data.json` — the full Brigadier command tree of
  that release

The generator walks every release from 1.14 on, extracts feature keys from
each command tree, and records the first release that has each key and the
first release that dropped it.

It also takes the newest pre-release or release candidate of a version that
has not shipped yet (for example `26.3-rc-3` before 26.3), marked
`stable=False`. It sorts before its final release, `parse("26.3")` resolves to
it until the release exists, and `LATEST` stays the newest *stable* release
(`NEWEST` includes the pre-release). Re-running the generator after the release
replaces it. 1.13 has no tree in mcmeta; it is added by hand
with pack format 4 and answered as 1.14 for features.

### Feature keys

| key | example | from the tree |
| --- | --- | --- |
| `command:<name>` | `command:return` | top-level command |
| `execute:<sub>` | `execute:on` | `execute` child |
| `condition:<name>` | `condition:items` | `execute if` child |
| `store:<target>` | `store:storage` | `execute store result` child |
| `schedule:<sub>` | `schedule:clear` | `schedule` child |
| `return:<sub>` | `return:run` | `return` child |
| `function:with` | | `with` anywhere under `function` (macros) |

`FEATURE_SINCE["command:return"] == "1.20"`,
`FEATURE_UNTIL["command:replaceitem"] == "1.17"`.

### Regenerating

```sh
python tools/generate_version_data.py --work-dir .mcmeta-cache
```

Needs network access to `raw.githubusercontent.com`. Downloads are cached in
the work dir, so re-runs only fetch new releases. Commit the regenerated
`version_data.py`; never edit it by hand.

## The API

```python
from datapack_emulator.emulator import versions

v = versions.parse("1.21.4")                # exact release id
versions.parse("26")                        # not a release: newest 26.x
versions.version_range("1.20.4", "1.21.4")  # inclusive, ordered
versions.for_pack_format(107.1)             # releases using that format
versions.closest_to_pack_format(50)         # newest release at or below
versions.supports(v, "execute:on")          # feature query
versions.vanilla_commands(v)                # every command name in v
versions.uses_singular_registries(v)        # function/ vs functions/
versions.supports_overlays(v)               # 1.20.2+
```

`Version` objects sort numerically (`1.21.10 > 1.21.9`, `26.1 > 1.21.11`) and
carry `pack_format`, `pack_format_minor`, `format` (a tuple) and
`format_string` (`"107.1"`).

## Rules that are not commands

| rule | since | effect |
| --- | --- | --- |
| singular registry folders (`function/`, `tags/function/`, …) | 1.21 | each version loads only its own spelling; files in the other one are not loaded, and the engine reports them |
| `overlays` and `supported_formats` in `pack.mcmeta` | 1.20.2 | older versions read only the base pack |
| `min_format` / `max_format` | 1.21.9 | parsed alongside the older fields |
| macros (`$` lines, `function … with`) | 1.20.2 | a `$` line is an unknown command before, so its function fails to load |

## What a server of each version loads

Checked in decompiled Mojang jars (1.16.1 to 26.3-rc-3) and applied by
`FunctionLibrary` in `src/datapack_emulator/emulator/runtime/library.py`:

* **Functions** are compiled one by one. A function with a line its version
  cannot parse is **not loaded at all** ("Failed to load function …: Whilst
  parsing command on line N: …"); every other function still loads. From
  1.20.2, `$` macro lines are parsed when called, so they never fail the load;
  before, a `$` line fails it like any unknown command. Multi-version
  packs use this on purpose, e.g. one function with `replaceitem` for 1.16 and
  one with `item replace` for 1.17+.
* `function <id>` resolves when it runs, so calling a function that failed to
  load only fails that call, silently.
* A **function tag** with any missing required entry is dropped whole ("Couldn't
  load tag … as it is missing following references: …"). `{"id": …,
  "required": false}` entries are skipped from 1.16.2; 1.16.1 cannot read them,
  so the tag file fails.
* On the first tick after start or reload, 1.16.1–1.19.2 run `#minecraft:tick`
  **before** `#minecraft:load`; 1.19.3 and later run load first.

These server-log lines are recorded as `game` warnings: vanilla's console
reports some of them as ERROR, but the pack keeps loading and working.

## Pack formats in `pack.mcmeta`

All of these are understood: `pack_format` as an int or a `major.minor`
float, `supported_formats` as an int, a `[min, max]` list or a
`{min_inclusive, max_inclusive}` object, and `min_format` / `max_format` as
ints, floats or `[major, minor]` lists.

`Datapack.declared_versions()` turns that into the list of releases the pack
claims. `Datapack.compatibility(version)` answers the way that version reads
the file, and the answer is never "refuse": an incompatible pack still loads,
the game only warns in the pack list.

| versions | reads | notes |
| --- | --- | --- |
| 1.13 – 1.20.1 | `pack_format` only | compared as one number |
| 1.20.2 – 1.21.8 | `pack_format` + `supported_formats` | a range that excludes `pack_format` is warned about and ignored |
| 1.21.9 + | `min_format` / `max_format` (required) | with a minimum of 81 or below, `pack_format` and `supported_formats` must be present, agree with min/max, and `pack_format` must be ≥ 15; otherwise the server logs "Couldn't load <pack> pack metadata" and compatibility is unknown |

So no single `pack.mcmeta` is clean both on 1.16.1 (which wants
`pack_format` 5) and on 1.21.9+ (which wants at least 15). `pack_format: 15`
with `supported_formats`/`min_format`/`max_format` from 15 loads everywhere and
is only flagged in the 1.16–1.20.1 pack list.

For packs whose declared range spans a change (the 1.21 folder rename, 1.20.2
overlays), the engine reports the legacy forms a version ignores as `info`
rather than `warning`, since they are there on purpose.

## Overlays

```json
"overlays": { "entries": [
  { "formats": { "min_inclusive": 10, "max_inclusive": 40 }, "directory": "legacy" }
]}
```

Each entry's directory is loaded as its own `Layer` (a set of namespaces).
`Datapack.view_for(version)` returns a `PackView`: the base layer, then every
overlay whose range contains the version's format, later layers overriding
earlier ones by resource id. Versions before 1.20.2 get the base layer only.

The emulator, the call graph, the explorer's inspector and the engine all read
the `PackView`, so switching versions switches the code that runs. Resources
remember which overlay they came from (`resource.overlay`).
