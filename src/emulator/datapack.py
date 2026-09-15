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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

from src.emulator import versions
from src.emulator.common import flatten_text_component, normalise_id
from src.emulator.namespace import Namespace
from src.emulator.resources import Function, Resource, Tag
from src.emulator.versions import Version

log = logging.getLogger(__name__)

Format = tuple[int, int]


#: the minor version vanilla uses for "any minor" (a whole-number upper bound)
ANY_MINOR = 0x7FFFFFFF


def _as_format(value: Any, upper: bool = False) -> Optional[Format]:
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


def format_label(pack_format: Optional[Format]) -> str:
    """``(94, 1)`` -> ``"94.1"``, ``(94, ANY_MINOR)`` -> ``"94.*"``."""
    if pack_format is None:
        return "*"
    major, minor = pack_format
    return f"{major}.*" if minor == ANY_MINOR else f"{major}.{minor}"


def _format_bounds(
    formats: Any, min_format: Any, max_format: Any
) -> tuple[Optional[Format], Optional[Format]]:
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
    minimum: Optional[Format] = None
    maximum: Optional[Format] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def applies_to(self, pack_format: Optional[Format]) -> bool:
        if pack_format is None:
            return False
        if self.minimum is not None and pack_format < self.minimum:
            return False
        if self.maximum is not None and pack_format > self.maximum:
            return False
        return True

    def describe(self) -> str:
        return f"{self.directory} [{format_label(self.minimum)} .. {format_label(self.maximum)}]"


class PackMCMETA:
    """``pack.mcmeta`` at the root of a datapack."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.content: dict[str, Any] = {}
        self.error: Optional[str] = None
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
    def pack_format(self) -> Optional[float]:
        value = self.pack.get("pack_format")
        if isinstance(value, (int, float)):
            return float(value)
        declared = self.format_tuple
        return float(f"{declared[0]}.{declared[1]}") if declared else None

    @property
    def format_tuple(self) -> Optional[Format]:
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
    def format_range(self) -> tuple[Optional[Format], Optional[Format]]:
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

    def supports_format(self, pack_format: Optional[Format]) -> bool:
        """Would the game accept this pack at ``pack_format``?"""
        if pack_format is None:
            return False
        minimum, maximum = self.format_range
        declared = self.format_tuple
        if minimum is None and maximum is None:
            return declared is not None and declared[0] == pack_format[0]
        if minimum is not None and pack_format < minimum:
            return False
        if maximum is not None and pack_format > maximum:
            return False
        return True

    def to_minecraft_version(self) -> str:
        return versions.format_to_version_string(self.pack_format)

    def __repr__(self) -> str:
        return f"<PackMCMETA format={self.pack_format} mc={self.to_minecraft_version()}>"


class PackPNG:
    """``pack.png``, the datapack icon.  Only the bytes are kept."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.data: bytes = b""
        self.error: Optional[str] = None
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
    entry: Optional[OverlayEntry] = None

    @property
    def label(self) -> str:
        return "base" if self.entry is None else self.directory

    def applies_to(self, pack_format: Optional[Format]) -> bool:
        return self.entry is None or self.entry.applies_to(pack_format)


class PackView:
    """A datapack flattened for one pack format: base plus matching overlays."""

    def __init__(self, layers: list[Layer], pack_format: Optional[Format]):
        self.pack_format = pack_format
        self.layers = layers
        #: registry -> resource id -> resource (later layers win)
        self.registries: dict[str, dict[str, Resource]] = {}
        self.namespaces: dict[str, list[Namespace]] = {}
        for layer in layers:
            for namespace in layer.namespaces.values():
                self.namespaces.setdefault(namespace.name, []).append(namespace)
                for registry, bucket in namespace.registries.items():
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

    def function(self, function_id: str) -> Optional[Function]:
        return self.functions.get(normalise_id(function_id))

    def resolve_function_tag(self, tag_id: str, _seen: Optional[set[str]] = None) -> list[str]:
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

    def __repr__(self) -> str:
        return (
            f"<PackView format={self.pack_format} functions={len(self.functions)} "
            f"overlays={self.active_overlays}>"
        )


class Datapack:
    """A datapack folder: ``pack.mcmeta``, ``pack.png``, ``data/`` and overlays."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.name = self.path.name
        self.mcmeta: Optional[PackMCMETA] = None
        self.icon: Optional[PackPNG] = None
        self.base: Layer = Layer(directory="data", path=self.path / "data")
        self.overlays: list[Layer] = []
        self.errors: list[str] = []

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "Datapack":
        pack = cls(path)
        pack.reload()
        return pack

    def reload(self) -> "Datapack":
        self.errors.clear()
        self.overlays.clear()
        self.view.cache_clear()

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

    @lru_cache(maxsize=64)
    def view(
        self, pack_format: Optional[Format] = None, allow_overlays: bool = True
    ) -> PackView:
        """Merge the base pack with the overlays that apply at ``pack_format``."""
        target = pack_format or (self.mcmeta.format_tuple if self.mcmeta else None)
        layers = [self.base]
        if allow_overlays:
            layers += [layer for layer in self.overlays if layer.applies_to(target)]
        return PackView(layers, target)

    def view_for(self, version: Version) -> PackView:
        """The pack as ``version`` would read it (pre-1.20.2 ignores overlays)."""
        return self.view(version.format, versions.supports_overlays(version))

    # -- accessors --------------------------------------------------------

    @property
    def namespaces(self) -> dict[str, Namespace]:
        """The base pack's namespaces (overlays live in :attr:`overlays`)."""
        return self.base.namespaces

    @property
    def base_functions(self) -> dict[str, Function]:
        return self.view().functions

    @property
    def pack_format(self) -> Optional[float]:
        return self.mcmeta.pack_format if self.mcmeta else None

    @property
    def format_range(self) -> tuple[Optional[Format], Optional[Format]]:
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
        return bool(self.mcmeta and self.mcmeta.supports_format(version.format))

    # convenience passthroughs to the default view -------------------------

    @property
    def functions(self) -> dict[str, Function]:
        return self.view().functions

    @property
    def function_tags(self) -> dict[str, Tag]:
        return self.view().function_tags

    def resources(self) -> Iterator[Resource]:
        return self.view().resources()

    def function(self, function_id: str) -> Optional[Function]:
        return self.view().function(function_id)

    def resolve_function_tag(self, tag_id: str) -> list[str]:
        return self.view().resolve_function_tag(tag_id)

    def __repr__(self) -> str:
        return f"<Datapack {self.name} namespaces={list(self.namespaces)}>"
