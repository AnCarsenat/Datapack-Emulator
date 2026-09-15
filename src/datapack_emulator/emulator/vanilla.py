"""Everything the base game already knows, read out of a ``client.jar``.

The jar is the authoritative source and it is already on most machines, so
nothing here is hardcoded:

* ``version.json`` — version id, data version, data/resource pack format
* ``assets/minecraft/lang/en_us.json`` — every command message and the entity,
  effect and enchantment names
* ``assets/minecraft/blockstates/`` — the block registry
* ``assets/minecraft/items/`` — the item registry
* ``assets/minecraft/particles/`` — the particle registry
* ``data/minecraft/`` — the vanilla data pack: tags, loot tables, recipes,
  advancements, enchantments, damage types, dimension types

:class:`VanillaLibrary` finds jars that are already installed (vanilla
launcher, Prism/MultiMC, its own cache) and only downloads one when asked to,
from Mojang's piston-meta manifest.

With assets loaded, the emulator answers in the exact wording of that version
and can tell a typo'd ``minecraft:armour_stand`` from a real entity type.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

#: where launchers keep their client jars
SEARCH_DIRS: tuple[Path, ...] = (
    Path.home() / ".minecraft" / "versions",
    Path.home()
    / ".local"
    / "share"
    / "PrismLauncher"
    / "libraries"
    / "com"
    / "mojang"
    / "minecraft",
    Path.home() / ".local" / "share" / "multimc" / "libraries" / "com" / "mojang" / "minecraft",
    Path.home()
    / ".var"
    / "app"
    / "org.prismlauncher.PrismLauncher"
    / "data"
    / "PrismLauncher"
    / "libraries"
    / "com"
    / "mojang"
    / "minecraft",
    Path.home() / "Library" / "Application Support" / "minecraft" / "versions",
    Path.home() / "AppData" / "Roaming" / ".minecraft" / "versions",
)

Progress = Callable[[str], None]
#: ``(bytes received, bytes expected)``; expected is 0 when the server does not say
ByteProgress = Callable[[int, int], None]
#: polled between chunks; returning ``True`` aborts the download
CancelCheck = Callable[[], bool]

CHUNK_SIZE = 64 * 1024


class DownloadCancelled(Exception):
    """The user stopped a client jar download; nothing was left on disk."""


def _read_json(archive: zipfile.ZipFile, name: str) -> dict:
    try:
        return json.loads(archive.read(name))
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        log.debug("cannot read %s from jar: %s", name, exc)
        return {}


def _ids_from_folder(names: Iterable[str], prefix: str) -> frozenset[str]:
    """``assets/minecraft/blockstates/stone.json`` -> ``minecraft:stone``."""
    out: set[str] = set()
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        rest = name[len(prefix) :].removesuffix(".json")
        if rest:
            out.add(f"minecraft:{rest}")
    return frozenset(out)


def _ids_from_lang(lang: dict[str, str], prefix: str) -> frozenset[str]:
    """``entity.minecraft.pig`` -> ``minecraft:pig``."""
    out: set[str] = set()
    for key in lang:
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if "." in rest:  # entity.minecraft.villager.farmer and friends
            continue
        out.add(f"minecraft:{rest}")
    return frozenset(out)


@dataclass
class VanillaAssets:
    """One version's base-game content, read from its client jar."""

    jar_path: Path
    version_id: str = ""
    data_version: int = 0
    pack_format: tuple[int, int] = (0, 0)
    lang: dict[str, str] = field(default_factory=dict)
    #: registry name -> ids, e.g. ``{"block": {"minecraft:stone", ...}}``
    registries: dict[str, frozenset[str]] = field(default_factory=dict)
    #: registry name -> tag id -> raw entries
    tags: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    # -- loading ----------------------------------------------------------

    @classmethod
    def from_jar(cls, jar_path: Path | str) -> VanillaAssets:
        jar_path = Path(jar_path)
        assets = cls(jar_path=jar_path)
        with zipfile.ZipFile(jar_path) as archive:
            names = archive.namelist()

            meta = _read_json(archive, "version.json")
            assets.version_id = str(meta.get("id", ""))
            assets.data_version = int(meta.get("world_version", 0) or 0)
            pack = meta.get("pack_version", {})
            if isinstance(pack, dict):
                assets.pack_format = (
                    int(pack.get("data_major", pack.get("data", 0)) or 0),
                    int(pack.get("data_minor", 0) or 0),
                )

            assets.lang = _read_json(archive, "assets/minecraft/lang/en_us.json")

            assets.registries = {
                "block": _ids_from_folder(names, "assets/minecraft/blockstates/"),
                "item": _ids_from_folder(names, "assets/minecraft/items/"),
                "particle": _ids_from_folder(names, "assets/minecraft/particles/"),
                "entity_type": _ids_from_lang(assets.lang, "entity.minecraft."),
                "mob_effect": _ids_from_lang(assets.lang, "effect.minecraft."),
                "attribute": _ids_from_lang(assets.lang, "attribute.name."),
            }
            for registry in (
                "advancement",
                "banner_pattern",
                "chat_type",
                "damage_type",
                "dimension_type",
                "enchantment",
                "instrument",
                "jukebox_song",
                "loot_table",
                "painting_variant",
                "recipe",
                "structure",
            ):
                ids = _ids_from_folder(names, f"data/minecraft/{registry}/")
                if ids:
                    assets.registries[registry] = ids

            for name in names:
                if not name.startswith("data/minecraft/tags/") or not name.endswith(".json"):
                    continue
                rest = name[len("data/minecraft/tags/") :]
                registry, _, tag_path = rest.partition("/")
                if not tag_path:
                    continue
                content = _read_json(archive, name)
                values = [
                    entry if isinstance(entry, str) else str(entry.get("id", ""))
                    for entry in content.get("values", [])
                    if isinstance(entry, (str, dict))
                ]
                assets.tags.setdefault(registry, {})[
                    f"minecraft:{tag_path.removesuffix('.json')}"
                ] = values

        log.info(
            "vanilla assets %s: %s",
            assets.version_id or jar_path.name,
            ", ".join(f"{name} {len(ids)}" for name, ids in sorted(assets.registries.items())),
        )
        return assets

    # -- queries ----------------------------------------------------------

    def read_json(self, name: str) -> dict[str, Any] | None:
        """A JSON file from the jar (``data/minecraft/loot_table/…``), or None."""
        try:
            with zipfile.ZipFile(self.jar_path) as archive:
                if name not in archive.namelist():
                    return None
                data = _read_json(archive, name)
        except (OSError, zipfile.BadZipFile):
            return None
        return data if isinstance(data, dict) and data else None

    def loot_table(self, table_id: str) -> dict[str, Any] | None:
        """A vanilla loot table by id; ``loot_table/`` from 1.21, ``loot_tables/`` before."""
        path = _qualify(table_id).split(":", 1)[1]
        for folder in ("loot_table", "loot_tables"):
            data = self.read_json(f"data/minecraft/{folder}/{path}.json")
            if data is not None:
                return data
        return None

    def knows(self, registry: str, resource_id: str) -> bool | None:
        """``True``/``False``, or ``None`` when that registry was not found."""
        ids = self.registries.get(registry)
        if not ids:
            return None
        return _qualify(resource_id) in ids

    def resolve_tag(self, registry: str, tag_id: str, _seen: set[str] | None = None) -> set[str]:
        """Flatten ``#minecraft:skeletons`` into the ids it contains."""
        seen = _seen if _seen is not None else set()
        tag_id = _qualify(tag_id.lstrip("#"))
        if tag_id in seen:
            return set()
        seen.add(tag_id)
        out: set[str] = set()
        for value in self.tags.get(registry, {}).get(tag_id, []):
            if value.startswith("#"):
                out |= self.resolve_tag(registry, value, seen)
            elif value:
                out.add(_qualify(value))
        return out

    def message(self, key: str) -> str | None:
        return self.lang.get(key)

    @property
    def summary(self) -> str:
        parts = [f"{name} {len(ids)}" for name, ids in sorted(self.registries.items())]
        tags = sum(len(entries) for entries in self.tags.values())
        return (
            f"{self.version_id or '?'} (data {self.data_version}, "
            f"pack_format {self.pack_format[0]}.{self.pack_format[1]}) — "
            f"{', '.join(parts)}, {tags} tags, {len(self.lang)} strings"
        )

    def __repr__(self) -> str:
        return f"<VanillaAssets {self.version_id} from {self.jar_path.name}>"


def _qualify(resource_id: str) -> str:
    resource_id = resource_id.strip()
    if resource_id.startswith("#"):
        resource_id = resource_id[1:]
    return resource_id if ":" in resource_id else f"minecraft:{resource_id}"


class VanillaLibrary:
    """Finds, downloads and caches client jars."""

    def __init__(self, cache_dir: Path | str, search_dirs: Iterable[Path] = SEARCH_DIRS):
        self.cache_dir = Path(cache_dir)
        self.search_dirs = [Path(directory) for directory in search_dirs]
        self._loaded: dict[str, VanillaAssets] = {}

    # -- discovery --------------------------------------------------------

    def local_jars(self) -> dict[str, Path]:
        """``version id -> jar path`` for every jar already on this machine."""
        found: dict[str, Path] = {}
        for directory in [*self.search_dirs, self.cache_dir]:
            if not directory.is_dir():
                continue
            for jar in sorted(directory.glob("*/*.jar")):
                if jar.name.endswith("-sources.jar"):
                    continue
                version = jar.parent.name
                found.setdefault(version, jar)
        return found

    def find(self, version_id: str) -> Path | None:
        return self.local_jars().get(version_id)

    # -- download ---------------------------------------------------------

    @staticmethod
    @lru_cache(maxsize=1)
    def manifest() -> dict:
        with urllib.request.urlopen(MANIFEST_URL, timeout=30) as response:
            return json.loads(response.read())

    def download(
        self,
        version_id: str,
        progress: Progress | None = None,
        on_bytes: ByteProgress | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Fetch ``version_id``'s client jar into the cache and return its path.

        The jar is streamed to a ``.part`` file, checked against the SHA-1
        Mojang publishes, then renamed into place — so an interrupted or
        cancelled download never leaves a truncated jar behind.
        """
        target = self.cache_dir / version_id / f"minecraft-{version_id}-client.jar"
        if target.is_file():
            return target

        def say(text: str) -> None:
            log.info("%s", text)
            if progress is not None:
                progress(text)

        def check_cancel() -> None:
            if cancelled is not None and cancelled():
                raise DownloadCancelled(f"download of {version_id} cancelled")

        say(f"looking up {version_id} in Mojang's version manifest")
        entry = next(
            (item for item in self.manifest().get("versions", []) if item["id"] == version_id),
            None,
        )
        if entry is None:
            raise KeyError(f"Mojang's manifest has no version {version_id!r}")
        check_cancel()

        with urllib.request.urlopen(entry["url"], timeout=30) as response:
            meta = json.loads(response.read())
        client = meta.get("downloads", {}).get("client", {})
        url = client.get("url")
        if not url:
            raise KeyError(f"{version_id} has no client download")
        expected_size = int(client.get("size", 0) or 0)
        expected_sha1 = str(client.get("sha1", "") or "").lower()

        say(f"downloading {url.rsplit('/', 1)[-1]} ({expected_size // 1024} KiB)")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        digest = hashlib.sha1()
        received = 0
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
                total = expected_size or int(response.headers.get("Content-Length", 0) or 0)
                if on_bytes is not None:
                    on_bytes(0, total)
                while True:
                    check_cancel()
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if on_bytes is not None:
                        on_bytes(received, total)
            if expected_sha1 and digest.hexdigest() != expected_sha1:
                raise OSError(
                    f"{version_id} client jar is corrupt: sha1 {digest.hexdigest()} "
                    f"does not match {expected_sha1}"
                )
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        verified = ", sha1 verified" if expected_sha1 else ""
        say(f"saved {target} ({received // 1024} KiB{verified})")
        return target

    # -- loading ----------------------------------------------------------

    def load(
        self,
        version_id: str,
        allow_download: bool = False,
        progress: Progress | None = None,
    ) -> VanillaAssets | None:
        """Assets for ``version_id``, or ``None`` if no jar is available."""
        cached = self._loaded.get(version_id)
        if cached is not None:
            return cached
        jar = self.find(version_id)
        if jar is None:
            if not allow_download:
                return None
            jar = self.download(version_id, progress)
        assets = VanillaAssets.from_jar(jar)
        self._loaded[version_id] = assets
        return assets

    def load_jar(self, jar_path: Path | str) -> VanillaAssets:
        assets = VanillaAssets.from_jar(jar_path)
        if assets.version_id:
            self._loaded[assets.version_id] = assets
        return assets

    def loaded(self) -> dict[str, VanillaAssets]:
        return dict(self._loaded)

    def __repr__(self) -> str:
        return f"<VanillaLibrary cache={self.cache_dir} local={len(self.local_jars())}>"


#: the library the app uses; the cache lives next to samples/ and projects/
def default_library() -> VanillaLibrary:
    from datapack_emulator.settings import PATHS

    return VanillaLibrary(PATHS.VANILLA_CACHE)
