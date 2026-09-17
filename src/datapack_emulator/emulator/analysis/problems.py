"""Static diagnostics for a whole pack in one version: what the server
refuses to load, what it would fail on at run time, and what looks wrong.

Nothing runs. Each finding is a ``Problem`` with a severity:

* **error** — the server refuses it: a function or tag that does not load
  (unknown commands, selector options, bad macro templates), an id the
  version does not have, a text component or JSON file that does not parse,
  a condition, item function, loot entry, advancement trigger or recipe type
  the version does not know, advancement criteria that do not add up;
* **warning** — it loads but cannot work as written, or may not load: a call
  to a function that does not exist, SNBT the emulator cannot read, a pack
  the version lists as incompatible or whose functions it does not read;
* **info** — worth a look: functions nothing calls, recursion.

Type ids are checked against ``registry_names`` (the vanilla registries of
every pack format). The window's problems dock and ``datapack-emulator-cli
check`` show the list.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.analysis.registry_names import COVERED_SINCE, NAMES
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

#: every kind of problem, for filters
CODES = (
    "pack",
    "function-not-loaded",
    "tag-not-loaded",
    "unknown-id",
    "text-component",
    "json",
    "condition",
    "item-function",
    "loot-table",
    "loot-entry",
    "advancement",
    "recipe",
    "snbt",
    "missing-function",
    "missing-tag",
    "unused-function",
    "recursion",
)

#: commands whose item or block argument carries SNBT, and where it sits
NBT_ARGUMENTS = {"give": (1,), "clear": (1,), "setblock": (3,), "fill": (6,)}
#: nested conditions and functions deeper than this are not followed
MAX_DEPTH = 64


@dataclass(frozen=True)
class Problem:
    severity: str
    #: one of ``CODES``
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


class _Scan:
    """What one check reads, computed once (views rebuild their dicts)."""

    def __init__(self, datapack: Any, version: Version, vanilla: Any | None):
        self.datapack = datapack
        self.version = version
        self.vanilla = vanilla
        self.view = datapack.view_for(version)
        self.functions = self.view.functions
        self.tags = self.view.function_tags
        self.registries = self.view.registries

    def resources(self, registry: str) -> list[Any]:
        return list(self.registries.get(registry, {}).values())

    def knows(self, registry: str, type_id: str) -> bool | None:
        """Whether the version has this type id (None: not checked)."""
        covered = COVERED_SINCE.get(registry)
        if covered is None or self.version < versions.parse(covered):
            return None
        namespace, _, path = type_id.partition(":")
        if namespace != "minecraft":
            return False
        entry = NAMES[registry].get(path)
        if entry is None:
            return False
        since, until = entry
        if since is not None and self.version < versions.parse(since):
            return False
        return until is None or self.version < versions.parse(until)


def find_problems(
    datapack: Any, version: str | Version, vanilla: Any | None = None
) -> list[Problem]:
    """Every problem of ``datapack`` (a Datapack or DatapackSet) in
    ``version``, errors first; ``vanilla`` (VanillaAssets) lets ids be checked."""
    scan = _Scan(datapack, versions.parse(version), vanilla)
    problems = list(_pack(scan))
    problems += _load_failures(scan)
    problems += _function_lines(scan)
    problems += _json_resources(scan)
    problems += _graph(scan)
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    unique = list(dict.fromkeys(problems))
    return sorted(unique, key=lambda p: (order[p.severity], p.resource, p.line, p.code))


def text_problems(text: str, suffix: str, version: str | Version) -> dict[int, str]:
    """What the version would refuse in one file's text, by line (1-based):
    function lines for ``.mcfunction``, the parse error for JSON. The source
    view underlines these as you type; ``check --lines`` prints them."""
    import json

    from datapack_emulator.emulator.runtime.library import line_problems

    suffix = suffix.lower()
    if suffix == ".mcfunction":
        return line_problems(text, command_set(versions.parse(version)))
    if suffix in (".json", ".mcmeta") and text.strip():
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            # an error at the end points past the text: mark the last line
            lines = text.splitlines()
            line = min(exc.lineno, len(lines))
            while line > 1 and not lines[line - 1].strip():
                line -= 1
            return {line: f"not valid JSON: {exc.msg} (line {exc.lineno})"}
    return {}


def count(problems: list[Problem]) -> dict[str, int]:
    return {severity: sum(p.severity == severity for p in problems) for severity in SEVERITIES}


# ---------------------------------------------------------------------------
# the pack
# ---------------------------------------------------------------------------


def _pack(scan: _Scan) -> Iterator[Problem]:
    version = scan.version
    compatibility = scan.datapack.compatibility(version)
    if not compatibility.compatible:
        yield Problem(
            "warning",
            "pack",
            f"{version.id} lists the pack as {compatibility.status.replace('_', ' ')} "
            f"({compatibility.reason}); it still loads",
        )
    if not scan.functions:
        try:
            written = scan.datapack.view().functions
        except Exception:  # a pack that cannot be read another way
            written = {}
        if written:
            yield Problem(
                "warning",
                "pack",
                f"{version.id} reads none of the pack's {len(written)} function(s): their "
                "folder is spelled for other versions (function/ from 1.21, functions/ before)",
            )


# ---------------------------------------------------------------------------
# functions and tags
# ---------------------------------------------------------------------------


def _load_failures(scan: _Scan) -> Iterator[Problem]:
    library = FunctionLibrary.build(scan.view, command_set(scan.version))
    for failure in library.failures:
        resource_id = failure.resource_id
        is_tag = resource_id.startswith("#")
        resource = scan.tags.get(resource_id) if is_tag else scan.functions.get(resource_id)
        yield Problem(
            "error",
            "tag-not-loaded" if is_tag else "function-not-loaded",
            failure.message + (f": {failure.detail}" if failure.detail else ""),
            resource_id,
            getattr(resource, "path", None),
            failure.line,
        )


def _function_lines(scan: _Scan) -> Iterator[Problem]:
    for function_id, function in scan.functions.items():
        for command in function.content:
            where = (function_id, function.path, command.line)
            if command.is_macro:
                continue  # the command is parsed when the function is called
            yield from _payloads(command, where)
            yield from _references(command, scan, where)
            if scan.vanilla is not None:
                yield from _ids(command, scan.vanilla, where)


def _snbt_problem(text: str) -> bool:
    body = text.strip()
    return body.replace(" ", "") != "{}" and not parse_snbt(body)


def _payloads(command: Command, where: tuple) -> Iterator[Problem]:
    for part in command.walk():
        arguments = part.arguments
        candidates: list[str] = []
        if part.name in ("summon", "data"):
            candidates = [argument for argument in arguments if argument.startswith("{")]
        for index in NBT_ARGUMENTS.get(part.name, ()):
            if len(arguments) > index and "{" in arguments[index]:
                candidates.append(arguments[index][arguments[index].index("{") :])
        for text in candidates[:1]:
            if _snbt_problem(text):
                yield Problem(
                    "warning",
                    "snbt",
                    f"{part.name}: the emulator cannot read {text[:40]}; if the game cannot "
                    "either, the function does not load",
                    *where,
                )
        if part.name in ("tellraw", "title") and len(arguments) > 1:
            if part.name == "title" and arguments[1] in ("clear", "reset", "times"):
                continue
            start = 1 if part.name == "tellraw" else 2
            payload = " ".join(arguments[start:])
            if payload and load_text_component(payload) is None:
                yield Problem(
                    "error",
                    "text-component",
                    f"{part.name}: the text is not a valid text component: the function does "
                    "not load",
                    *where,
                )


def _references(command: Command, scan: _Scan, where: tuple) -> Iterator[Problem]:
    for target, kind in command.calls():
        if target.startswith("#"):
            if target not in scan.tags:
                yield Problem(
                    "warning",
                    "missing-tag",
                    f"{kind} of #{target.lstrip('#')}: no such function tag",
                    *where,
                )
        elif normalise_id(target) not in scan.functions:
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


def _problem(resource: Any, severity: str, code: str, message: str) -> Problem:
    return Problem(severity, code, message, resource.id, resource.path)


def _json_resources(scan: _Scan) -> Iterator[Problem]:
    for registry, bucket in scan.registries.items():
        for resource in bucket.values():
            if getattr(resource, "error", None):
                yield Problem(
                    "error",
                    "json",
                    f"{registry} file does not parse: {resource.error}",
                    resource.id,
                    resource.path,
                )
    for resource in scan.resources("tags/function"):
        if not resource.error and not isinstance(resource.content, dict):
            yield _problem(resource, "error", "json", "a tag file holds an object")
    for resource in scan.resources("predicate"):
        if not resource.error:
            yield from _conditions(resource.content, resource, "predicate", scan)
    for resource in scan.resources("item_modifier"):
        if resource.error:
            continue
        if not isinstance(resource.content, (dict, list)):
            yield _problem(resource, "error", "item-function", "an item modifier is an object")
        else:
            yield from _item_functions(resource.content, resource, "item modifier", scan)
    for resource in scan.resources("loot_table"):
        if not resource.error:
            yield from _loot_table(resource, scan)
    known = {resource.id for resource in scan.resources("advancement")}
    for resource in scan.resources("advancement"):
        if not resource.error:
            yield from _advancement(resource, known, scan)
    for resource in scan.resources("recipe"):
        if not resource.error:
            yield from _recipe(resource, scan)


def _type_id(value: Any) -> str | None:
    return normalise_id(value) if isinstance(value, str) and value else None


def _conditions(
    value: Any, resource: Any, where: str, scan: _Scan, depth: int = 0
) -> Iterator[Problem]:
    if depth > MAX_DEPTH:
        return
    if isinstance(value, list):
        for item in value:
            yield from _conditions(item, resource, where, scan, depth + 1)
        return
    if not isinstance(value, dict):
        yield _problem(resource, "error", "condition", f"{where}: a condition is an object")
        return
    kind = _type_id(value.get("condition"))
    if kind is None:
        yield _problem(resource, "error", "condition", f"{where}: condition needs a type id")
    elif scan.knows("loot_condition_type", kind) is False:
        yield _problem(
            resource,
            "error",
            "condition",
            f"{where}: {scan.version.id} has no condition type {kind}: the file does not load",
        )
    if "term" in value:
        yield from _conditions(value["term"], resource, where, scan, depth + 1)
    if isinstance(value.get("terms"), list):
        yield from _conditions(value["terms"], resource, where, scan, depth + 1)


def _item_functions(
    value: Any, resource: Any, where: str, scan: _Scan, depth: int = 0
) -> Iterator[Problem]:
    if depth > MAX_DEPTH:
        return
    if isinstance(value, list):
        for item in value:
            yield from _item_functions(item, resource, where, scan, depth + 1)
        return
    if not isinstance(value, dict):
        yield _problem(
            resource, "error", "item-function", f"{where}: an item function is an object"
        )
        return
    name = _type_id(value.get("function"))
    if name is None:
        yield _problem(resource, "error", "item-function", f"{where}: function needs a type id")
    elif scan.knows("loot_function_type", name) is False:
        yield _problem(
            resource,
            "error",
            "item-function",
            f"{where}: {scan.version.id} has no item function {name}: the file does not load",
        )
    if "conditions" in value:
        yield from _conditions(value["conditions"], resource, where, scan, depth + 1)
    for key in ("functions", "modifier", "on_pass", "on_fail"):
        if key in value:
            yield from _item_functions(value[key], resource, where, scan, depth + 1)


def _loot_table(resource: Any, scan: _Scan) -> Iterator[Problem]:
    content = resource.content
    if not isinstance(content, dict):
        yield _problem(resource, "error", "loot-table", "a loot table is an object")
        return
    pools = content.get("pools", [])
    if not isinstance(pools, list):
        yield _problem(resource, "error", "loot-table", "pools is not a list")
        return
    yield from _item_functions(content.get("functions", []), resource, "loot table", scan)
    for number, pool in enumerate(pools, start=1):
        where = f"pool {number}"
        if not isinstance(pool, dict):
            yield _problem(resource, "error", "loot-table", f"{where} is not an object")
            continue
        if "rolls" not in pool:
            yield _problem(resource, "error", "loot-table", f"{where} has no rolls")
        yield from _conditions(pool.get("conditions", []), resource, where, scan)
        yield from _item_functions(pool.get("functions", []), resource, where, scan)
        yield from _entries(pool.get("entries"), resource, where, scan)


def _entries(
    entries: Any, resource: Any, where: str, scan: _Scan, depth: int = 0
) -> Iterator[Problem]:
    if depth > MAX_DEPTH:
        return
    if not isinstance(entries, list):
        yield _problem(resource, "error", "loot-table", f"{where}: entries is not a list")
        return
    for entry in entries:
        if not isinstance(entry, dict):
            yield _problem(resource, "error", "loot-table", f"{where}: an entry is not an object")
            continue
        kind = _type_id(entry.get("type"))
        if kind is None:
            yield _problem(resource, "error", "loot-entry", f"{where}: an entry needs a type id")
        elif scan.knows("loot_pool_entry_type", kind) is False:
            yield _problem(
                resource,
                "error",
                "loot-entry",
                f"{where}: {scan.version.id} has no loot entry type {kind}: the file does not load",
            )
        yield from _conditions(entry.get("conditions", []), resource, where, scan, depth + 1)
        yield from _item_functions(entry.get("functions", []), resource, where, scan, depth + 1)
        if "children" in entry:
            yield from _entries(entry["children"], resource, where, scan, depth + 1)


def _advancement(resource: Any, known: set[str], scan: _Scan) -> Iterator[Problem]:
    content = resource.content
    if not isinstance(content, dict):
        yield _problem(resource, "error", "advancement", "an advancement is an object")
        return
    criteria = content.get("criteria")
    if not isinstance(criteria, dict) or not criteria:
        yield _problem(
            resource, "error", "advancement", "an advancement needs criteria: it does not load"
        )
        criteria = {}
    for name, criterion in criteria.items():
        if not isinstance(criterion, dict):
            yield _problem(resource, "error", "advancement", f"criterion {name} is not an object")
            continue
        trigger = _type_id(criterion.get("trigger"))
        if trigger is None:
            yield _problem(resource, "error", "advancement", f"criterion {name} has no trigger")
        elif scan.knows("trigger_type", trigger) is False:
            yield _problem(
                resource,
                "error",
                "advancement",
                f"criterion {name}: {scan.version.id} has no trigger {trigger}",
            )
    requirements = content.get("requirements")
    if isinstance(requirements, list) and requirements and criteria:
        named = {
            str(name)
            for group in requirements
            for name in (group if isinstance(group, list) else [group])
        }
        for name in sorted(named - set(criteria)):
            yield _problem(
                resource, "error", "advancement", f"requirements name an unknown criterion {name}"
            )
        for name in sorted(set(criteria) - named):
            yield _problem(
                resource,
                "error",
                "advancement",
                f"criterion {name} is not in the requirements: the advancement does not load",
            )
    parent = content.get("parent")
    if isinstance(parent, str):
        parent_id = normalise_id(parent)
        if parent_id not in known and not parent_id.startswith("minecraft:"):
            yield _problem(
                resource, "warning", "advancement", f"parent {parent_id} is not in the pack"
            )
    rewards = content.get("rewards")
    if isinstance(rewards, dict) and isinstance(rewards.get("function"), str):
        target = rewards["function"].strip()
        if target.startswith("#"):
            yield _problem(
                resource,
                "error",
                "advancement",
                f"reward function {target} is a tag; a reward names one function",
            )
        elif normalise_tagged_id(target) not in scan.functions:
            yield _problem(
                resource,
                "warning",
                "missing-function",
                f"reward function {normalise_tagged_id(target)} does not exist",
            )


def _recipe(resource: Any, scan: _Scan) -> Iterator[Problem]:
    content = resource.content
    if not isinstance(content, dict):
        yield _problem(resource, "error", "recipe", "a recipe is an object")
        return
    kind = _type_id(content.get("type"))
    if kind is None:
        yield _problem(resource, "error", "recipe", "a recipe needs a type")
    elif scan.knows("recipe_serializer", kind) is False:
        yield _problem(resource, "error", "recipe", f"{scan.version.id} has no recipe type {kind}")


# ---------------------------------------------------------------------------
# the call graph
# ---------------------------------------------------------------------------


def _run_function_effects(value: Any, found: list[str], depth: int = 0) -> None:
    """Functions an enchantment runs (its ``minecraft:run_function`` effects)."""
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        if _type_id(value.get("type")) == "minecraft:run_function" and isinstance(
            value.get("function"), str
        ):
            found.append(normalise_id(value["function"]))
        for item in value.values():
            _run_function_effects(item, found, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _run_function_effects(item, found, depth + 1)


def _graph(scan: _Scan) -> Iterator[Problem]:
    graph = CallGraph.from_pack(scan.view)
    functions = scan.functions
    # advancement rewards and enchantment effects are entry points too
    stack: list[str] = []
    for resource in scan.resources("advancement"):
        rewards = resource.content.get("rewards") if isinstance(resource.content, dict) else None
        if isinstance(rewards, dict) and isinstance(rewards.get("function"), str):
            stack.append(normalise_id(rewards["function"]))
    for resource in scan.resources("enchantment"):
        _run_function_effects(getattr(resource, "content", None), stack)
    reached: set[str] = set()
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
            "nothing in the pack calls it (load and tick tags, other tags, functions, "
            "schedules, advancement rewards, enchantments)",
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
