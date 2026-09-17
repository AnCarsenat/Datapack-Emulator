"""Static diagnostics for a whole pack in one version: what the server
refuses to load, what it would fail on at run time, and what looks wrong.

Nothing runs. Each finding is a ``Problem`` with a severity:

* **error** — the server refuses it (a function or tag that does not load,
  an unknown selector option, SNBT or an id the version does not have, a
  JSON file that does not parse);
* **warning** — it loads but cannot work as written (a call to a function
  that does not exist, an unknown condition or item function in a JSON
  resource, a text component that does not parse, an advancement without
  criteria);
* **info** — worth a look (functions nothing calls, recursion).

The window's problems dock and ``datapack-emulator-cli check`` show them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.registry import command_set
from datapack_emulator.emulator.common import (
    load_text_component,
    normalise_id,
    normalise_tagged_id,
    parse_snbt,
)
from datapack_emulator.emulator.runtime.library import FunctionLibrary
from datapack_emulator.emulator.versions import Version

SEVERITIES = ("error", "warning", "info")

#: the options vanilla's entity selector parser knows (EntitySelectorOptions)
SELECTOR_OPTIONS = frozenset(
    {
        "name", "distance", "level", "x", "y", "z", "dx", "dy", "dz", "x_rotation",
        "y_rotation", "limit", "sort", "gamemode", "team", "type", "tag", "nbt",
        "scores", "advancements", "predicate",
    }
)  # fmt: skip

#: item functions vanilla has that the emulator does not list (it skips them)
OTHER_ITEM_FUNCTIONS = frozenset(
    {
        "minecraft:reference", "minecraft:set_book_cover", "minecraft:set_written_book_pages",
        "minecraft:set_writable_book_pages", "minecraft:toggle_tooltips",
        "minecraft:set_ominous_bottle_amount", "minecraft:set_custom_model_data",
        "minecraft:set_banner_pattern", "minecraft:set_fireworks",
        "minecraft:set_firework_explosion", "minecraft:set_instrument",
        "minecraft:modify_contents", "minecraft:set_random_dyes",
        "minecraft:set_random_potion", "minecraft:limit_count", "minecraft:copy_state",
        "minecraft:set_loot_table", "minecraft:discard",
    }
)  # fmt: skip

LOOT_ENTRY_TYPES = frozenset(
    {
        "minecraft:item", "minecraft:tag", "minecraft:loot_table", "minecraft:empty",
        "minecraft:dynamic", "minecraft:alternatives", "minecraft:group",
        "minecraft:sequence",
    }
)  # fmt: skip

#: commands whose SNBT argument is parsed when the function loads
NBT_COMMANDS = ("summon", "data", "give", "item", "clear", "setblock", "fill")


@dataclass(frozen=True)
class Problem:
    severity: str
    #: a short slug (``selector-option``, ``missing-function``, …)
    code: str
    message: str
    #: the function, tag or resource id ("" for the pack itself)
    resource: str = ""
    path: Path | None = None
    line: int = 0

    @property
    def where(self) -> str:
        return f"{self.resource}:{self.line}" if self.line else self.resource

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path) if self.path else ""
        return data

    def format(self) -> str:
        return f"{self.severity:7} {self.where or '-'}: {self.message} [{self.code}]"


def find_problems(
    datapack: Any, version: str | Version, vanilla: Any | None = None
) -> list[Problem]:
    """Every problem of ``datapack`` (a Datapack or DatapackSet) in
    ``version``, errors first; ``vanilla`` (VanillaAssets) lets ids be checked."""
    version = versions.parse(version)
    view = datapack.view_for(version)
    problems = list(_load_failures(view, version))
    problems += _function_lines(view, version, vanilla)
    problems += _json_resources(view)
    problems += _graph(view)
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    seen: set[Problem] = set()
    unique = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return sorted(unique, key=lambda p: (order[p.severity], p.resource, p.line, p.code))


def count(problems: list[Problem]) -> dict[str, int]:
    return {severity: sum(p.severity == severity for p in problems) for severity in SEVERITIES}


# ---------------------------------------------------------------------------
# functions and tags
# ---------------------------------------------------------------------------


def _load_failures(view: Any, version: Version) -> Iterator[Problem]:
    library = FunctionLibrary.build(view, command_set(version))
    for failure in library.failures:
        resource_id = failure.resource_id
        resource = (
            view.function_tags.get(resource_id)
            if resource_id.startswith("#")
            else view.functions.get(resource_id)
        )
        yield Problem(
            "error",
            "tag-not-loaded" if resource_id.startswith("#") else "function-not-loaded",
            failure.message + (f": {failure.detail}" if failure.detail else ""),
            resource_id,
            getattr(resource, "path", None),
            failure.line,
        )


def _function_lines(view: Any, version: Version, vanilla: Any | None) -> Iterator[Problem]:
    functions = view.functions
    for function_id, function in functions.items():
        for command in function.content:
            where = (function_id, function.path, command.line)
            if command.is_macro:
                continue  # filled in when called
            yield from _selectors(command, where)
            yield from _payloads(command, where)
            yield from _references(command, view, where)
            if vanilla is not None:
                yield from _ids(command, vanilla, where)


def _selectors(command: Command, where: tuple) -> Iterator[Problem]:
    for selector in command.selectors:
        for key in selector.arguments:
            if key not in SELECTOR_OPTIONS:
                yield Problem(
                    "error",
                    "selector-option",
                    f"unknown selector option '{key}' in {selector.raw}: the function does "
                    "not load",
                    *where,
                )


def _payloads(command: Command, where: tuple) -> Iterator[Problem]:
    for part in command.walk():
        if part.name in NBT_COMMANDS:
            for argument in part.arguments:
                text = argument.strip()
                if text.startswith("{") and text != "{}" and not parse_snbt(text):
                    yield Problem(
                        "error",
                        "snbt",
                        f"{part.name}: {text[:40]} is not valid SNBT: the function does not load",
                        *where,
                    )
                    break
        if part.name in ("tellraw", "title") and len(part.arguments) > 1:
            if part.name == "title" and part.arguments[1] in ("clear", "reset", "times"):
                continue
            start = 1 if part.name == "tellraw" else 2
            payload = " ".join(part.arguments[start:])
            if payload and load_text_component(payload) is None:
                yield Problem(
                    "warning",
                    "text-component",
                    f"{part.name}: the text is not a valid text component",
                    *where,
                )


def _references(command: Command, view: Any, where: tuple) -> Iterator[Problem]:
    for target, kind in command.calls():
        if target.startswith("#"):
            if target not in view.function_tags:
                yield Problem(
                    "warning",
                    "missing-tag",
                    f"{kind} of #{target.lstrip('#')}: no such function tag",
                    *where,
                )
        elif view.function(target) is None:
            yield Problem(
                "warning", "missing-function", f"{kind} of {target}: no such function", *where
            )


def _ids(command: Command, vanilla: Any, where: tuple) -> Iterator[Problem]:
    for part in command.walk():
        arguments = part.arguments
        checks: list[tuple[str, str]] = []
        if part.name == "summon" and arguments:
            checks.append(("entity_type", arguments[0]))
        if part.name in ("give", "clear") and len(arguments) > 1:
            checks.append(("item", arguments[1]))
        block_at = {"setblock": 3, "fill": 6}.get(part.name)
        if block_at is not None and len(arguments) > block_at:
            checks.append(("block", arguments[block_at]))
        for registry, raw in checks:
            if raw.startswith(("#", "*", "$")):
                continue
            resource = normalise_id(raw.split("[")[0].split("{")[0])
            if vanilla.knows(registry, resource) is False:
                yield Problem(
                    "error",
                    "unknown-id",
                    f"{part.name}: {registry.replace('_', ' ')} {resource} does not exist in "
                    f"{vanilla.version_id or 'this version'}",
                    *where,
                )


# ---------------------------------------------------------------------------
# JSON resources
# ---------------------------------------------------------------------------


def _resources(view: Any, registry: str) -> Iterator[Any]:
    yield from view.registries.get(registry, {}).values()


def _json_resources(view: Any) -> Iterator[Problem]:
    for registry, bucket in view.registries.items():
        for resource in bucket.values():
            if getattr(resource, "error", None):
                yield Problem(
                    "error",
                    "json",
                    f"{registry} file does not parse: {resource.error}",
                    resource.id,
                    resource.path,
                )
    for resource in _resources(view, "predicate"):
        yield from _conditions(resource.content, resource, "predicate")
    for resource in _resources(view, "item_modifier"):
        yield from _item_functions(resource.content, resource)
    for resource in _resources(view, "loot_table"):
        yield from _loot_table(resource.content, resource)
    advancements = {resource.id for resource in _resources(view, "advancement")}
    for resource in _resources(view, "advancement"):
        yield from _advancement(resource, advancements, view)
    for resource in _resources(view, "recipe"):
        content = resource.content
        if isinstance(content, dict) and not resource.error and "type" not in content:
            yield Problem("error", "recipe", "a recipe needs a type", resource.id, resource.path)


def _problem(resource: Any, severity: str, code: str, message: str) -> Problem:
    return Problem(severity, code, message, resource.id, resource.path)


def _conditions(value: Any, resource: Any, where: str) -> Iterator[Problem]:
    from datapack_emulator.emulator.runtime.predicates import CONDITIONS

    if isinstance(value, list):
        for item in value:
            yield from _conditions(item, resource, where)
        return
    if not isinstance(value, dict):
        if not getattr(resource, "error", None):
            yield _problem(resource, "error", "condition", f"{where}: a condition is an object")
        return
    kind = normalise_id(str(value.get("condition", "")))
    if kind not in CONDITIONS:
        yield _problem(
            resource, "warning", "condition", f"{where}: unknown condition type {kind or '(none)'}"
        )
    for key in ("term",):
        if key in value:
            yield from _conditions(value[key], resource, where)
    if isinstance(value.get("terms"), list):
        yield from _conditions(value["terms"], resource, where)


def _item_functions(value: Any, resource: Any, where: str = "item modifier") -> Iterator[Problem]:
    from datapack_emulator.emulator.runtime.loot import FUNCTIONS

    if isinstance(value, list):
        for item in value:
            yield from _item_functions(item, resource, where)
        return
    if not isinstance(value, dict):
        return
    name = normalise_id(str(value.get("function", "")))
    if name not in FUNCTIONS and name not in OTHER_ITEM_FUNCTIONS:
        yield _problem(
            resource,
            "warning",
            "item-function",
            f"{where}: unknown item function {name or '(none)'}",
        )
    if "conditions" in value:
        yield from _conditions(value["conditions"], resource, where)
    for key in ("functions", "modifier", "on_pass", "on_fail"):
        if key in value:
            yield from _item_functions(value[key], resource, where)


def _loot_table(content: Any, resource: Any) -> Iterator[Problem]:
    if not isinstance(content, dict):
        return
    pools = content.get("pools", [])
    if not isinstance(pools, list):
        yield _problem(resource, "error", "loot-table", "pools is not a list")
        return
    yield from _item_functions(content.get("functions", []), resource, "loot table")
    for number, pool in enumerate(pools, start=1):
        where = f"pool {number}"
        if not isinstance(pool, dict):
            yield _problem(resource, "error", "loot-table", f"{where} is not an object")
            continue
        if "rolls" not in pool:
            yield _problem(resource, "error", "loot-table", f"{where} has no rolls")
        yield from _conditions(pool.get("conditions", []), resource, where)
        yield from _item_functions(pool.get("functions", []), resource, where)
        yield from _entries(pool.get("entries"), resource, where)


def _entries(entries: Any, resource: Any, where: str) -> Iterator[Problem]:
    if not isinstance(entries, list):
        yield _problem(resource, "error", "loot-table", f"{where}: entries is not a list")
        return
    for entry in entries:
        if not isinstance(entry, dict):
            yield _problem(resource, "error", "loot-table", f"{where}: an entry is not an object")
            continue
        kind = normalise_id(str(entry.get("type", "")))
        if kind not in LOOT_ENTRY_TYPES:
            yield _problem(
                resource, "warning", "loot-entry", f"{where}: unknown entry type {kind or '(none)'}"
            )
        yield from _conditions(entry.get("conditions", []), resource, where)
        yield from _item_functions(entry.get("functions", []), resource, where)
        if "children" in entry:
            yield from _entries(entry["children"], resource, where)


def _advancement(resource: Any, known: set[str], view: Any) -> Iterator[Problem]:
    content = resource.content
    if not isinstance(content, dict) or resource.error:
        return
    criteria = content.get("criteria")
    if not isinstance(criteria, dict) or not criteria:
        yield _problem(resource, "warning", "advancement", "an advancement needs criteria")
    else:
        for name, criterion in criteria.items():
            if isinstance(criterion, dict) and "trigger" not in criterion:
                yield _problem(resource, "error", "advancement", f"criterion {name} has no trigger")
    parent = content.get("parent")
    if isinstance(parent, str):
        parent_id = normalise_id(parent)
        if parent_id not in known and not parent_id.startswith("minecraft:"):
            yield _problem(
                resource, "warning", "advancement", f"parent {parent_id} is not in the pack"
            )
    rewards = content.get("rewards")
    if isinstance(rewards, dict) and isinstance(rewards.get("function"), str):
        target = normalise_tagged_id(rewards["function"])
        if view.function(target) is None:
            yield _problem(
                resource, "warning", "missing-function", f"reward function {target} does not exist"
            )


# ---------------------------------------------------------------------------
# the call graph
# ---------------------------------------------------------------------------


def _graph(view: Any) -> Iterator[Problem]:
    graph = CallGraph.from_pack(view)
    functions = view.functions
    # advancement rewards are entry points too
    reached: set[str] = set()
    stack = [
        normalise_id(resource.content["rewards"]["function"])
        for resource in _resources(view, "advancement")
        if isinstance(resource.content, dict)
        and isinstance(resource.content.get("rewards"), dict)
        and isinstance(resource.content["rewards"].get("function"), str)
    ]
    while stack:
        node = stack.pop()
        if node not in reached:
            reached.add(node)
            stack.extend(graph.successors(node))
    for function_id in graph.unreachable():
        if function_id in reached or function_id not in functions:
            continue
        yield Problem(
            "info",
            "unused-function",
            "nothing calls it from #minecraft:load, #minecraft:tick or an advancement",
            function_id,
            functions[function_id].path,
        )
    for cycle in graph.cycles():
        first = cycle[0]
        function = functions.get(first)
        yield Problem(
            "info",
            "recursion",
            "calls itself: " + " -> ".join(cycle),
            first,
            getattr(function, "path", None),
        )
