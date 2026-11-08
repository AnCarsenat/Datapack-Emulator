"""Item components as the ItemStack codec sees them (1.20.5+).

A stack stores a *patch* over its item's default (prototype) components: values
that differ from the default, and ``!key`` for a default that was removed. The
game always reads a stack through its codec, so:

* component predicates (``*[max_stack_size=16]``, ``*[food]``, ``*[!food]``)
  test the *effective* components, the defaults with the patch applied
  (:func:`effective`);
* a stack written into a block or entity comes back canonical: identifiers
  namespaced, components equal to the default dropped (:func:`normalise`),
  and a stack the codec cannot decode is refused (:func:`stack_problem`).

Where the defaults come from (a model, see ``docs/emulation.md``):

* ``item_defaults.py``, generated from the server's item components report
  for 26.3 (via misode/mcmeta), holds ``max_stack_size``, ``max_damage``,
  ``damage``, ``food``, ``consumable``, ``tool``, ``weapon``, ``equippable``,
  ``rarity``, ``enchantable``, ``repairable``, the components every item has
  (``enchantments``, ``lore``, ``repair_cost``, ``tooltip_display``) and the
  ``item_name`` translation key; ``item_model`` is the item id for every item;
* each is used from the version that introduced it (``SINCE``); the values
  of compound components are 26.3's and only used from 1.21.5 (``VALUE_SINCE``),
  before which only their presence is known;
* any other component (``attribute_modifiers``, ``container``, …) has no
  known default: only what the stack itself carries counts.
"""

from __future__ import annotations

import re
import struct
from functools import lru_cache
from typing import Any

from datapack_emulator.emulator import item_defaults, versions
from datapack_emulator.emulator.common import (
    nbt_equal,
    normalise_id,
    normalise_tagged_id,
    untyped,
)
from datapack_emulator.emulator.versions import Version

#: the default of a component whose presence is known but not its value
UNKNOWN: Any = type("Unknown", (), {"__repr__": lambda self: "UNKNOWN"})()

#: first version where the component is a default component of items
SINCE: dict[str, str] = {
    **dict.fromkeys(
        ("max_stack_size", "max_damage", "damage", "food", "rarity", "tool",
         "enchantments", "lore", "repair_cost"),
        "1.20.5",
    ),
    **dict.fromkeys(
        ("consumable", "equippable", "enchantable", "repairable", "item_model", "item_name"),
        "1.21.2",
    ),
    **dict.fromkeys(("weapon", "tooltip_display"), "1.21.5"),
}  # fmt: skip
SINCE = {f"minecraft:{key}": value for key, value in SINCE.items()}
#: compound components whose 26.3 value is only used from this version
#: (food lost eat_seconds in 1.21.2, enchantments their ``levels`` wrapper in 1.21.5…)
VALUE_SINCE: dict[str, str] = {
    f"minecraft:{key}": "1.21.5"
    for key in ("food", "consumable", "tool", "equippable", "enchantments")
}

#: items that do not stack, for ids the table does not know
_UNSTACKABLE_SUFFIXES = (
    "_sword", "_pickaxe", "_axe", "_shovel", "_hoe", "_helmet", "_chestplate", "_leggings",
    "_boots", "_horse_armor", "_boat", "_raft", "_minecart", "_bucket", "shulker_box",
    "_bed", "potion", "_banner_pattern", "music_disc_",
)  # fmt: skip
_SIXTEEN_SUFFIXES = ("_sign", "_hanging_sign", "_banner", "_egg")


def _since(version: Version | None, first: str) -> bool:
    return version is None or version >= versions.parse(first)


def _fallback_stack_size(path: str) -> int:
    """A guess by name for an item the table does not list."""
    if any(part in path for part in _UNSTACKABLE_SUFFIXES):
        return 1
    if path.endswith(_SIXTEEN_SUFFIXES) and not path.endswith("spawn_egg"):
        return 16
    return 64


def default_stack_size(item_id: str) -> int:
    namespace, _, path = item_id.rpartition(":")
    if namespace in ("", "minecraft") and path in item_defaults.ITEMS:
        return int(
            item_defaults.PER_ITEM["minecraft:max_stack_size"].get(
                path, item_defaults.FALLBACK["minecraft:max_stack_size"]
            )
        )
    return _fallback_stack_size(path)


def defaults(item_id: str, version: Version | None) -> dict[str, Any]:
    """The item's default components known to the emulator, in ``version``.

    A value may be :data:`UNKNOWN` (present, value not modelled for that version).
    The dict is shared: copy it before changing it.
    """
    return _defaults(item_id, version)


@lru_cache(maxsize=4096)
def _defaults(item_id: str, version: Version | None) -> dict[str, Any]:
    if version is not None and version < versions.parse(SINCE["minecraft:max_stack_size"]):
        return {}
    namespace, _, path = item_id.rpartition(":")
    known = namespace in ("", "minecraft") and path in item_defaults.ITEMS
    found: dict[str, Any] = dict(item_defaults.COMMON)
    found["minecraft:item_model"] = normalise_id(item_id)
    if known:
        kind = "block" if path in item_defaults.BLOCK_NAMED else "item"
        found["minecraft:item_name"] = {"translate": f"{kind}.minecraft.{path}"}
        for component, values in item_defaults.PER_ITEM.items():
            value = values.get(path, item_defaults.FALLBACK.get(component, UNKNOWN))
            if value is not UNKNOWN:
                found[component] = value
    else:
        found["minecraft:max_stack_size"] = _fallback_stack_size(path)
        found["minecraft:item_name"] = UNKNOWN
        found["minecraft:rarity"] = item_defaults.FALLBACK["minecraft:rarity"]
    out: dict[str, Any] = {}
    for component, value in found.items():
        if not _since(version, SINCE.get(component, "1.20.5")):
            continue
        first = VALUE_SINCE.get(component)
        out[component] = value if first is None or _since(version, first) else UNKNOWN
    return out


def qualify(key: str) -> str:
    """``food`` -> ``minecraft:food``, ``!food`` -> ``!minecraft:food``."""
    key = key.strip()
    if key.startswith("!"):
        return "!" + normalise_id(key[1:])
    return normalise_id(key)


def effective(item_id: str, components: dict[str, Any], version: Version | None) -> dict[str, Any]:
    """Defaults with the stack's patch applied (``!key`` removes a default)."""
    result = dict(defaults(item_id, version))
    for key, value in components.items():
        name = qualify(key)
        if name.startswith("!"):
            result.pop(name[1:], None)
        else:
            result[name] = namespace_ids(name, value)
    return result


def _float32(value: float) -> float:
    try:
        return float(struct.unpack("f", struct.pack("f", value))[0])
    except (OverflowError, struct.error):
        return value


#: components that hold raw NBT (``CustomData``): the game keeps their SNBT
#: types, so ``{a:1b}`` and ``{a:1}`` differ; every other component is decoded
#: by a codec that gives each field its own type
RAW_NBT = frozenset(
    f"minecraft:{name}"
    for name in ("custom_data", "entity_data", "block_entity_data", "bucket_entity_data")
)
#: components that hold item stacks (normalised by the stack codec)
_STACK_LISTS = frozenset({"minecraft:bundle_contents", "minecraft:charged_projectiles"})


def same_value(a: Any, b: Any, key: str | None = None) -> bool:
    """Equal as the game compares decoded components of type ``key``: floats at
    float precision, numbers whatever their NBT type, compounds whatever their
    key order; raw NBT (:data:`RAW_NBT`) exactly, types included."""
    if a is UNKNOWN or b is UNKNOWN:
        return False
    if key in RAW_NBT:
        return nbt_equal(a, b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            return _float32(float(a)) == _float32(float(b))
        return int(a) == int(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


# ---------------------------------------------------------------------------
# identifiers inside components
# ---------------------------------------------------------------------------

#: components whose value is an identifier (or an inline definition, left as is)
_ID_VALUED = frozenset(
    f"minecraft:{name}"
    for name in (
        "item_model", "tooltip_style", "note_block_sound", "damage_type", "break_sound",
        "instrument", "jukebox_playable", "provides_trim_material", "provides_banner_patterns",
        "painting/variant", "villager/variant", "wolf/variant", "wolf/sound_variant",
        "cat/variant", "cat/sound_variant", "pig/variant", "pig/sound_variant", "cow/variant",
        "cow/sound_variant", "chicken/variant", "chicken/sound_variant", "frog/variant",
        "zombie_nautilus/variant",
    )
)  # fmt: skip
#: components whose value is a list of identifiers
_ID_LISTS = frozenset({"minecraft:recipes", "minecraft:pot_decorations"})
#: compound components -> their identifier fields
_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "minecraft:equippable": ("asset_id", "equip_sound", "camera_overlay", "allowed_entities",
                             "shearing_sound"),
    "minecraft:consumable": ("sound",),
    "minecraft:use_cooldown": ("cooldown_group",),
    "minecraft:damage_resistant": ("types",),
    "minecraft:repairable": ("items",),
    "minecraft:potion_contents": ("potion",),
    "minecraft:trim": ("material", "pattern"),
    "minecraft:jukebox_playable": ("song",),
    "minecraft:entity_data": ("id",),
    "minecraft:block_entity_data": ("id",),
}  # fmt: skip
_ENCHANTMENT_MAPS = frozenset({"minecraft:enchantments", "minecraft:stored_enchantments"})

_NAMESPACE = re.compile(r"[a-z0-9_.-]*")
_PATH = re.compile(r"[a-z0-9_./-]*")


def _id(value: Any) -> Any:
    """An id, an ``#tag`` or a list of them, namespaced; anything else as is."""
    if isinstance(value, str):
        return normalise_tagged_id(value)
    if isinstance(value, list):
        return [_id(item) for item in value]
    return value


def namespace_ids(key: str, value: Any) -> Any:
    """``value`` of component ``key`` with the identifiers in it namespaced."""
    if key in _ID_VALUED or key in _ID_LISTS:
        return _id(value) if isinstance(value, (str, list)) else _fields(key, value)
    if key in _ENCHANTMENT_MAPS and isinstance(value, dict):
        levels = value.get("levels")
        if isinstance(levels, dict):  # before 1.21.5: {levels: {…}, show_in_tooltip}
            return {**value, "levels": {normalise_id(k): v for k, v in levels.items()}}
        return {normalise_id(k): v for k, v in value.items()}
    if key == "minecraft:tool" and isinstance(value, dict) and isinstance(value.get("rules"), list):
        rules = [
            {**rule, "blocks": _id(rule["blocks"])} if isinstance(rule, dict) and "blocks" in rule
            else rule
            for rule in value["rules"]
        ]  # fmt: skip
        return {**value, "rules": rules}
    if key == "minecraft:banner_patterns" and isinstance(value, list):
        return [
            {**layer, "pattern": _id(layer["pattern"])}
            if isinstance(layer, dict) and isinstance(layer.get("pattern"), str)
            else layer
            for layer in value
        ]
    if key == "minecraft:potion_contents" and isinstance(value, str):
        return normalise_id(value)
    if key in _STACK_LISTS and isinstance(value, list):
        return [normalise_stack_nbt(item, None) for item in value]
    if key == "minecraft:container" and isinstance(value, list):
        return [
            {**entry, "item": normalise_stack_nbt(entry["item"], None)}
            if isinstance(entry, dict) and isinstance(entry.get("item"), dict)
            else entry
            for entry in value
        ]
    if key == "minecraft:use_remainder" and isinstance(value, dict):
        return normalise_stack_nbt(value, None)
    return _fields(key, value)


def _fields(key: str, value: Any) -> Any:
    names = _ID_FIELDS.get(key)
    if not names or not isinstance(value, dict):
        return value
    return {name: _id(item) if name in names else item for name, item in value.items()}


def _bad_ids(value: Any) -> list[str]:
    """Strings among ``value`` (a namespaced id, tag or list) that are no valid id."""
    if isinstance(value, list):
        return [bad for item in value for bad in _bad_ids(item)]
    if not isinstance(value, str):
        return []
    namespace, _, path = value.removeprefix("#").partition(":")
    if not _NAMESPACE.fullmatch(namespace) or not path or not _PATH.fullmatch(path):
        return [value]
    return []


# ---------------------------------------------------------------------------
# the codec's canonical form
# ---------------------------------------------------------------------------


def normalise(item_id: str, components: dict[str, Any], version: Version | None) -> dict[str, Any]:
    """The patch the codec writes back: keys and ids namespaced, values equal to
    the default dropped, removals of components the item does not have dropped."""
    known = defaults(item_id, version)
    out: dict[str, Any] = {}
    for key, value in components.items():
        name = qualify(key)
        if name.startswith("!"):
            if known.get(name[1:], None) is None and name[1:] in SINCE:
                continue  # removing what the item does not have: nothing to store
            out[name] = {}
            continue
        value = schema_typed(name, namespace_ids(name, value))
        if name in known and same_value(value, known[name], name):
            continue
        out[name] = value
    return out


def same_patch(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two normalised patches are the same (see :func:`same_value`)."""
    return a.keys() == b.keys() and all(same_value(a[key], b[key], key) for key in a)


def _untyped_stack(stack: Any) -> Any:
    if not isinstance(stack, dict):
        return untyped(stack)
    return {key: item if key == "components" else untyped(item) for key, item in stack.items()}


def schema_typed(key: str, value: Any) -> Any:
    """``value`` of component ``key`` with the SNBT number types it was written
    with dropped, as the component's codec does (``max_stack_size:16b`` is
    stored as an int); raw NBT keeps them, nested stacks keep their own
    components' (already normalised). The emulator does not know each field's
    type, so the numbers become plain and compare by value."""
    if key in RAW_NBT:
        return value
    if key in _STACK_LISTS and isinstance(value, list):
        return [_untyped_stack(item) for item in value]
    if key == "minecraft:use_remainder":
        return _untyped_stack(value)
    if key == "minecraft:container" and isinstance(value, list):
        return [
            {k: _untyped_stack(v) if k == "item" else untyped(v) for k, v in entry.items()}
            if isinstance(entry, dict)
            else untyped(entry)
            for entry in value
        ]
    return untyped(value)


def normalise_stack_nbt(data: Any, version: Version | None) -> Any:
    """A nested stack (in ``container``, ``bundle_contents`` …) in canonical form."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return data
    out = dict(data)
    out["id"] = normalise_id(data["id"])
    components = data.get("components")
    if isinstance(components, dict):
        patch = normalise(out["id"], components, version)
        if patch:
            out["components"] = patch
        else:
            out.pop("components")
    return out


def component_problem(key: str, value: Any) -> str | None:
    """Why the codec of component ``key`` refuses ``value``, or None (only the
    checks listed under :func:`stack_problem` are made)."""
    name = qualify(str(key))
    if _bad_ids(name.removeprefix("!")):
        return f"Non [a-z0-9/._-] character in path of location: {name}"
    if name.startswith("!"):
        return None
    value = namespace_ids(name, value)
    bad: list[str] = []
    if name in _ID_VALUED or name in _ID_LISTS:
        bad = _bad_ids(value) if isinstance(value, (str, list)) else []
    elif name in _ID_FIELDS and isinstance(value, dict):
        bad = [b for field in _ID_FIELDS[name] for b in _bad_ids(value.get(field))]
    if bad:
        return f"Non [a-z0-9/._-] character in path of location: {bad[0]}"
    integer = isinstance(value, int) and not isinstance(value, bool)
    if name == "minecraft:max_stack_size" and not (integer and 1 <= value <= 99):
        return f"Value must be within range [1;99]: {value}"
    if name == "minecraft:max_damage" and not (integer and value >= 1):
        return f"Value must be positive: {value}"
    return None


def stack_problem(data: dict[str, Any]) -> str | None:
    """Why the 1.20.5+ codec refuses this stack NBT, or None.

    Checked: ``count`` in 1..99, ``components`` a compound with valid keys, the
    identifiers of id-valued components, ``max_stack_size`` in 1..99 and a
    positive ``max_damage``. Unknown item ids and component types are not.
    """
    count = data.get("count")
    if isinstance(count, (int, float)) and not isinstance(count, bool) and not 1 <= count <= 99:
        return f"Value must be within range [1;99]: {count}"
    components = data.get("components")
    if components is None:
        return None
    if not isinstance(components, dict):
        return f"Not a map: {components!r}"
    for key, value in components.items():
        problem = component_problem(key, value)
        if problem is not None:
            return f"{qualify(str(key))}: {problem}"
    return None
