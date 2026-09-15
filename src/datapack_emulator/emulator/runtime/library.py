"""What the server actually loads from a pack: its function library.

A pack view lists every ``.mcfunction`` and function tag on disk; a server of a
given version does not load all of them. These rules were checked by a peer
session in decompiled Mojang jars (1.16.1 to 26.3-rc-3):

* Each function is compiled on its own. If any line does not parse in that
  version, the **whole function is dropped** ("Failed to load function <id>")
  and every other function still loads. ``$`` macro lines are only parsed when
  the function is called, so they never fail the load.
* ``function <id>`` resolves lazily when it runs, so calling a function that
  failed to load does not break the caller's load.
* A function **tag** with any missing required entry — a function or tag that
  does not exist or failed to load — is **dropped entirely** ("Couldn't load
  tag <id> as it is missing following references: ..."). Entries written as
  ``{"id": ..., "required": false}`` are skipped silently from 1.16.2; 1.16.1
  cannot read object entries at all, so such a tag file fails to read.
"""

from __future__ import annotations

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
            failure = _parse_failure(function, commands)
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
            if not optional_allowed and any(entry.written_as_object for entry in entries):
                self.tag_failures[tag_id] = LoadFailure(
                    tag_id,
                    f"Couldn't read tag list {tag_id[1:]}",
                    detail=f"{self.version.id} only accepts plain strings in tag values",
                )
                resolved[tag_id] = None
                return None
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


def _parse_failure(function: Function, commands: CommandSet) -> LoadFailure | None:
    """The first line of ``function`` this version cannot parse, if any."""
    for command in function.content:
        if command.is_macro:
            continue  # macro lines are parsed when the function is called
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
