"""What the server actually loads from a pack: its function library.

A pack view lists every ``.mcfunction`` and function tag on disk; a server of a
given version does not load all of them. These rules were checked by a peer
session in decompiled Mojang jars (1.16.1 to 26.3-rc-3):

* Each function is compiled on its own. If any line does not parse in that
  version, the **whole function is dropped** ("Failed to load function <id>")
  and every other function still loads. From 1.20.2, ``$`` macro lines are only
  parsed when the function is called, so they never fail the load; before
  that, a ``$`` line is an unknown command like any other.
* ``function <id>`` resolves lazily when it runs, so calling a function that
  failed to load does not break the caller's load.
* A function **tag** with any missing required entry — a function or tag that
  does not exist or failed to load — is **dropped entirely** ("Couldn't load
  tag <id> as it is missing following references: ..."). Entries written as
  ``{"id": ..., "required": false}`` are skipped silently from 1.16.2; 1.16.1
  cannot read object entries at all, so such a tag file fails to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.registry import CommandSet
from datapack_emulator.emulator.common import normalise_id, normalise_tagged_id
from datapack_emulator.emulator.datapack import PackView
from datapack_emulator.emulator.resources import Function, Tag
from datapack_emulator.emulator.versions import Version

#: the first release whose tag loader accepts ``{"id", "required"}`` entries
OPTIONAL_TAG_ENTRIES_SINCE = "1.16.2"


@dataclass(frozen=True)
class LoadFailure:
    """Why the server refused a function or a tag, in its own words."""

    resource_id: str
    message: str
    detail: str = ""
    line: int = 0
    key: str = ""


@dataclass
class FunctionLibrary:
    version: Version
    functions: dict[str, Function] = field(default_factory=dict)
    #: tag id (``#ns:path``) -> function ids, in tag order
    tags: dict[str, list[str]] = field(default_factory=dict)
    function_failures: dict[str, LoadFailure] = field(default_factory=dict)
    tag_failures: dict[str, LoadFailure] = field(default_factory=dict)

    # -- building ---------------------------------------------------------

    @classmethod
    def build(cls, pack: PackView, commands: CommandSet) -> FunctionLibrary:
        library = cls(version=commands.version)
        for function_id, function in sorted(pack.functions.items()):
            failure = _parse_failure(function, commands, versions.supports_macros(library.version))
            if failure is None:
                library.functions[function_id] = function
            else:
                library.function_failures[function_id] = failure
        library._build_tags(pack.function_tags)
        return library

    def _build_tags(self, raw_tags: dict[str, Tag]) -> None:
        optional_allowed = self.version >= versions.parse(OPTIONAL_TAG_ENTRIES_SINCE)
        resolved: dict[str, list[str] | None] = {}

        def resolve(tag_id: str, stack: tuple[str, ...]) -> list[str] | None:
            if tag_id in resolved:
                return resolved[tag_id]
            tag = raw_tags.get(tag_id)
            if tag is None or tag_id in stack:  # missing or cyclic
                return None
            entries = tag.entries
            if not optional_allowed:
                # each file is read on its own: an unreadable one is skipped, the
                # files of other packs still count (a merged tag keeps them all)
                readable = []
                for source in getattr(tag, "sources", [tag]):
                    if any(entry.written_as_object for entry in source.entries):
                        self.tag_failures[tag_id] = LoadFailure(
                            tag_id,
                            f"Couldn't read tag list {tag_id[1:]}",
                            detail=f"{self.version.id} only accepts plain strings in tag values",
                        )
                    else:
                        readable.append(source)
                if not readable:
                    resolved[tag_id] = None
                    return None
                entries = []
                for source in readable:
                    entries = list(source.entries) if source.replace else entries + source.entries
            members: list[str] = []
            missing: list[str] = []
            for entry in entries:
                if entry.value.startswith("#"):
                    nested = resolve(normalise_tagged_id(entry.value), (*stack, tag_id))
                    if nested is None:
                        if entry.required:
                            missing.append(entry.value)
                        continue
                    members.extend(member for member in nested if member not in members)
                else:
                    function_id = normalise_id(entry.value)
                    if function_id not in self.functions:
                        if entry.required:
                            missing.append(entry.value)
                        continue
                    if function_id not in members:
                        members.append(function_id)
            if missing:
                self.tag_failures[tag_id] = LoadFailure(
                    tag_id,
                    f"Couldn't load tag {tag_id[1:]} as it is missing following references: "
                    + ", ".join(missing),
                )
                resolved[tag_id] = None
                return None
            resolved[tag_id] = members
            return members

        for tag_id in sorted(raw_tags):
            members = resolve(tag_id, ())
            if members is not None:
                self.tags[tag_id] = members

    # -- queries ----------------------------------------------------------

    def function(self, function_id: str) -> Function | None:
        return self.functions.get(normalise_id(function_id))

    def resolve_tag(self, tag_id: str) -> list[str]:
        return list(self.tags.get(normalise_tagged_id(tag_id), []))

    @property
    def failures(self) -> list[LoadFailure]:
        return [*self.function_failures.values(), *self.tag_failures.values()]


#: the options vanilla's entity selector parser knows (EntitySelectorOptions,
#: the same from 1.16 to 26.x)
SELECTOR_OPTIONS = frozenset(
    {
        "name", "distance", "level", "x", "y", "z", "dx", "dy", "dz", "x_rotation",
        "y_rotation", "limit", "sort", "gamemode", "team", "type", "tag", "nbt",
        "scores", "advancements", "predicate",
    }
)  # fmt: skip
_MACRO_NAME = re.compile(r"[A-Za-z0-9_]+")


def unknown_selector_option(command) -> str:
    """The first selector option the game does not know ("" when none)."""
    for selector in command.selectors:
        for key in selector.arguments:
            if key not in SELECTOR_OPTIONS:
                return key
    return ""


def macro_template_problem(raw: str) -> str:
    """Why a ``$`` line is not a macro template ("" when it is): it needs at
    least one ``$(name)``, closed, with a name of letters, digits and ``_``."""
    body = raw.strip()[1:]
    names = []
    index = 0
    while True:
        start = body.find("$(", index)
        if start < 0:
            break
        end = body.find(")", start + 2)
        if end < 0:
            return "Unterminated macro variable"
        name = body[start + 2 : end]
        if not _MACRO_NAME.fullmatch(name):
            return f"Invalid macro variable name '{name}'"
        names.append(name)
        index = end + 1
    return "" if names else "No variables in macro"


def line_problems(text: str, commands: CommandSet) -> dict[int, str]:
    """Every line of function text this version would refuse, by line number
    (1-based) — what the source view underlines while you type."""
    from datapack_emulator.emulator.commands.parser import Command

    macros = versions.supports_macros(commands.version)
    found: dict[int, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        command = Command.parse(raw, line=number)
        if command is None:
            continue
        if command.is_macro:
            if not macros:
                found[number] = (
                    f"macro lines need {versions.MACROS_SINCE}: in {commands.version.id} this "
                    "is an unknown command"
                )
            else:
                problem = macro_template_problem(command.raw)
                if problem:
                    found[number] = problem
            continue
        option = unknown_selector_option(command)
        if option:
            found[number] = f"Unknown option '{option}'"
            continue
        missing = commands.missing_features(command.features())
        if missing:
            feature, since = missing[0]
            found[number] = f"{commands.version.id} has no {feature}" + (
                f" (added in {since.id})" if since else ""
            )
    return found


def _parse_failure(
    function: Function, commands: CommandSet, macros_supported: bool
) -> LoadFailure | None:
    """The first line of ``function`` this version cannot parse, if any."""
    for command in function.content:
        if command.is_macro and macros_supported:
            # the command is parsed when the function is called, but the
            # template itself is read now
            problem = macro_template_problem(command.raw)
            if problem:
                return LoadFailure(
                    function.id,
                    f"Failed to load function {function.id}",
                    detail=f"Whilst parsing command on line {command.line}: {problem}",
                    line=command.line,
                )
            continue
        if command.is_macro:  # before 1.20.2 a `$` line is just an unknown command
            return LoadFailure(
                function.id,
                f"Failed to load function {function.id}",
                detail=(
                    f"Whilst parsing command on line {command.line}: "
                    "Unknown or incomplete command, see below for error"
                ),
                line=command.line,
                key="command.unknown.command",
            )
        option = unknown_selector_option(command)
        if option:
            return LoadFailure(
                function.id,
                f"Failed to load function {function.id}",
                detail=f"Whilst parsing command on line {command.line}: Unknown option '{option}'",
                line=command.line,
                key="argument.entity.options.unknown",
            )
        for feature, _since in commands.missing_features(command.features()):
            unknown_command = feature.startswith("command:")
            key = "command.unknown.command" if unknown_command else "command.unknown.argument"
            reason = (
                "Unknown or incomplete command, see below for error"
                if unknown_command
                else "Incorrect argument for command"
            )
            return LoadFailure(
                function.id,
                f"Failed to load function {function.id}",
                detail=f"Whilst parsing command on line {command.line}: {reason}",
                line=command.line,
                key=key,
            )
    return None
