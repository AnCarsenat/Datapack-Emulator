"""What can be typed next on a command line: command names, `execute`
subcommands and conditions, selector options, and the ids the pack (and the
client jar, when one is loaded) knows.

The source view's completion popup and ``datapack-emulator-cli complete``
both read this; nothing here touches Qt or runs a command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.runtime.library import SELECTOR_OPTIONS
from datapack_emulator.emulator.versions import Version

#: where a word being typed starts: these characters end the one before it
WORD_BREAKS = " \t[]{},=\"'"
#: commands whose first argument is an id of a registry the jar knows
#: command -> (registry, which word after the command name)
ID_ARGUMENTS: dict[str, tuple[str, int]] = {
    "summon": ("entity_type", 1),
    "setblock": ("block", 4),
    "give": ("item", 2),
    "clear": ("item", 2),
    "playsound": ("sound_event", 1),
    "particle": ("particle_type", 1),
    "effect": ("mob_effect", 3),
    "enchant": ("enchantment", 2),
}
#: commands whose argument is a function id (or a #tag of functions)
FUNCTION_ARGUMENTS = ("function", "schedule")


@dataclass(frozen=True)
class Candidate:
    """One thing that can be typed here."""

    #: the word itself, as it would be inserted
    text: str
    #: command, subcommand, condition, store, selector-option, function, tag, id
    kind: str
    #: a short hint: the version that added it, the registry it is in…
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.text}  ({self.kind}{': ' + self.detail if self.detail else ''})"


def word_at(line: str, cursor: int | None = None) -> tuple[str, int]:
    """The word being typed and where it starts (the cursor is the end of the
    line when it is not given)."""
    end = len(line) if cursor is None else max(0, min(cursor, len(line)))
    start = end
    while start > 0 and line[start - 1] not in WORD_BREAKS:
        start -= 1
    return line[start:end], start


def _feature_names(prefix: str, version: Version) -> list[tuple[str, str]]:
    """``(name, since)`` of every feature ``prefix:name`` this version has."""
    out = []
    for key in versions.feature_keys(prefix):
        if not versions.supports(version, key):
            continue
        since = versions.since_of(key)
        out.append((key.split(":", 1)[1], since.id if since else ""))
    return out


def _inside_selector(before: str) -> bool:
    """Whether the cursor is in a selector's ``[…]`` (unclosed since an ``@``)."""
    opened = before.rfind("[")
    if opened < 0 or "]" in before[opened:]:
        return False
    return "@" in before[:opened].split(" ")[-1]


def _expects_option_value(before: str) -> bool:
    """Right after ``option=``, a value is wanted, not another option."""
    stripped = before.rstrip()
    return stripped.endswith("=") or bool(re.search(r"=[^,\[\]]*$", before.split("[")[-1]))


def _tokens(before: str) -> list[str]:
    return [token for token in before.split(" ") if token]


def _executable(tokens: list[str]) -> bool:
    """Whether a whole command is expected next (the start, or after ``run``)."""
    if not tokens:
        return True
    return tokens[-1] in ("run",)


def complete(
    line: str,
    cursor: int | None = None,
    version: str | Version = versions.LATEST,
    view: Any | None = None,
    vanilla: Any | None = None,
    limit: int = 50,
) -> list[Candidate]:
    """What could be typed at ``cursor`` in ``line``, best first.

    ``view`` is a pack's :class:`PackView` (its functions and tags), ``vanilla``
    a :class:`VanillaAssets` (the game's ids).
    """
    parsed = versions.parse(version)
    word, start = word_at(line, cursor)
    before = line[:start]
    tokens = _tokens(before)
    candidates: list[Candidate] = []

    if _inside_selector(before):
        if not _expects_option_value(before):
            candidates = [
                Candidate(option, "selector-option") for option in sorted(SELECTOR_OPTIONS)
            ]
        elif view is not None and before.rstrip().endswith(("type=", "type=!")):
            candidates = _ids(view, vanilla, "entity_type")
    elif _executable(tokens):
        candidates = [
            Candidate(name, "command", _since(name, parsed))
            for name in sorted(versions.vanilla_commands(parsed))
        ]
    elif tokens[0] in FUNCTION_ARGUMENTS or tokens[-1] == "function":
        candidates = _functions(view)
    elif tokens[0] == "execute":
        candidates = _execute(tokens, parsed, view, vanilla)
    else:
        candidates = _arguments(tokens, view, vanilla)

    wanted = word.lower()
    matching = [item for item in candidates if item.text.lower().startswith(wanted)]
    if not matching and wanted:  # then anything that contains what was typed
        matching = [item for item in candidates if wanted in item.text.lower()]
    return matching[:limit]


def _since(name: str, version: Version) -> str:
    since = versions.since_of(f"command:{name}")
    return f"since {since.id}" if since and since <= version else ""


def _execute(
    tokens: list[str], version: Version, view: Any | None, vanilla: Any | None
) -> list[Candidate]:
    last = tokens[-1]
    if last in ("if", "unless"):
        return [
            Candidate(name, "condition", f"since {since}" if since else "")
            for name, since in sorted(_feature_names("condition", version))
        ]
    if last == "store" or (len(tokens) > 1 and tokens[-2] == "store"):
        if last == "store":
            return [Candidate(name, "store") for name in ("result", "success")]
        return [
            Candidate(name, "store", f"since {since}" if since else "")
            for name, since in sorted(_feature_names("store", version))
        ]
    return [
        Candidate(name, "subcommand", f"since {since}" if since else "")
        for name, since in sorted(_feature_names("execute", version))
    ] + [Candidate("run", "subcommand")]


def _functions(view: Any | None) -> list[Candidate]:
    if view is None:
        return []
    out = [Candidate(function_id, "function") for function_id in sorted(view.functions)]
    out += [
        Candidate(tag_id if tag_id.startswith("#") else f"#{tag_id}", "tag")
        for tag_id in sorted(view.function_tags)
    ]
    return out


def _ids(view: Any | None, vanilla: Any | None, registry: str) -> list[Candidate]:
    if vanilla is None:
        return []
    known = vanilla.registries.get(registry) or frozenset()
    return [Candidate(name, "id", registry) for name in sorted(known)]


def _arguments(tokens: list[str], view: Any | None, vanilla: Any | None) -> list[Candidate]:
    registry_and_place = ID_ARGUMENTS.get(tokens[0])
    if registry_and_place is None:
        return []
    registry, place = registry_and_place
    if len(tokens) != place:
        return []
    return _ids(view, vanilla, registry)
