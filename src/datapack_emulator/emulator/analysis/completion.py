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
WORD_BREAKS = " \t[]{},=!\"'"
#: what a line may start with and still be a command: `/say hi`, `$say $(x)`
LINE_PREFIXES = ("/", "$")
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
#: what `schedule` can be followed by
SCHEDULE_SUBCOMMANDS = ("clear", "function")
#: selector options the game gained after the oldest version gating covers
SELECTOR_SINCE = {"predicate": "1.15"}
#: how many words an `execute` subcommand takes before another one may start
#: (its own arguments; only the fixed-length ones are listed)
SUBCOMMAND_WORDS = {
    "as": 1,
    "at": 1,
    "in": 1,
    "on": 1,
    "anchored": 1,
    "align": 1,
    "positioned": 3,
    "rotated": 2,
    "summon": 1,
}


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
    if start == 0 and end > 0 and line[:1] in LINE_PREFIXES:
        start = 1  # `/` and a macro's `$` are not part of the command name
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
    body = before
    if body[:1] in LINE_PREFIXES:
        body = body[1:]
    return body.split()  # any whitespace: a tab separates words too


def _in_quotes(before: str) -> bool:
    """Whether the cursor is inside a string: nothing here is a command."""
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _wants_function(tokens: list[str]) -> bool:
    """Whether a function id (or a #tag) is what comes next."""
    if tokens[-1] == "function":  # `function `, `schedule function `, `run function `
        return True
    return len(tokens) > 1 and tokens[-2:] == ["schedule", "clear"]


def _executable(tokens: list[str]) -> bool:
    """Whether a whole command is expected next: the start of the line, or
    after an ``execute`` chain's ``run`` (the word ``run`` in a message is
    not one)."""
    if not tokens:
        return True
    return tokens[-1] == "run" and tokens[0] == "execute"


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

    if _in_quotes(before) or before.lstrip().startswith("#"):
        return []  # inside a string, or a comment: nothing here is a command
    if _inside_selector(before):
        if not _expects_option_value(before):
            candidates = [
                Candidate(option, "selector-option", _option_since(option, parsed))
                for option in sorted(SELECTOR_OPTIONS)
                if _has_option(option, parsed)
            ]
        elif before.rstrip().endswith(("type=", "type=!")):
            candidates = _ids(vanilla, "entity_type")
    elif _executable(tokens):
        candidates = [
            Candidate(name, "command", _since(name))
            for name in sorted(versions.vanilla_commands(parsed))
        ]
    elif _wants_function(tokens):
        candidates = _functions(view)
    elif tokens == ["schedule"]:
        candidates = [Candidate(name, "subcommand") for name in SCHEDULE_SUBCOMMANDS]
    elif tokens[0] == "execute":
        candidates = _execute(tokens, parsed, vanilla)
    else:
        candidates = _arguments(tokens, vanilla)

    wanted = word.lower()
    matching = [item for item in candidates if item.text.lower().startswith(wanted)]
    if not matching and wanted:  # then anything that contains what was typed
        matching = [item for item in candidates if wanted in item.text.lower()]
    return matching[:limit]


def _since(name: str) -> str:
    since = versions.since_of(f"command:{name}")
    return f"since {since.id}" if since else ""


def _has_option(option: str, version: Version) -> bool:
    since = SELECTOR_SINCE.get(option)
    return since is None or versions.parse(since) <= version


def _option_since(option: str, version: Version) -> str:
    since = SELECTOR_SINCE.get(option)
    return f"since {since}" if since and versions.parse(since) <= version else ""


def _execute(tokens: list[str], version: Version, vanilla: Any | None) -> list[Candidate]:
    last = tokens[-1]
    names = dict(_feature_names("execute", version))
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
    if _in_arguments(tokens, names):  # a subcommand's own arguments come first
        return []
    return [
        Candidate(name, "subcommand", f"since {since}" if since else "")
        for name, since in sorted(names.items())
    ]


def _in_arguments(tokens: list[str], names: dict[str, str]) -> bool:
    """Whether the words typed so far are still one subcommand's arguments."""
    for index in range(len(tokens) - 1, 0, -1):
        word = tokens[index]
        if word not in names:
            continue
        wanted = SUBCOMMAND_WORDS.get(word)
        if wanted is None:  # if/unless/store/run: their own branches say
            return False
        return len(tokens) - index - 1 < wanted
    return False


def _functions(view: Any | None) -> list[Candidate]:
    if view is None:
        return []
    out = [Candidate(function_id, "function") for function_id in sorted(view.functions)]
    out += [
        Candidate(tag_id if tag_id.startswith("#") else f"#{tag_id}", "tag")
        for tag_id in sorted(view.function_tags)
    ]
    return out


def _ids(vanilla: Any | None, registry: str) -> list[Candidate]:
    if vanilla is None:
        return []
    known = vanilla.registries.get(registry) or frozenset()
    return [Candidate(name, "id", registry) for name in sorted(known)]


def _arguments(tokens: list[str], vanilla: Any | None) -> list[Candidate]:
    registry_and_place = ID_ARGUMENTS.get(tokens[0])
    if registry_and_place is None:
        return []
    registry, place = registry_and_place
    if len(tokens) != place:
        return []
    return _ids(vanilla, registry)
