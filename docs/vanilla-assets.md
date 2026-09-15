# Base-game assets from `client.jar`

The Minecraft client jar already contains most of what the base game knows.
`src/emulator/vanilla.py` reads it instead of hardcoding anything.

## What is read

| jar entry | becomes |
| --- | --- |
| `version.json` | version id, data version, data pack format |
| `assets/minecraft/lang/en_us.json` | every message string; entity, effect and attribute ids |
| `assets/minecraft/blockstates/*.json` | block registry |
| `assets/minecraft/items/*.json` | item registry |
| `assets/minecraft/particles/*.json` | particle registry |
| `data/minecraft/<registry>/` | advancement, banner_pattern, chat_type, damage_type, dimension_type, enchantment, instrument, jukebox_song, loot_table, painting_variant, recipe, structure |
| `data/minecraft/tags/<registry>/` | every vanilla tag |

Not in the jar, so not available: the sound event list (it ships in the asset
index, downloaded separately) and the command tree (that comes from the data
generator — see [versions.md](versions.md)).

## What it changes

With assets loaded for a run:

* **Message wording** — `MessageCatalogue` uses that version's `en_us.json`
  before the built-in table, so errors read exactly as that release prints
  them.
* **Id checks** — these commands report vanilla's own error when an id does
  not exist in that version:

  | command | registry | error |
  | --- | --- | --- |
  | `summon` | entity_type | `Unknown ID: minecraft:armour_stand` |
  | `setblock` | block | `Unknown block type 'minecraft:stoen'` |
  | `give`, `clear` | item | `Unknown item 'minecraft:diamomd'` |
  | `effect give` | mob_effect | `Unknown ID: minecraft:speeed` |
  | `particle` | particle | `Unknown ID: minecraft:flamme` |

* **Entity type tags** — `@e[type=#minecraft:skeletons]` resolves through the
  real tag. Without a jar, a tag filter cannot be evaluated and is skipped.

Without a jar nothing is rejected: ids are let through rather than guessed at.

## Where jars are found

`VanillaLibrary.local_jars()` looks, in order, in:

* `~/.minecraft/versions/<v>/` (vanilla launcher; also the macOS and Windows
  equivalents)
* `~/.local/share/PrismLauncher/libraries/com/mojang/minecraft/<v>/`
  (also MultiMC and the Prism Flatpak)
* `.cache/vanilla/<v>/` in this repository (downloads)

The main window loads the jar for the selected version automatically when one
is found. *file › load client jar…* picks any jar by hand.

## Downloading

*file › download client jar for this version*, or:

```sh
python -m src.emulator vanilla --download 1.21.4
```

The version is looked up in Mojang's
`piston-meta.mojang.com/mc/game/version_manifest_v2.json`, then its client jar
is saved to `.cache/vanilla/<version>/`. Nothing is downloaded unless you ask.
`.cache/` is git-ignored.

## From code

```python
from src.emulator.vanilla import default_library

library = default_library()
assets = library.load("26.2", allow_download=False)   # None if not installed
assets.knows("block", "minecraft:stone")               # True / False / None
assets.resolve_tag("entity_type", "#minecraft:skeletons")
assets.message("commands.summon.success")              # "Summoned new %s"

Emulator(pack, version="26.2", vanilla=assets)
TestEngine(pack, library=library)                      # one jar per version
```
