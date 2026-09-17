"""Minecraft versions, pack formats and per-version feature availability.

The raw table lives in :mod:`datapack_emulator.emulator.version_data`, which is generated
from `misode/mcmeta <https://github.com/misode/mcmeta>`_ by
``tools/generate_version_data.py``.  This module is the hand-written layer on
top: ordering, parsing, ranges, pack-format lookup and the feature queries the
rest of the emulator asks (``does this version have /return run?``).

Every version-dependent decision in the project goes through here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache

from datapack_emulator.emulator.version_data import (
    FEATURE_SINCE,
    FEATURE_UNTIL,
    PRERELEASES,
    RELEASES,
)

#: mcmeta has no command tree before this release, so feature answers for
#: older versions are answered as if they were this one.
FEATURE_FLOOR = "1.14"

#: 1.13 shares pack_format 4 with 1.14 but predates the generated table.
EXTRA_RELEASES: tuple[tuple[str, int, int, int], ...] = (
    ("1.13", 4, 0, 1519),
    ("1.13.1", 4, 0, 1628),
    ("1.13.2", 4, 0, 1631),
)


_ID_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:-(?P<stage>pre|rc)-(?P<build>\d+))?$")
_STAGES = {"pre": 0, "rc": 1, None: 2}  # a release sorts after its pre-releases


def _key(identifier: str) -> tuple[int, ...]:
    """Sort key: ``"1.21.10"`` -> ``(1, 21, 10, 2, 0)``, ``"26.3-rc-3"`` ->
    ``(26, 3, 0, 1, 3)`` — numerically, and pre-releases before their release."""
    match = _ID_RE.match(identifier)
    if match is None:
        parts = re.findall(r"\d+", identifier)
        return tuple(int(part) for part in parts) or (0,)
    numbers = [int(part) for part in match.group("number").split(".")]
    numbers += [0] * (3 - len(numbers))
    return (*numbers, _STAGES[match.group("stage")], int(match.group("build") or 0))


@dataclass(frozen=True, order=True)
class Version:
    """One Minecraft release, ordered by its numeric components."""

    sort_key: tuple[int, ...] = field(compare=True)
    id: str = field(compare=False)
    pack_format: int = field(compare=False, default=0)
    pack_format_minor: int = field(compare=False, default=0)
    data_version: int = field(compare=False, default=0)
    #: False for a pre-release or release candidate
    stable: bool = field(compare=False, default=True)

    @property
    def format(self) -> tuple[int, int]:
        return (self.pack_format, self.pack_format_minor)

    @property
    def format_string(self) -> str:
        if self.pack_format_minor:
            return f"{self.pack_format}.{self.pack_format_minor}"
        return str(self.pack_format)

    def __str__(self) -> str:
        return self.id


def _build() -> tuple[Version, ...]:
    rows = [(row, True) for row in (*EXTRA_RELEASES, *RELEASES)]
    rows += [(row, False) for row in PRERELEASES]
    versions = [
        Version(_key(identifier), identifier, pack_format, minor, data_version, stable)
        for (identifier, pack_format, minor, data_version), stable in rows
    ]
    return tuple(sorted(versions))


#: every known release, oldest first
VERSIONS: tuple[Version, ...] = _build()
BY_ID: dict[str, Version] = {version.id: version for version in VERSIONS}
OLDEST: Version = VERSIONS[0]
#: the newest stable release — what "latest" and the default version mean
LATEST: Version = [version for version in VERSIONS if version.stable][-1]
#: the newest version known at all, pre-releases included
NEWEST: Version = VERSIONS[-1]


def parse(spec: str | Version | None) -> Version:
    """``"1.21.4"``, ``"latest"``, ``"oldest"`` or a :class:`Version`."""
    if isinstance(spec, Version):
        return spec
    if spec is None or spec == "latest":
        return LATEST
    if spec == "oldest":
        return OLDEST
    text = str(spec).strip()
    if text in BY_ID:
        return BY_ID[text]
    # not a release id: a line like "26" resolves to its newest release ("26.2"),
    # and an unreleased "26.3" to its newest pre-release ("26.3-rc-3"); an exact
    # id always wins, so "1.21" is 1.21, not 1.21.11
    candidates = [
        version for version in VERSIONS if version.id.startswith((text + ".", text + "-"))
    ]
    stable = [version for version in candidates if version.stable]
    if stable or candidates:
        return (stable or candidates)[-1]
    raise KeyError(f"unknown Minecraft version {spec!r}")


def version_range(start: str | Version | None, end: str | Version | None) -> list[Version]:
    """Every release between ``start`` and ``end``, inclusive."""
    low = parse(start) if start is not None else OLDEST
    high = parse(end) if end is not None else NEWEST
    if high < low:
        low, high = high, low
    return [version for version in VERSIONS if low <= version <= high]


def for_pack_format(pack_format: float | int | None) -> list[Version]:
    """Releases that ship the given ``pack_format`` (``107.1`` style accepted)."""
    if pack_format is None:
        return []
    major = int(pack_format)
    minor = round((float(pack_format) - major) * 10)
    exact = [version for version in VERSIONS if version.format == (major, minor)]
    if exact:
        return exact
    return [version for version in VERSIONS if version.pack_format == major]


def closest_to_pack_format(pack_format: float | int | None) -> Version:
    """The newest release whose pack_format is <= the one requested."""
    matches = for_pack_format(pack_format)
    if matches:
        return matches[-1]
    if pack_format is None:
        return LATEST
    older = [version for version in VERSIONS if version.pack_format <= int(pack_format)]
    return older[-1] if older else OLDEST


def format_to_version_string(pack_format: float | int | None) -> str:
    """Human-readable ``"1.21 – 1.21.1"`` for a pack format."""
    matches = for_pack_format(pack_format)
    if not matches:
        return "unknown"
    if len(matches) == 1:
        return matches[0].id
    return f"{matches[0].id} – {matches[-1].id}"


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


@cache
def supports(version: Version, feature: str) -> bool:
    """Is ``feature`` (e.g. ``"command:return"``) present in ``version``?"""
    since = FEATURE_SINCE.get(feature)
    if since is None:
        return False
    effective = max(version, parse(FEATURE_FLOOR))
    if effective < parse(since):
        return False
    until = FEATURE_UNTIL.get(feature)
    return until is None or effective < parse(until)


@cache
def vanilla_commands(version: Version) -> frozenset[str]:
    """Every command name the vanilla game knows in ``version``."""
    return frozenset(
        key.split(":", 1)[1]
        for key in FEATURE_SINCE
        if key.startswith("command:") and supports(version, key)
    )


def feature_keys(prefix: str) -> list[str]:
    return sorted(key for key in FEATURE_SINCE if key.startswith(prefix + ":"))


def since_of(feature: str) -> Version | None:
    since = FEATURE_SINCE.get(feature)
    return parse(since) if since else None


# ---------------------------------------------------------------------------
# structural rules that are not commands
# ---------------------------------------------------------------------------

#: ``data/<ns>/functions`` became ``data/<ns>/function`` (24w21a / pack_format 45)
SINGULAR_REGISTRIES_SINCE = "1.21"
SINGULAR_REGISTRIES_FORMAT = (45, 0)
#: ``pack.mcmeta`` gained ``overlays`` and ``supported_formats`` (23w32a)
OVERLAYS_SINCE = "1.20.2"
#: ``pack.mcmeta`` gained ``min_format`` / ``max_format`` (25w31a)
MIN_MAX_FORMAT_SINCE = "1.21.9"
#: ``$`` macro lines, i.e. ``function ns:f with ...``
MACROS_SINCE = "1.20.2"


def uses_singular_registries(version: Version) -> bool:
    return version >= parse(SINGULAR_REGISTRIES_SINCE)


def singular_registries_for_format(pack_format: tuple[int, int] | None) -> bool:
    """Folder spelling when only a pack format is known (no format: modern)."""
    return pack_format is None or pack_format >= SINGULAR_REGISTRIES_FORMAT


def supports_overlays(version: Version) -> bool:
    return version >= parse(OVERLAYS_SINCE)


def supports_min_max_format(version: Version) -> bool:
    return version >= parse(MIN_MAX_FORMAT_SINCE)


#: ``weather <kind> <duration>`` took seconds before it took a time argument (22w46a)
WEATHER_TIME_SINCE = "1.19.3"


def weather_takes_time(version: Version) -> bool:
    return version >= parse(WEATHER_TIME_SINCE)


#: ``strict`` placement for setblock, fill and clone (25w03a)
STRICT_PLACEMENT_SINCE = "1.21.5"
#: the commandModificationBlockLimit gamerule (23w03a); a fixed 32 768 before
MODIFICATION_LIMIT_RULE_SINCE = "1.19.4"


def supports_strict_placement(version: Version) -> bool:
    return version >= parse(STRICT_PLACEMENT_SINCE)


def has_modification_limit_rule(version: Version) -> bool:
    return version >= parse(MODIFICATION_LIMIT_RULE_SINCE)


def supports_macros(version: Version) -> bool:
    return version >= parse(MACROS_SINCE)


def iter_versions(spec: Iterable[str | Version]) -> Iterator[Version]:
    for item in spec:
        yield parse(item)
