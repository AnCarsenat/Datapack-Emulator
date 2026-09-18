"""What a JSON resource may hold, learned from the version's own files.

The game ships its vanilla data pack inside ``client.jar``: every recipe,
loot table, predicate, item modifier and worldgen file the version itself
loads. Reading them says, for that exact version, which fields each kind of
file has, which are always there, and what each one holds — so a pack's JSON
can be checked against the version instead of against a hand-written list.

A schema is learned once per jar and kept beside it (``schemas/<version>.json``
in the jar cache), because reading a jar's data folder takes a second.

What it can say and what it cannot: a field no vanilla file uses is reported
as unknown *to that version's own files*, which is a warning, not an error —
the game may still accept it. A field every vanilla file of that type has is
reported as missing when a pack leaves it out, and a field whose value is
never of that kind in any vanilla file is reported as the wrong kind. Nothing
here needs the network, and nothing is claimed when no jar is loaded.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datapack_emulator.emulator.common import normalise_id

log = logging.getLogger(__name__)

#: the format of a written schema file, so an older one is not read wrongly
FORMAT = 1
KIND = "datapack-emulator-schema"
#: how deep a shape is learned (loot tables nest entries inside entries)
MAX_DEPTH = 12
#: how deep the shapes themselves are walked (a shape's variants and items are
#: levels of their own, so this is larger than what was learned)
WALK_DEPTH = 40
#: how many vanilla files a type needs before "every file has it" means anything
ENOUGH = 3
#: the keys a JSON object dispatches on: its shape depends on this id
DISPATCH_KEYS = ("type", "condition", "function")
#: the folders learned, as a pack writes them (both spellings are read)
LEARNED = (
    "recipe",
    "loot_table",
    "predicate",
    "item_modifier",
    "advancement",
    "damage_type",
    "enchantment",
    "dimension_type",
    "banner_pattern",
    "chat_type",
    "dialog",
    "instrument",
    "jukebox_song",
    "painting_variant",
    "trim_material",
    "trim_pattern",
    "worldgen/configured_feature",
    "worldgen/placed_feature",
    "worldgen/biome",
    "worldgen/noise_settings",
    "worldgen/structure",
    "worldgen/template_pool",
)
#: a bound for a pathological jar; vanilla's biggest folder is ~1400 files,
#: so every file is normally read (reading only some would make "the version
#: never writes this" a claim about the half that happened to be read)
PER_FOLDER = 20000
#: fields whose names the pack chooses: a criterion, a recipe key letter, a
#: mob in spawn costs. Their names say nothing, only their values do.
MAP_FIELDS = frozenset(
    {
        "criteria",
        "key",
        "spawn_costs",
        "effects",
        "spawners",
        "carvers",
        "features",
        "properties",
        "requirements",
    }
)
#: how many different names a field must hold before it is read as a map
MAP_NAMES = 8
#: how many files must agree before "it is always this kind" is said at all
KIND_EVIDENCE = 20
#: how many of the objects in one place must carry the dispatch key for the
#: place to be read as dispatching (an entity predicate's `type` is an entity
#: id, not a kind of object, and most of them have no `type` at all)
DISPATCH_SHARE = 0.9
#: where a `condition` or `function` object can be: a `type` dispatches
#: anywhere, but `{"function": "ns:id"}` is also an advancement's reward
DISPATCH_PARENTS = {
    "condition": ("conditions", "term", "terms", "predicate"),
    "function": ("functions", "modifier", "modifiers"),
}


def _mostly_once(fields: dict[str, Shape]) -> bool:
    """Whether most of these names were written by one file only: then they
    are names the pack chose, not fields of a schema."""
    once = sum(1 for shape in fields.values() if shape.seen <= 1)
    return once * 2 > len(fields)


def kind_of(value: Any) -> str:
    """What a JSON value is, in one word."""
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "an object"


@dataclass
class Shape:
    """What one place in a file held, across every vanilla file read."""

    #: how many values reached this place
    seen: int = 0
    #: the kinds it held (``kind_of``)
    kinds: set[str] = field(default_factory=set)
    #: an object's fields
    fields: dict[str, Shape] = field(default_factory=dict)
    #: a list's items, as one shape
    items: Shape | None = None
    #: the key this object dispatches on ("type", "condition", "function")
    dispatch: str = ""
    #: that key's id -> the shape of an object of that kind
    variants: dict[str, Shape] = field(default_factory=dict)
    #: whether learning stopped here (too deep): nothing below it is known
    truncated: bool = False
    #: whether the field names here are the pack's own (a criterion, a recipe
    #: key letter): then only their values are worth checking
    named_by_the_pack: bool = False

    # -- learning ----------------------------------------------------------

    def learn(self, value: Any, depth: int = 0, parent: str = "") -> None:
        self.seen += 1
        self.kinds.add(kind_of(value))
        if depth >= MAX_DEPTH:
            self.truncated = True  # nothing below this is known, so nothing is claimed
            return
        if isinstance(value, dict):
            key = _dispatch_key(value, parent)
            if key:
                self.dispatch = key
                variant = self.variants.setdefault(normalise_id(str(value[key])), Shape())
                variant.seen += 1
                variant.kinds.add("an object")
                variant._learn_fields(value, depth)
            else:
                self._learn_fields(value, depth)
        elif isinstance(value, list):
            for item in value:
                if self.items is None:
                    self.items = Shape()
                self.items.learn(item, depth + 1, parent)

    def _learn_fields(self, value: dict[str, Any], depth: int) -> None:
        for name, held in value.items():
            self.fields.setdefault(name, Shape()).learn(held, depth + 1, name)

    def settle(self, name: str = "") -> None:
        """After learning: drop a dispatch the objects there mostly do not
        carry, and mark the places whose field names the pack chooses, so
        their names are not read as a list of what may be written."""
        if self.dispatch and self.variants:
            dispatched = sum(shape.seen for shape in self.variants.values())
            if dispatched < DISPATCH_SHARE * self.seen:
                # most of them are plain objects: this was never a kind
                for shape in self.variants.values():
                    for field_name, held in shape.fields.items():
                        self.fields.setdefault(field_name, Shape()).merge(held)
                self.dispatch = ""
                self.variants = {}
        if self.fields and (
            name in MAP_FIELDS or (len(self.fields) >= MAP_NAMES and _mostly_once(self.fields))
        ):
            self.named_by_the_pack = True
        for field_name, shape in self.fields.items():
            shape.settle(field_name)
        if self.items is not None:
            self.items.settle(name)
        for shape in self.variants.values():
            shape.settle(name)

    def values_shape(self) -> Shape:
        """One shape for every value of a map (its keys are the pack's own)."""
        merged = Shape()
        for shape in self.fields.values():
            merged.merge(shape)
        return merged

    def merge(self, other: Shape) -> None:
        """Fold another shape of the same place into this one."""
        self.seen += other.seen
        self.kinds |= other.kinds
        self.dispatch = self.dispatch or other.dispatch
        self.truncated = self.truncated or other.truncated
        self.named_by_the_pack = self.named_by_the_pack or other.named_by_the_pack
        for name, shape in other.fields.items():
            self.fields.setdefault(name, Shape()).merge(shape)
        if other.items is not None:
            if self.items is None:
                self.items = Shape()
            self.items.merge(other.items)
        for name, shape in other.variants.items():
            self.variants.setdefault(name, Shape()).merge(shape)

    # -- reading -----------------------------------------------------------

    def required(self) -> set[str]:
        """The fields every vanilla file of this shape had."""
        if self.seen < ENOUGH:
            return set()
        return {name for name, shape in self.fields.items() if shape.seen == self.seen}

    def known(self) -> set[str]:
        return set(self.fields)

    def for_type(self, type_id: str) -> Shape | None:
        return self.variants.get(normalise_id(type_id))

    def describe(self) -> str:
        """One line: what this place holds, for the inspector and the CLI."""
        kinds = ", ".join(sorted(self.kinds)) or "nothing"
        if self.variants:
            return f"{kinds} ({len(self.variants)} kinds of {self.dispatch})"
        return kinds

    # -- saving ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"seen": self.seen, "kinds": sorted(self.kinds)}
        if self.fields:
            out["fields"] = {name: shape.to_dict() for name, shape in self.fields.items()}
        if self.items is not None:
            out["items"] = self.items.to_dict()
        if self.dispatch:
            out["dispatch"] = self.dispatch
            out["variants"] = {name: shape.to_dict() for name, shape in self.variants.items()}
        if self.truncated:
            out["truncated"] = True
        if self.named_by_the_pack:
            out["named_by_the_pack"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Shape:
        shape = cls(seen=int(data.get("seen", 0)), kinds=set(data.get("kinds", [])))
        shape.fields = {
            name: cls.from_dict(held) for name, held in (data.get("fields") or {}).items()
        }
        items = data.get("items")
        shape.items = cls.from_dict(items) if isinstance(items, dict) else None
        shape.dispatch = str(data.get("dispatch", ""))
        shape.variants = {
            name: cls.from_dict(held) for name, held in (data.get("variants") or {}).items()
        }
        shape.truncated = bool(data.get("truncated", False))
        shape.named_by_the_pack = bool(data.get("named_by_the_pack", False))
        return shape


def _dispatch_key(value: dict[str, Any], parent: str = "") -> str:
    for key in DISPATCH_KEYS:
        held = value.get(key)
        if not isinstance(held, str) or ":" not in held:
            continue  # a kind is written as an id; `"type": "top"` is a value
        where = DISPATCH_PARENTS.get(key)
        if where is not None and parent not in where:
            continue  # an advancement's `rewards.function` is an id, not a kind
        return key
    return ""


@dataclass
class Issue:
    """One thing a file says that its version's own files never say."""

    #: "unknown-field", "missing-field" or "wrong-kind"
    kind: str
    #: where it is, as ``pools[0].entries[1].name``
    where: str
    message: str
    #: warnings are "no file of that version read does this", notes "every
    #: file of that version read does". Neither is a rule of the game: the
    #: version's own files say what it writes, not everything it accepts.
    severity: str = "warning"


@dataclass
class Schema:
    """Every folder's shape, for one version."""

    version_id: str = ""
    folders: dict[str, Shape] = field(default_factory=dict)
    #: folders vanilla writes no files of: their shapes were collected from
    #: inside other files, so their count is of objects, not files
    collected: set[str] = field(default_factory=set)

    def counts(self, folder: str) -> str:
        """ "file(s)" or "object(s)": what the number for that folder counts."""
        return "object(s)" if folder in self.collected else "file(s)"

    def shape(self, folder: str) -> Shape | None:
        return self.folders.get(folder)

    def check(self, content: Any, folder: str) -> list[Issue]:
        """What ``content`` holds that the version's own ``folder`` files do not."""
        shape = self.folders.get(folder)
        if shape is None or shape.seen < ENOUGH:
            return []
        issues: list[Issue] = []
        if isinstance(content, list) and "a list" not in shape.kinds:
            # a predicate or item modifier file may hold one or several
            for index, item in enumerate(content):
                _check(item, shape, f"[{index}]", issues, self.version_id)
            return issues
        _check(content, shape, "", issues, self.version_id)
        return issues

    # -- saving ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND,
            "format": FORMAT,
            "version": self.version_id,
            "collected": sorted(self.collected),
            "folders": {name: shape.to_dict() for name, shape in self.folders.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schema:
        if not isinstance(data, dict) or data.get("kind") != KIND:
            raise ValueError("not a datapack-emulator schema file")
        if int(data.get("format", 0)) != FORMAT:
            raise ValueError(f"schema format {data.get('format')}, this build reads {FORMAT}")
        schema = cls(version_id=str(data.get("version", "")))
        schema.folders = {
            name: Shape.from_dict(held) for name, held in (data.get("folders") or {}).items()
        }
        schema.collected = {str(name) for name in (data.get("collected") or [])}
        return schema


def _at(where: str, step: str) -> str:
    return f"{where}.{step}" if where else step


def _check(value: Any, shape: Shape, where: str, issues: list[Issue], version_id: str) -> None:
    held = kind_of(value)
    if shape.truncated:
        return  # learning stopped above here: nothing below it is known
    if shape.kinds and held not in shape.kinds and shape.seen >= KIND_EVIDENCE:
        issues.append(
            Issue(
                "wrong-kind",
                where,
                f"is {held}; every {version_id} file read holds "
                + " or ".join(sorted(shape.kinds))
                + " here",
            )
        )
        return
    if isinstance(value, dict):
        inner = shape
        if shape.dispatch:
            type_id = value.get(shape.dispatch)
            if not isinstance(type_id, str):
                return  # the id itself is checked against the registry elsewhere
            found = shape.for_type(type_id)
            if found is None:
                return  # an unknown type: the registry check says so, not this
            inner = found
        if inner.named_by_the_pack:
            # a criterion, a recipe key letter: the names are the pack's own,
            # so only what they hold is worth checking
            values = inner.values_shape()
            for name, held_value in value.items():
                _check(held_value, values, _at(where, name), issues, version_id)
            return
        if inner.seen >= ENOUGH and inner.fields:
            for name in sorted(set(value) - inner.known()):
                issues.append(
                    Issue(
                        "unknown-field",
                        _at(where, name),
                        f"no {version_id} file read uses this field here",
                    )
                )
            for name in sorted(inner.required() - set(value)):
                issues.append(
                    Issue(
                        "missing-field",
                        _at(where, name),
                        f"missing: every {version_id} file read sets it here (it may be optional)",
                        "info",
                    )
                )
        for name, held_value in value.items():
            child = inner.fields.get(name)
            if child is not None:
                _check(held_value, child, _at(where, name), issues, version_id)
    elif isinstance(value, list) and shape.items is not None:
        for index, item in enumerate(value):
            _check(item, shape.items, f"{where}[{index}]", issues, version_id)


# ---------------------------------------------------------------------------
# learning a version's schema from its jar
# ---------------------------------------------------------------------------


def _folder_names(names: list[str], folder: str) -> list[str]:
    """The jar entries of one folder, in either spelling, newest first."""
    for spelling in (folder, _plural(folder)):
        prefix = f"data/minecraft/{spelling}/"
        found = sorted(name for name in names if name.startswith(prefix) and name.endswith(".json"))
        if not found:
            continue
        if len(found) <= PER_FOLDER:
            return found
        # a jar with more files than any real one: spread over the whole folder
        step = (len(found) + PER_FOLDER - 1) // PER_FOLDER
        return found[::step][:PER_FOLDER]
    return []


def _plural(folder: str) -> str:
    """The pre-1.21 spelling of a folder (``loot_table`` -> ``loot_tables``)."""
    head, _, last = folder.rpartition("/")
    plural = last + ("es" if last.endswith(("s", "x", "ch")) else "s")
    return f"{head}/{plural}" if head else plural


#: a folder vanilla ships no files of, learned from where it is used inline
FROM_INSIDE = {"predicate": "condition", "item_modifier": "function"}


def _collect(shape: Shape, dispatch: str, into: Shape, depth: int = 0) -> None:
    """Fold every object dispatching on ``dispatch`` into one shape: vanilla
    writes its conditions and item functions inside loot tables, not as files."""
    if depth >= WALK_DEPTH:
        return
    if shape.dispatch == dispatch:
        into.merge(shape)
        return
    for child in shape.fields.values():
        _collect(child, dispatch, into, depth + 1)
    for child in shape.variants.values():
        _collect(child, dispatch, into, depth + 1)
    if shape.items is not None:
        _collect(shape.items, dispatch, into, depth + 1)


def learn_from_jar(jar_path: Path | str, folders: tuple[str, ...] = LEARNED) -> Schema:
    """Read a client jar's own data pack and say what each folder holds."""
    jar_path = Path(jar_path)
    schema = Schema()
    with zipfile.ZipFile(jar_path) as archive:
        names = archive.namelist()
        meta = archive.read("version.json") if "version.json" in names else b"{}"
        try:
            schema.version_id = str(json.loads(meta).get("id", "")) or jar_path.stem
        except (json.JSONDecodeError, UnicodeDecodeError):
            schema.version_id = jar_path.stem
        for folder in folders:
            shape = Shape()
            for name in _folder_names(names, folder):
                try:
                    content = json.loads(archive.read(name))
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError, OSError) as exc:
                    log.debug("cannot read %s from %s: %s", name, jar_path, exc)
                    continue
                shape.learn(content)
            if shape.seen:
                shape.settle()
                schema.folders[folder] = shape
    for folder, dispatch in FROM_INSIDE.items():
        collected = Shape()
        for name, shape in list(schema.folders.items()):
            if name != folder:
                _collect(shape, dispatch, collected)
        if collected.seen < ENOUGH:
            continue
        collected.kinds.add("an object")
        collected.settle()
        schema.collected.add(folder)
        # a pack writes these as files; vanilla mostly writes them inside a
        # loot table, so both are folded together
        if folder in schema.folders:
            schema.folders[folder].merge(collected)
        else:
            schema.folders[folder] = collected
    log.info(
        "schema for %s: %s",
        schema.version_id,
        ", ".join(f"{name} {shape.seen}" for name, shape in sorted(schema.folders.items())),
    )
    return schema


def schema_path(cache_dir: Path | str, version_id: str, jar_path: Path | str | None = None) -> Path:
    """Where a learned schema is kept: one file per jar, not per version id —
    two jars can call themselves the same version (a snapshot rebuilt, a
    hand-made one)."""
    version_id = re.sub(r"[^A-Za-z0-9._-]", "_", version_id) or "unknown"
    mark = ""
    if jar_path is not None:
        try:
            mark = f"-{Path(jar_path).stat().st_size}"
        except OSError:
            mark = ""
    return Path(cache_dir) / "schemas" / f"{version_id}{mark}.json"


def load_schema(path: Path | str) -> Schema:
    """Read a written schema; raises ``ValueError`` when it is not one."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    return Schema.from_dict(data)


def save_schema(schema: Schema, path: Path | str) -> Path:
    """Write it whole: another run may be reading the same file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    beside = path.with_name(f"{path.name}.{os.getpid()}.part")
    beside.write_text(json.dumps(schema.to_dict()), encoding="utf-8")
    os.replace(beside, path)
    return path


def schema_for(assets: Any, cache_dir: Path | str | None = None) -> Schema | None:
    """The schema of a loaded jar, read from the cache or learned and kept.

    ``assets`` is a :class:`~datapack_emulator.emulator.vanilla.VanillaAssets`;
    None when there is no jar, so nothing is claimed.
    """
    if assets is None or not getattr(assets, "jar_path", None):
        return None
    version_id = getattr(assets, "version_id", "") or Path(assets.jar_path).stem
    cached = schema_path(cache_dir, version_id, assets.jar_path) if cache_dir else None
    if cached is not None and cached.is_file():
        try:
            return load_schema(cached)
        except ValueError as exc:  # a schema of an older build: learn it again
            log.info("re-learning the schema of %s: %s", version_id, exc)
    try:
        schema = learn_from_jar(assets.jar_path)
    except (OSError, zipfile.BadZipFile) as exc:
        log.warning("cannot read %s: %s", assets.jar_path, exc)
        return None
    if cached is not None:
        try:
            save_schema(schema, cached)
        except OSError as exc:  # a read-only cache is not a reason to stop
            log.info("cannot keep the schema beside the jar: %s", exc)
    return schema
