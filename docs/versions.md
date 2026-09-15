# Versions, pack formats and overlays

Every version-dependent decision goes through `src/emulator/versions.py`.
Nothing else in the code compares version strings.

## Where the data comes from

`src/emulator/version_data.py` is **generated** from
[misode/mcmeta](https://github.com/misode/mcmeta), which republishes Mojang's
data-generator output for every release:

* `summary/versions/data.json` — every release with its data pack format,
  format minor and data version
* `<version>-summary/commands/data.json` — the full Brigadier command tree of
  that release

The generator walks every release from 1.14 on, extracts feature keys from
each command tree, and records the first release that has each key and the
first release that dropped it. 1.13 has no tree in mcmeta; it is added by hand
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
from src.emulator import versions

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
| singular registry folders (`function/`, `tags/function/`, …) | 1.21 | files in the other spelling are reported as ignored |
| `overlays` and `supported_formats` in `pack.mcmeta` | 1.20.2 | older versions read only the base pack |
| `min_format` / `max_format` | 1.21.9 | parsed alongside the older fields |
| macros (`$` lines, `function … with`) | 1.20.2 | reported as unavailable before |

## Pack formats in `pack.mcmeta`

All of these are understood: `pack_format` as an int or a `major.minor`
float, `supported_formats` as an int, a `[min, max]` list or a
`{min_inclusive, max_inclusive}` object, and `min_format` / `max_format` as
ints, floats or `[major, minor]` lists.

`Datapack.declared_versions()` turns that into the list of releases the pack
claims; `Datapack.supports(version)` answers for one.

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
