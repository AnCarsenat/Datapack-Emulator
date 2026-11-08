"""Regenerate ``src/datapack_emulator/emulator/item_defaults.py`` from misode/mcmeta.

The client jar has no per-item component data; the server writes it with
``java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --reports``
(``reports/items.json``), and misode/mcmeta publishes that report for every
version as ``<version>-summary/item_components/data.json``. This reads it and
keeps only the components the emulator models (see ``COMPONENTS``).

    python tools/generate_item_defaults.py [--version 26.3] [--work-dir .mcmeta-cache]

Needs network access (raw.githubusercontent.com).
"""

import argparse
import datetime
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "datapack_emulator" / "emulator" / "item_defaults.py"
URL = "https://raw.githubusercontent.com/misode/mcmeta/{version}-summary/item_components/data.json"

#: components kept per item, with the value of the items not listed (None: absent)
COMPONENTS: dict[str, object] = {
    "minecraft:max_stack_size": 64,
    "minecraft:rarity": "common",
    "minecraft:max_damage": None,
    "minecraft:damage": None,
    "minecraft:food": None,
    "minecraft:consumable": None,
    "minecraft:tool": None,
    "minecraft:weapon": None,
    "minecraft:equippable": None,
    "minecraft:enchantable": None,
    "minecraft:repairable": None,
}
#: components every item has with the same value (checked below)
COMMON = ("minecraft:enchantments", "minecraft:lore", "minecraft:repair_cost",
          "minecraft:tooltip_display")  # fmt: skip

parser = argparse.ArgumentParser()
parser.add_argument("--version", default="26.3")
parser.add_argument("--work-dir", type=pathlib.Path, default=ROOT / ".mcmeta-cache")
arguments = parser.parse_args()
target = arguments.work_dir / f"item_components-{arguments.version}.json"
if not target.exists():
    target.parent.mkdir(parents=True, exist_ok=True)
    url = URL.format(version=arguments.version)
    with urllib.request.urlopen(url, timeout=60) as response:
        target.write_bytes(response.read())
report: dict[str, dict] = json.loads(target.read_text(encoding="utf-8"))

common: dict[str, object] = {}
for component in COMMON:
    values = {json.dumps(item.get(component), sort_keys=True) for item in report.values()}
    if len(values) != 1:
        raise SystemExit(f"{component} differs between items: {sorted(values)[:3]}")
    common[component] = json.loads(values.pop())

per_item: dict[str, dict[str, object]] = {}
for component, fallback in COMPONENTS.items():
    per_item[component] = {
        item: components[component]
        for item, components in sorted(report.items())
        if components.get(component, None) != fallback
    }
    missing = [i for i, c in report.items() if fallback is not None and component not in c]
    if missing:
        raise SystemExit(f"{component} is missing on {missing[:3]}, it cannot have a fallback")

problems = [
    item
    for item, components in report.items()
    if components["minecraft:item_model"] != f"minecraft:{item}"
    or components["minecraft:item_name"].get("translate")
    not in (f"item.minecraft.{item}", f"block.minecraft.{item}")
]
if problems:
    raise SystemExit(f"item_model / item_name do not follow the item id: {problems[:5]}")
block_named = sorted(
    item
    for item, components in report.items()
    if components["minecraft:item_name"]["translate"].startswith("block.")
)


def literal(value: object) -> str:
    """A Python literal with double quotes, like ruff writes it."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items()) + "}"
    return repr(value)


def words(name: str, items: list[str]) -> list[str]:
    """``NAME = frozenset(\"\"\"a b c …\"\"\".split())``, wrapped at 96 columns."""
    out = [f"{name}: frozenset[str] = frozenset(", '    """']
    line = ""
    for item in items:
        if line and len(line) + len(item) + 1 > 92:
            out.append("    " + line)
            line = item
        else:
            line = f"{line} {item}" if line else item
    out += ["    " + line, '    """.split()', ")"]
    return out


lines = [
    '"""Default (prototype) components of every item, for the components the emulator models.',
    "",
    "GENERATED FILE — do not edit by hand.  Rebuilt by tools/generate_item_defaults.py from",
    f"  {URL.format(version=arguments.version)}",
    "(misode/mcmeta's copy of the server's ``--reports`` item components report).",
    f"Snapshot taken {datetime.date.today().isoformat()}; values are those of {arguments.version}.",
    "",
    "Item ids are written without their ``minecraft:`` namespace.",
    '"""',
    "",
    "from __future__ import annotations",
    "",
    "from typing import Any",
    "",
    f"VERSION = {literal(arguments.version)}",
    "",
    "#: every item in the report",
    *words("ITEMS", sorted(report)),
    "",
    "#: components every item has, with the same value",
    "COMMON: dict[str, Any] = {",
    *(f"    {literal(key)}: {literal(value)}," for key, value in common.items()),
    "}",
    "",
    "#: value of a component for the items ``PER_ITEM`` does not list (absent when missing)",
    "FALLBACK: dict[str, Any] = {",
    *(
        f"    {literal(key)}: {literal(value)},"
        for key, value in COMPONENTS.items()
        if value is not None
    ),
    "}",
    "",
    "#: component -> item -> its default value, where it differs from ``FALLBACK``",
    "PER_ITEM: dict[str, dict[str, Any]] = {",
]
for component, items in per_item.items():
    lines.append(f"    {literal(component)}: {{")
    lines.extend(f"        {literal(item)}: {literal(value)}," for item, value in items.items())
    lines.append("    },")
lines += [
    "}",
    "",
    "#: items named ``block.minecraft.<id>`` (the rest are ``item.minecraft.<id>``)",
    *words("BLOCK_NAMED", block_named),
    "",
]
OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {OUT} ({len(report)} items)")
