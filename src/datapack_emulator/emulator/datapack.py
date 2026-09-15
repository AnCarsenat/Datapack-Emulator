"""The datapack itself: ``pack.mcmeta``, ``pack.png``, ``data/`` and overlays.

An overlay is a second copy of ``data/`` that the game swaps in when the pack
format matches — the mechanism packs use to support several Minecraft versions
at once (added in 1.20.2).  :class:`Datapack` loads the base pack and every
overlay directory separately; :meth:`Datapack.view` then merges them for one
pack format and hands back a :class:`PackView`, which is what the emulator and
the call graph actually read.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import flatten_text_component, normalise_id
from datapack_emulator.emulator.namespace import Namespace
from datapack_emulator.emulator.resources import Function, Resource, Tag
from datapack_emulator.emulator.versions import Version

log = logging.getLogger(__name__)

Format = tuple[int, int]


#: the minor version vanilla uses for "any minor" (a whole-number upper bound)
ANY_MINOR = 0x7FFFFFFF


def _as_format(value: Any, upper: bool = False) -> Format | None:
    """Read a pack format as ``(major, minor)``.

    ``18.1`` and ``[18, 1]`` are exact. A whole number or a one-element list
    means the major version only: as a lower bound that is ``(18, 0)``, as an
    upper bound it is ``(18, ANY_MINOR)`` — "max_format: 94" includes 94.1.
    https://minecraft.wiki/w/Pack.mcmeta
    """
    if value is None or isinstance(value, bool):
        return None
    whole = ANY_MINOR if upper else 0
    if isinstance(value, (list, tuple)):
        if len(value) == 2:
            return (int(value[0]), int(value[1]))
        if len(value) == 1:
            return (int(value[0]), whole)
        return None
    if isinstance(value, int):
        return (value, whole)
    if isinstance(value, float):
        major = int(value)
        return (major, round((value - major) * 10))
    return None


def format_label(pack_format: Format | None) -> str:
    """``(94, 1)`` -> ``"94.1"``, ``(94, ANY_MINOR)`` -> ``"94.*"``."""
    if pack_format is None:
        return "*"
    major, minor = pack_format
    return f"{major}.*" if minor == ANY_MINOR else f"{major}.{minor}"


def _within(pack_format: Format | None, minimum: Format | None, maximum: Format | None) -> bool:
    """Is ``pack_format`` inside the inclusive, possibly open, range?"""
    if pack_format is None:
        return False
    return (minimum is None or pack_format >= minimum) and (
        maximum is None or pack_format <= maximum
    )


def _format_bounds(
    formats: Any, min_format: Any, max_format: Any
) -> tuple[Format | None, Format | None]:
    """``(min, max)`` from the old ``supported_formats``/``formats`` field or
    the newer ``min_format``/``max_format`` pair; the newer fields fill gaps."""
    minimum = maximum = None
    if isinstance(formats, dict):
        minimum = _as_format(formats.get("min_inclusive"))
        maximum = _as_format(formats.get("max_inclusive"), upper=True)
    elif isinstance(formats, list) and len(formats) == 2:
        minimum, maximum = _as_format(formats[0]), _as_format(formats[1], upper=True)
    elif formats is not None:
        minimum, maximum = _as_format(formats), _as_format(formats, upper=True)
    if minimum is None:
        minimum = _as_format(min_format)
    if maximum is None:
        maximum = _as_format(max_format, upper=True)
    return (minimum, maximum)


@dataclass
class OverlayEntry:
    """One entry of ``pack.mcmeta``'s ``overlays.entries``."""

    directory: str
    minimum: Format | None = None
    maximum: Format | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def applies_to(self, pack_format: Format | None) -> bool:
        return _within(pack_format, self.minimum, self.maximum)

    def describe(self) -> str:
        return f"{self.directory} [{format_label(self.minimum)} .. {format_label(self.maximum)}]"


class PackMCMETA:
    """``pack.mcmeta`` at the root of a datapack."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.content: dict[str, Any] = {}
        self.error: str | None = None
        try:
            with self.path.open("r", encoding="utf-8") as file:
                self.content = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.error = str(exc)
            log.error("cannot read %s: %s", self.path, exc)

    @property
    def pack(self) -> dict[str, Any]:
        return self.content.get("pack", {})

    @property
    def pack_format(self) -> float | None:
        value = self.pack.get("pack_format")
        if isinstance(value, (int, float)):
            return float(value)
        declared = self.format_tuple
        return float(f"{declared[0]}.{declared[1]}") if declared else None

    @property
    def format_tuple(self) -> Format | None:
        """The format the pack targets: ``pack_format``, else the top of its range."""
        exact = _as_format(self.pack.get("pack_format"))
        if exact is not None:
            return exact
        minimum, maximum = self.format_range
        bound = maximum or minimum
        if bound is None:
            return None
        if bound[1] != ANY_MINOR:
            return bound
        # "any minor of N": the newest release actually published with major N
        known = [version.format for version in versions.VERSIONS if version.pack_format == bound[0]]
        return max(known) if known else (bound[0], 0)

    @property
    def format_range(self) -> tuple[Format | None, Format | None]:
        """``(min, max)`` from ``supported_formats`` / ``min_format`` / ``max_format``."""
        return _format_bounds(
            self.pack.get("supported_formats"),
            self.pack.get("min_format"),
            self.pack.get("max_format"),
        )

    @property
    def description(self) -> str:
        return flatten_text_component(self.pack.get("description", ""))

    @property
    def overlays(self) -> list[OverlayEntry]:
        entries = self.content.get("overlays", {})
        raw_entries = entries.get("entries", []) if isinstance(entries, dict) else []
        out: list[OverlayEntry] = []
        for entry in raw_entries:
            if not isinstance(entry, dict) or "directory" not in entry:
                continue
            minimum, maximum = _format_bounds(
                entry.get("formats"), entry.get("min_format"), entry.get("max_format")
            )
            out.append(
                OverlayEntry(
                    directory=str(entry["directory"]),
                    minimum=minimum,
                    maximum=maximum,
                    raw=entry,
                )
            )
        return out

    def supports_format(self, pack_format: Format | None) -> bool:
        """Would the game accept this pack at ``pack_format``?"""
        if pack_format is None:
            return False
        minimum, maximum = self.format_range
        declared = self.format_tuple
        if minimum is None and maximum is None:
            return declared is not None and declared[0] == pack_format[0]
        return _within(pack_format, minimum, maximum)

    def to_minecraft_version(self) -> str:
        return versions.format_to_version_string(self.pack_format)

    def __repr__(self) -> str:
        return f"<PackMCMETA format={self.pack_format} mc={self.to_minecraft_version()}>"


class PackPNG:
    """``pack.png``, the datapack icon.  Only the bytes are kept."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.data: bytes = b""
        self.error: str | None = None
        try:
            self.data = self.path.read_bytes()
        except OSError as exc:
            self.error = str(exc)
            log.warning("cannot read %s: %s", self.path, exc)

    @property
    def exists(self) -> bool:
        return bool(self.data)

    @property
    def size(self) -> tuple[int, int]:
        """Width/height straight out of the PNG IHDR chunk (no image library)."""
        if len(self.data) >= 24 and self.data[:8] == b"\x89PNG\r\n\x1a\n":
            return (
                int.from_bytes(self.data[16:20], "big"),
                int.from_bytes(self.data[20:24], "big"),
            )
        return (0, 0)

    def __repr__(self) -> str:
        return f"<PackPNG {self.path.name} {self.size[0]}x{self.size[1]}>"


@dataclass
class Layer:
    """The base pack, or one overlay: a set of namespaces from one directory."""

    directory: str  # "data" for the base pack
    path: Path
    namespaces: dict[str, Namespace] = field(default_factory=dict)
    entry: OverlayEntry | None = None

    @property
    def label(self) -> str:
        return "base" if self.entry is None else self.directory

    def applies_to(self, pack_format: Format | None) -> bool:
        return self.entry is None or self.entry.applies_to(pack_format)


class PackView:
    """A datapack flattened for one pack format: base plus matching overlays."""

    def __init__(self, layers: list[Layer], pack_format: Format | None, singular: bool = True):
        self.pack_format = pack_format
        self.layers = layers
        #: whether this view reads `function/` (1.21+) or `functions/` folders
        self.singular = singular
        #: registry -> resource id -> resource (later layers win)
        self.registries: dict[str, dict[str, Resource]] = {}
        self.namespaces: dict[str, list[Namespace]] = {}
        for layer in layers:
            for namespace in layer.namespaces.values():
                self.namespaces.setdefault(namespace.name, []).append(namespace)
                for registry, bucket in namespace.registries_for(singular).items():
                    self.registries.setdefault(registry, {}).update(bucket)

    # -- accessors --------------------------------------------------------

    @property
    def functions(self) -> dict[str, Function]:
        return {
            key: value
            for key, value in self.registries.get("function", {}).items()
            if isinstance(value, Function)
        }

    @property
    def function_tags(self) -> dict[str, Tag]:
        out: dict[str, Tag] = {}
        for tag in self.registries.get("tags/function", {}).values():
            if isinstance(tag, Tag):
                out[tag.tag_id] = tag
        return out

    def resources(self) -> Iterator[Resource]:
        for bucket in self.registries.values():
            yield from bucket.values()

    def function(self, function_id: str) -> Function | None:
        return self.functions.get(normalise_id(function_id))

    def resolve_function_tag(self, tag_id: str, _seen: set[str] | None = None) -> list[str]:
        """Flatten ``#ns:tag`` into the list of function ids it points at."""
        seen = _seen if _seen is not None else set()
        normalised = normalise_id(tag_id.lstrip("#"))
        if normalised in seen:
            return []
        seen.add(normalised)
        tag = self.function_tags.get(f"#{normalised}")
        if tag is None:
            return []
        out: list[str] = []
        for value in tag.values:
            if value.startswith("#"):
                out.extend(self.resolve_function_tag(value, seen))
            else:
                out.append(normalise_id(value))
        return out

    @property
    def plural_folders(self) -> set[str]:
        found: set[str] = set()
        for namespaces in self.namespaces.values():
            for namespace in namespaces:
                found |= namespace.plural_folders
        return found

    @property
    def active_overlays(self) -> list[str]:
        return [layer.directory for layer in self.layers if layer.entry is not None]

    @classmethod
    def merged(cls, views: list[PackView]) -> PackView:
        """Several packs enabled together, in load order.

        Resources with the same id come from the last pack that has them, like
        the game; tags are the exception: their values add up across packs,
        unless a later pack's tag says ``"replace": true``.
        """
        if len(views) == 1:
            return views[0]
        first = views[0]
        combined = cls(
            [layer for view in views for layer in view.layers], first.pack_format, first.singular
        )
        for registry in [name for name in combined.registries if name.startswith("tags/")]:
            tags: dict[str, Resource] = {}
            for view in views:
                for resource_id, tag in view.registries.get(registry, {}).items():
                    earlier = tags.get(resource_id)
                    if isinstance(earlier, Tag) and isinstance(tag, Tag):
                        tags[resource_id] = _merge_tags(earlier, tag)
                    else:
                        tags[resource_id] = tag
            combined.registries[registry] = tags
        return combined

    def __repr__(self) -> str:
        return (
            f"<PackView format={self.pack_format} functions={len(self.functions)} "
            f"overlays={self.active_overlays}>"
        )


def _merge_tags(earlier: Tag, later: Tag) -> Tag:
    """The same tag from two packs: the earlier values then the later ones, or
    only the later ones when it says ``replace``. ``sources`` keeps every file,
    in order, for loaders that read each file on its own (1.16.1)."""
    merged = Tag(later.path, later.namespace, later.registry, later.resource_path)
    merged.overlay = later.overlay
    earlier_values = [] if later.replace else earlier.content.get("values", [])
    merged.content = {"values": [*earlier_values, *later.content.get("values", [])]}
    merged.sources = [*getattr(earlier, "sources", [earlier]), later]
    return merged


class Datapack:
    """A datapack folder: ``pack.mcmeta``, ``pack.png``, ``data/`` and overlays."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.name = self.path.name
        self.mcmeta: PackMCMETA | None = None
        self.icon: PackPNG | None = None
        self.base: Layer = Layer(directory="data", path=self.path / "data")
        self.overlays: list[Layer] = []
        self.errors: list[str] = []
        #: merged views per (pack format, overlays allowed, singular folders)
        self._views: dict[tuple[Format | None, bool, bool], PackView] = {}

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> Datapack:
        pack = cls(path)
        pack.reload()
        return pack

    def reload(self) -> Datapack:
        self.errors.clear()
        self.overlays.clear()
        self._views.clear()

        if not self.path.is_dir():
            self.errors.append(f"{self.path} is not a directory")
            return self

        mcmeta_path = self.path / "pack.mcmeta"
        if mcmeta_path.is_file():
            self.mcmeta = PackMCMETA(mcmeta_path)
            if self.mcmeta.error:
                self.errors.append(f"pack.mcmeta: {self.mcmeta.error}")
        else:
            self.mcmeta = None
            self.errors.append("missing pack.mcmeta")

        png_path = self.path / "pack.png"
        self.icon = PackPNG(png_path) if png_path.is_file() else None

        self.base = Layer(directory="data", path=self.path / "data")
        if self.base.path.is_dir():
            self.base.namespaces = self._load_namespaces(self.base.path, overlay="")
        else:
            self.errors.append("missing data/ folder")

        for entry in self.mcmeta.overlays if self.mcmeta else []:
            directory = self.path / entry.directory
            data_dir = directory / "data"
            if not data_dir.is_dir():
                self.errors.append(f"overlay '{entry.directory}' has no data/ folder")
                continue
            layer = Layer(directory=entry.directory, path=data_dir, entry=entry)
            layer.namespaces = self._load_namespaces(data_dir, overlay=entry.directory)
            self.overlays.append(layer)

        log.info(
            "loaded %s: %d namespace(s), %d function(s), %d overlay(s), pack_format %s (%s)",
            self.name,
            len(self.base.namespaces),
            len(self.base_functions),
            len(self.overlays),
            self.pack_format,
            self.minecraft_version,
        )
        return self

    @staticmethod
    def _load_namespaces(data_dir: Path, overlay: str) -> dict[str, Namespace]:
        out: dict[str, Namespace] = {}
        for entry in sorted(data_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                out[entry.name] = Namespace(entry.name, entry, overlay=overlay).load()
        return out

    # -- views ------------------------------------------------------------

    def view(
        self,
        pack_format: Format | None = None,
        allow_overlays: bool = True,
        singular: bool | None = None,
    ) -> PackView:
        """Merge the base pack with the overlays that apply at ``pack_format``.

        Cached on the instance: an ``lru_cache`` on the method would be shared
        by every Datapack, keep them all alive, and be cleared for all of them
        whenever one reloads.
        """
        target = pack_format or (self.mcmeta.format_tuple if self.mcmeta else None)
        if singular is None:
            singular = versions.singular_registries_for_format(target)
        key = (target, allow_overlays, singular)
        cached = self._views.get(key)
        if cached is None:
            layers = [self.base]
            if allow_overlays:
                layers += [layer for layer in self.overlays if layer.applies_to(target)]
            cached = self._views[key] = PackView(layers, target, singular)
        return cached

    def view_for(self, version: Version) -> PackView:
        """The pack as ``version`` would read it (pre-1.20.2 ignores overlays)."""
        return self.view(
            version.format,
            versions.supports_overlays(version),
            versions.uses_singular_registries(version),
        )

    # -- accessors --------------------------------------------------------

    @property
    def namespaces(self) -> dict[str, Namespace]:
        """The base pack's namespaces (overlays live in :attr:`overlays`)."""
        return self.base.namespaces

    @property
    def base_functions(self) -> dict[str, Function]:
        return self.view().functions

    @property
    def pack_format(self) -> float | None:
        return self.mcmeta.pack_format if self.mcmeta else None

    @property
    def format_range(self) -> tuple[Format | None, Format | None]:
        return self.mcmeta.format_range if self.mcmeta else (None, None)

    @property
    def minecraft_version(self) -> str:
        return self.mcmeta.to_minecraft_version() if self.mcmeta else "unknown"

    @property
    def description(self) -> str:
        return self.mcmeta.description if self.mcmeta else ""

    def declared_versions(self) -> list[Version]:
        """Every release the pack says it supports, oldest first."""
        minimum, maximum = self.format_range
        declared = self.mcmeta.format_tuple if self.mcmeta else None
        if minimum is None and maximum is None and declared is None:
            return []
        low = minimum or declared
        high = maximum or declared
        return [
            version
            for version in versions.VERSIONS
            if (low is None or version.format >= low) and (high is None or version.format <= high)
        ]

    def supports(self, version: Version) -> bool:
        return self.compatibility(version).compatible

    def compatibility(self, version: Version) -> Compatibility:
        """How ``version`` reads this pack's pack.mcmeta (see Compatibility)."""
        return Compatibility.check(self, version)

    # convenience passthroughs to the default view -------------------------

    @property
    def functions(self) -> dict[str, Function]:
        return self.view().functions

    @property
    def function_tags(self) -> dict[str, Tag]:
        return self.view().function_tags

    def resources(self) -> Iterator[Resource]:
        return self.view().resources()

    def function(self, function_id: str) -> Function | None:
        return self.view().function(function_id)

    def resolve_function_tag(self, tag_id: str) -> list[str]:
        return self.view().resolve_function_tag(tag_id)

    def __repr__(self) -> str:
        return f"<Datapack {self.name} namespaces={list(self.namespaces)}>"


class DatapackSet:
    """Several datapacks enabled in one world, in load order (later ones win).

    Offers what the emulator, the engine and the window read from a
    :class:`Datapack`, so a single pack and a set are used the same way. What
    only makes sense for one pack (``pack.mcmeta``, the icon) comes from the
    first pack; compatibility looks at every pack.
    """

    def __init__(self, packs: Iterable[Datapack] = ()):
        self.packs: list[Datapack] = list(packs)
        self._views: dict[tuple[Any, ...], PackView] = {}

    @classmethod
    def load(cls, paths: Iterable[Path | str]) -> DatapackSet:
        return cls(Datapack.load(path) for path in paths)

    # -- editing ---------------------------------------------------------------

    def add(self, pack: Datapack) -> None:
        self.packs.append(pack)
        self._views.clear()

    def remove(self, index: int) -> Datapack:
        self._views.clear()
        return self.packs.pop(index)

    def move(self, index: int, step: int) -> bool:
        target = index + step
        if not (0 <= index < len(self.packs) and 0 <= target < len(self.packs)):
            return False
        self.packs[index], self.packs[target] = self.packs[target], self.packs[index]
        self._views.clear()
        return True

    def reload(self) -> DatapackSet:
        for pack in self.packs:
            pack.reload()
        self._views.clear()
        return self

    def index_of(self, path: Path | str) -> int | None:
        wanted = Path(path).resolve()
        for index, pack in enumerate(self.packs):
            if pack.path.resolve() == wanted:
                return index
        return None

    def __len__(self) -> int:
        return len(self.packs)

    def __iter__(self) -> Iterator[Datapack]:
        return iter(self.packs)

    def __bool__(self) -> bool:
        return bool(self.packs)

    # -- what a single pack offers ---------------------------------------------

    @property
    def primary(self) -> Datapack:
        return self.packs[0]

    @property
    def name(self) -> str:
        return " + ".join(pack.name for pack in self.packs) or "no datapack"

    @property
    def path(self) -> Path:
        return self.primary.path

    @property
    def paths(self) -> list[Path]:
        return [pack.path for pack in self.packs]

    @property
    def errors(self) -> list[str]:
        if len(self.packs) == 1:
            return list(self.primary.errors)
        return [f"{pack.name}: {error}" for pack in self.packs for error in pack.errors]

    @property
    def mcmeta(self) -> PackMCMETA | None:
        return self.primary.mcmeta

    @property
    def icon(self) -> PackPNG | None:
        return self.primary.icon

    @property
    def base(self) -> Layer:
        return self.primary.base

    @property
    def overlays(self) -> list[Layer]:
        return [layer for pack in self.packs for layer in pack.overlays]

    @property
    def namespaces(self) -> dict[str, Namespace]:
        out: dict[str, Namespace] = {}
        for pack in self.packs:
            out.update(pack.namespaces)
        return out

    @property
    def pack_format(self) -> float | None:
        return self.primary.pack_format

    @property
    def format_range(self) -> tuple[Format | None, Format | None]:
        return self.primary.format_range

    @property
    def minecraft_version(self) -> str:
        return self.primary.minecraft_version

    @property
    def description(self) -> str:
        return self.primary.description

    def declared_versions(self) -> list[Version]:
        """Versions every pack declares; the first pack's when they share none."""
        if len(self.packs) == 1:
            return self.primary.declared_versions()
        common = [set(pack.declared_versions()) for pack in self.packs if pack.declared_versions()]
        shared = sorted(set.intersection(*common)) if common else []
        return shared or self.primary.declared_versions()

    def compatibility(self, version: Version) -> Compatibility:
        """The first pack ``version`` does not list as compatible, else the first
        pack's; ``server_log`` holds what the server logs about every pack."""
        results = [(pack, pack.compatibility(version)) for pack in self.packs]
        if len(results) == 1:
            return results[0][1]
        server_log = [
            f"{pack.name}: {line}" for pack, result in results for line in result.server_log
        ]
        for pack, result in results:
            if not result.compatible:
                return replace(
                    result, reason=f"{pack.name}: {result.reason}", server_log=server_log
                )
        return replace(results[0][1], server_log=server_log)

    def supports(self, version: Version) -> bool:
        return all(pack.supports(version) for pack in self.packs)

    def view(
        self,
        pack_format: Format | None = None,
        allow_overlays: bool = True,
        singular: bool | None = None,
    ) -> PackView:
        key = ("view", pack_format, allow_overlays, singular)
        if key not in self._views:
            self._views[key] = PackView.merged(
                [pack.view(pack_format, allow_overlays, singular) for pack in self.packs]
            )
        return self._views[key]

    def view_for(self, version: Version) -> PackView:
        key = ("version", version)
        if key not in self._views:
            self._views[key] = PackView.merged([pack.view_for(version) for pack in self.packs])
        return self._views[key]

    @property
    def functions(self) -> dict[str, Function]:
        return self.view().functions

    @property
    def function_tags(self) -> dict[str, Tag]:
        return self.view().function_tags

    def resources(self) -> Iterator[Resource]:
        return self.view().resources()

    def function(self, function_id: str) -> Function | None:
        return self.view().function(function_id)

    def resolve_function_tag(self, tag_id: str) -> list[str]:
        return self.view().resolve_function_tag(tag_id)

    def __repr__(self) -> str:
        return f"<DatapackSet {self.name}>"


#: 1.20.2 reads supported_formats; 1.21.9 (25w31a) requires min_format/max_format
SUPPORTED_FORMATS_SINCE = "1.20.2"
MIN_MAX_FORMAT_SINCE = "1.21.9"
#: last data pack format without a minor version
LAST_WHOLE_FORMAT = 81
#: 1.21.9+ rejects multi-version metadata whose pack_format is older than this
MULTI_VERSION_MIN_PACK_FORMAT = 15


@dataclass
class Compatibility:
    """What one version makes of pack.mcmeta.

    Rules checked by a peer session in decompiled Mojang jars:

    * before 1.20.2 only ``pack_format`` is read and compared as one number
    * 1.20.2–1.21.8 also read ``supported_formats``; a range that does not
      contain ``pack_format`` is warned about and collapses to it
    * 1.21.9+ require ``min_format``/``max_format``; when the minimum is a
      pre-minor format (<= 81) ``pack_format`` and ``supported_formats`` must be
      present and agree with them, and ``pack_format`` must be at least 15.
      A violation makes the server log that it could not load the metadata and
      treat compatibility as unknown.

    In every case the pack is still loaded: incompatibility is a warning in the
    pack list, never a refusal.
    """

    version: Version
    #: "compatible", "too_old", "too_new" or "unknown"
    status: str
    #: problems the server itself logs while reading the metadata
    server_log: list[str] = field(default_factory=list)
    #: explanation of the verdict, for the emulator's notes
    reason: str = ""

    @property
    def compatible(self) -> bool:
        return self.status == "compatible"

    @classmethod
    def check(cls, datapack: Datapack, version: Version) -> Compatibility:
        mcmeta = datapack.mcmeta
        if mcmeta is None:
            return cls(version, "unknown", reason="no pack.mcmeta")
        pack = mcmeta.pack

        if version < versions.parse(SUPPORTED_FORMATS_SINCE):
            declared = pack.get("pack_format")
            if not isinstance(declared, (int, float)) or isinstance(declared, bool):
                return cls(version, "unknown", reason="pack_format is missing")
            return cls._compare(
                version, (int(declared), 0), (int(declared), ANY_MINOR), "pack_format"
            )

        if version < versions.parse(MIN_MAX_FORMAT_SINCE):
            declared = pack.get("pack_format")
            if not isinstance(declared, (int, float)) or isinstance(declared, bool):
                return cls(version, "unknown", reason="pack_format is missing")
            low, high = _format_bounds(pack.get("supported_formats"), None, None)
            whole = (int(declared), 0)
            if low is None and high is None:
                low, high = whole, (int(declared), ANY_MINOR)
            elif not _within(whole, low, high):
                verdict = cls._compare(version, whole, (int(declared), ANY_MINOR), "pack_format")
                verdict.server_log.append(
                    f"pack_format {int(declared)} is outside its supported_formats; "
                    "the range is ignored"
                )
                return verdict
            return cls._compare(version, low, high, "supported_formats")

        # 1.21.9 and later
        problems = _modern_metadata_problems(pack)
        if problems:
            return cls(
                version,
                "unknown",
                server_log=[
                    f"Couldn't load {datapack.name} pack metadata: {problem}"
                    for problem in problems
                ],
                reason="its metadata does not validate, so compatibility is unknown",
            )
        low = _as_format(pack.get("min_format"))
        high = _as_format(pack.get("max_format"), upper=True)
        return cls._compare(version, low, high, "min_format/max_format")

    @classmethod
    def _compare(
        cls, version: Version, low: Format | None, high: Format | None, source: str
    ) -> Compatibility:
        target = version.format
        if low is not None and target < low:
            return cls(version, "too_new", reason=f"{source} starts after {version.format_string}")
        if high is not None and target > high:
            return cls(version, "too_old", reason=f"{source} ends before {version.format_string}")
        return cls(version, "compatible", reason=f"{source} covers {version.format_string}")


def _modern_metadata_problems(pack: dict[str, Any]) -> list[str]:
    """The validation 1.21.9+ applies to min_format/max_format metadata."""
    minimum = _as_format(pack.get("min_format"))
    maximum = _as_format(pack.get("max_format"), upper=True)
    if minimum is None or maximum is None:
        return ["min_format and max_format are required"]
    if minimum[0] > LAST_WHOLE_FORMAT:
        legacy = [key for key in ("pack_format", "supported_formats") if key in pack]
        return (
            [f"{', '.join(legacy)} must be absent when min_format is above {LAST_WHOLE_FORMAT}"]
            if legacy
            else []
        )
    problems: list[str] = []
    declared = pack.get("pack_format")
    supported = pack.get("supported_formats")
    if not isinstance(declared, (int, float)) or isinstance(declared, bool):
        problems.append("pack_format is required when min_format is a pre-minor format")
    if supported is None:
        problems.append("supported_formats is required when min_format is a pre-minor format")
    else:
        low, high = _format_bounds(supported, None, None)
        if low is not None and low[0] != minimum[0]:
            problems.append("supported_formats must start at min_format")
        if high is not None and high[0] not in (maximum[0], LAST_WHOLE_FORMAT):
            problems.append("supported_formats must end at max_format")
    if isinstance(declared, (int, float)) and not isinstance(declared, bool):
        if not _within((int(declared), 0), minimum, maximum):
            problems.append("pack_format must lie between min_format and max_format")
        if int(declared) < MULTI_VERSION_MIN_PACK_FORMAT:
            problems.append(
                "Multi-version packs cannot support minimum version of less than "
                f"{MULTI_VERSION_MIN_PACK_FORMAT}"
            )
    return problems
