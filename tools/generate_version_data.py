"""Regenerate ``src/emulator/version_data.py`` from misode/mcmeta.

Downloads the release table and one Brigadier command tree per release into a
work directory, diffs them, and writes the table the emulator reads.

    python tools/generate_version_data.py [--work-dir .mcmeta-cache]

Needs network access (raw.githubusercontent.com) and a few minutes the first
time; afterwards the cached trees are reused.
"""

import argparse
import datetime
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "emulator" / "version_data.py"
VERSIONS_URL = "https://raw.githubusercontent.com/misode/mcmeta/summary/versions/data.json"
COMMANDS_URL = (
    "https://raw.githubusercontent.com/misode/mcmeta/{version}-summary/commands/data.json"
)


def fetch(url: str, target: pathlib.Path) -> pathlib.Path:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:
            target.write_bytes(response.read())
    return target


parser = argparse.ArgumentParser()
parser.add_argument("--work-dir", type=pathlib.Path, default=ROOT / ".mcmeta-cache")
arguments = parser.parse_args()
work = arguments.work_dir

versions = json.loads(fetch(VERSIONS_URL, work / "versions.json").read_text())
releases = [
    v for v in versions if v.get("type") == "release" and (v.get("data_pack_version") or 0) >= 4
]
releases = list(reversed(releases))


def features(tree):
    children = tree["children"]
    out = set()
    for name in children:
        out.add(f"command:{name}")
    execute = children.get("execute", {}).get("children", {})
    for name in execute:
        out.add(f"execute:{name}")
    for name in execute.get("if", {}).get("children", {}):
        out.add(f"condition:{name}")
    store = execute.get("store", {}).get("children", {}).get("result", {}).get("children", {})
    for name in store:
        out.add(f"store:{name}")
    for name in children.get("schedule", {}).get("children", {}):
        out.add(f"schedule:{name}")
    for name in children.get("return", {}).get("children", {}):
        out.add(f"return:{name}")

    def walk(node, depth=0):
        if depth > 3:
            return
        for key, value in (node.get("children") or {}).items():
            yield key
            yield from walk(value, depth + 1)

    if "with" in set(walk(children.get("function", {}))):
        out.add("function:with")
    return out


since, until, previous, last = {}, {}, set(), None
rows = []
for release in releases:
    identifier = release["id"]
    tree = json.loads(
        fetch(
            COMMANDS_URL.format(version=identifier), work / "trees" / f"{identifier}.json"
        ).read_text()
    )
    current = features(tree)
    for name in sorted(current - previous):
        since.setdefault(name, identifier)
        until.pop(name, None)
    for name in sorted(previous - current):
        until[name] = identifier
    previous = current
    last = identifier
    rows.append(
        (
            identifier,
            release["data_pack_version"],
            release.get("data_pack_version_minor", 0),
            release["data_version"],
        )
    )

lines = [
    '"""Version table and per-version command availability.',
    "",
    "GENERATED FILE — do not edit by hand.  Rebuilt from misode/mcmeta:",
    "  versions:  https://raw.githubusercontent.com/misode/mcmeta/summary/versions/data.json",
    "  commands:  https://raw.githubusercontent.com/misode/mcmeta/<version>-summary/commands/data.json",
    f"Snapshot taken {datetime.date.today().isoformat()}; newest release covered: {last}.",
    "",
    "``RELEASES`` holds every release from 1.14 on (mcmeta carries no command tree",
    "for 1.13, which shares pack_format 4 with 1.14).  ``FEATURE_SINCE`` maps a",
    "feature key to the first release that has it, ``FEATURE_UNTIL`` to the first",
    "release that dropped it again.  Keys look like ``command:say``, ``execute:on``,",
    "``condition:items``, ``store:storage``, ``schedule:clear``, ``return:run`` and",
    "``function:with``.",
    '"""',
    "",
    "from __future__ import annotations",
    "",
    "#: (version id, data pack format, format minor, data version)",
    "RELEASES: tuple[tuple[str, int, int, int], ...] = (",
]
for row in rows:
    lines.append(f'    ("{row[0]}", {row[1]}, {row[2]}, {row[3]}),')
lines += [")", "", "FEATURE_SINCE: dict[str, str] = {"]
for name in sorted(since):
    lines.append(f'    "{name}": "{since[name]}",')
lines += ["}", "", "FEATURE_UNTIL: dict[str, str] = {"]
for name in sorted(until):
    lines.append(f'    "{name}": "{until[name]}",')
lines += ["}", ""]
OUT.write_text("\n".join(lines), encoding="utf-8")
print(OUT, len(rows), "releases,", len(since), "features,", len(until), "removals")
