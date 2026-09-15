"""Projects: what you were working on, saved next to ``samples/``.

A project is saved as one ``.dpemu`` file: a zip holding ``project.json``
and a copy of every datapack it analyzes under ``datapacks/<n>/<name>/`` (in
load order), so one file carries the settings, the tests and the packs. Older
archives with a single ``datapack/`` and plain ``.json`` projects, which point
at the datapack where it is, still open; saving them writes the new layout.

``project.json`` looks like:

    {
      "name": "hat",
      "datapacks": ["datapacks/0/hat", "datapacks/1/extras"],
      "version": "1.21.4",
      "ticks": 20, "players": 1, "seed": 0,
      "engine_versions": ["1.20.4", "1.21.4"],
      "vanilla_jar": "",
      "speed": "fast",
      "tests": [{"command": "function hat:tick", "at_tick": 5, "expect": "", "enabled": true}]
    }

An archive is unpacked into the cache when opened; the datapacks are used from
there and written back into the archive, with everything else, when saved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from datapack_emulator.settings import EMULATION, PATHS

log = logging.getLogger(__name__)

#: a zip with project.json and the datapack — the only format written
SUFFIX = ".dpemu"
#: plain JSON projects from before archives; read, never written
LEGACY_SUFFIX = ".json"
SUFFIXES = (SUFFIX, LEGACY_SUFFIX)
ARCHIVE_MANIFEST = "project.json"
#: archive format 1 held one pack here
ARCHIVE_DATAPACK = "datapack"
ARCHIVE_DATAPACKS = "datapacks"
#: bumped when the archive layout changes incompatibly (2: several datapacks)
ARCHIVE_FORMAT = 2
#: never copied into an archive
SKIPPED_NAMES = frozenset({".git", "__pycache__", ".DS_Store"})


def projects_dir() -> Path:
    PATHS.PROJECTS.mkdir(parents=True, exist_ok=True)
    return PATHS.PROJECTS


def list_projects() -> list[Path]:
    if not PATHS.PROJECTS.is_dir():
        return []
    return sorted(path for path in PATHS.PROJECTS.iterdir() if path.suffix in SUFFIXES)


def _relative(path: Path | None) -> str:
    """Inside the repo -> relative; anywhere else -> absolute."""
    if path is None:
        return ""
    path = Path(path)
    try:
        return path.resolve().relative_to(PATHS.ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _absolute(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (PATHS.ROOT / path)


@dataclass
class Project:
    """Everything the window needs to pick up where it left off."""

    name: str = "untitled"
    #: the datapacks analyzed together, in load order (later packs win)
    datapacks: list[Path] = field(default_factory=list)
    version: str = ""
    ticks: int = EMULATION.DEFAULT_TICKS
    players: int = EMULATION.DEFAULT_PLAYERS
    seed: int = EMULATION.DEFAULT_SEED
    engine_versions: list[str] = field(default_factory=list)
    vanilla_jar: str = ""
    #: "fast" or "realtime"
    speed: str = "fast"
    #: CommandTest.to_dict() entries from the environment tab
    tests: list[dict[str, Any]] = field(default_factory=list)
    #: run the tests during run all / run emulator / step
    tests_during_runs: bool = False
    #: the user's own notes: about the project, and per function id
    notes: str = ""
    function_notes: dict[str, str] = field(default_factory=dict)
    #: where it was saved; ``None`` until the first save
    path: Path | None = None

    # -- serialising ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datapacks": [_relative(path) for path in self.datapacks],
            "version": self.version,
            "ticks": self.ticks,
            "players": self.players,
            "seed": self.seed,
            "engine_versions": list(self.engine_versions),
            "vanilla_jar": self.vanilla_jar,
            "speed": self.speed,
            "tests": [dict(test) for test in self.tests],
            "tests_during_runs": self.tests_during_runs,
            "notes": self.notes,
            "function_notes": dict(sorted(self.function_notes.items())),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> Project:
        return cls(
            name=str(data.get("name") or (path.stem if path else "untitled")),
            datapacks=_datapack_paths(data),
            version=str(data.get("version", "")),
            ticks=int(data.get("ticks", EMULATION.DEFAULT_TICKS)),
            players=int(data.get("players", EMULATION.DEFAULT_PLAYERS)),
            seed=int(data.get("seed", EMULATION.DEFAULT_SEED)),
            engine_versions=[str(item) for item in data.get("engine_versions", [])],
            vanilla_jar=str(data.get("vanilla_jar", "")),
            speed=str(data.get("speed", "fast")),
            tests=[dict(test) for test in data.get("tests", []) if isinstance(test, dict)],
            tests_during_runs=bool(data.get("tests_during_runs", False)),
            notes=str(data.get("notes", "")),
            function_notes={
                str(key): str(value)
                for key, value in (data.get("function_notes") or {}).items()
                if str(value).strip()
            }
            if isinstance(data.get("function_notes"), dict)
            else {},
            path=path,
        )

    # -- files ------------------------------------------------------------

    def default_path(self) -> Path:
        return projects_dir() / f"{_safe_name(self.name)}{SUFFIX}"

    def save(self, path: Path | None = None) -> Path:
        """Write the ``.dpemu`` archive, datapack included.

        Any other suffix is replaced, so a legacy ``hat.json`` saves as
        ``hat.dpemu`` next to it.
        """
        target = Path(path) if path else (self.path or self.default_path())
        if target.suffix == LEGACY_SUFFIX:
            target = target.with_suffix(SUFFIX)
        elif target.suffix != SUFFIX:
            target = target.with_name(target.name + SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write_archive(target)
        self.path = target
        log.info("saved project %s", target)
        return target

    @property
    def datapack(self) -> Path | None:
        """The first datapack (older code and single-pack projects)."""
        return self.datapacks[0] if self.datapacks else None

    def _write_archive(self, target: Path) -> None:
        data = self.to_dict()
        data["archive_format"] = ARCHIVE_FORMAT
        packs = [path for path in self.datapacks if path.is_dir()]
        folders = [
            f"{ARCHIVE_DATAPACKS}/{index}/{_safe_name(path.name)}"
            for index, path in enumerate(packs)
        ]
        data["datapacks"] = folders
        # write next to the target and swap, so a failed save keeps the old file
        partial = target.with_name(target.name + ".part")
        # listed before the .part exists; an archive saved inside its own pack
        # must not swallow itself
        contents = [
            (folder, pack, _pack_files(pack, exclude={target, partial}))
            for folder, pack in zip(folders, packs, strict=True)
        ]
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(ARCHIVE_MANIFEST, _dump(data))
                for folder, pack, files in contents:
                    for file in files:
                        name = PurePosixPath(folder, *file.relative_to(pack).parts)
                        archive.write(file, str(name))
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path | str, unpack_to: Path | None = None) -> Project:
        """Unpack a ``.dpemu`` archive and read it, or read a legacy ``.json``.

        Archives unpack into ``unpack_to`` (default: the cache), replacing any
        earlier copy: the archive is what counts.
        """
        path = Path(path)
        if path.suffix == SUFFIX or zipfile.is_zipfile(path):
            return cls._load_archive(path, unpack_to)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls._parse(data, path)

    @classmethod
    def _parse(cls, data: Any, path: Path) -> Project:
        """``from_dict`` for file contents: anything malformed is a ValueError."""
        if not isinstance(data, dict):
            raise ValueError("the project data is not a JSON object")
        try:
            return cls.from_dict(data, path=path)
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"malformed project data: {exc}") from exc

    @classmethod
    def _load_archive(cls, path: Path, unpack_to: Path | None) -> Project:
        target = unpack_to or unpacked_dir(path)
        try:
            with zipfile.ZipFile(path) as archive:
                data = json.loads(archive.read(ARCHIVE_MANIFEST).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError(f"{ARCHIVE_MANIFEST} is not a JSON object")
                version = data.get("archive_format", ARCHIVE_FORMAT)
                if not isinstance(version, int) or version > ARCHIVE_FORMAT:
                    raise ValueError(f"made by a newer version (archive format {version})")
                _check_names(archive, target)  # before the old copy is removed
                if target.exists():
                    shutil.rmtree(target)
                target.mkdir(parents=True)
                archive.extractall(target)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ValueError(f"not a project archive: {exc}") from exc
        if version < 2:  # one pack under datapack/
            folders = [ARCHIVE_DATAPACK] if data.get("datapack") else []
        else:
            folders = [
                str(entry) for entry in data.get("datapacks") or [] if isinstance(entry, str)
            ]
        data.pop("datapack", None)
        root = target.resolve()
        for folder in folders:
            resolved = (root / folder).resolve()
            if resolved == root or root not in resolved.parents:
                raise ValueError(f"unsafe datapack path in the project: {folder}")
        data["datapacks"] = [
            str(target / folder) for folder in folders if (target / folder).is_dir()
        ]
        return cls._parse(data, path)

    def renamed(self, name: str) -> Project:
        """A copy under a new name, not yet written anywhere."""
        return replace(self, name=name, path=None)

    @property
    def title(self) -> str:
        return self.name if self.path else f"{self.name} (unsaved)"

    def __repr__(self) -> str:
        return f"<Project {self.name} datapacks={[path.name for path in self.datapacks]}>"


def _datapack_paths(data: dict[str, Any]) -> list[Path]:
    """``datapacks`` (a list), or the single ``datapack`` of older projects."""
    entries = data.get("datapacks")
    if not isinstance(entries, list):
        entries = [data.get("datapack", "")]
    paths = [_absolute(str(entry)) for entry in entries if isinstance(entry, str) and entry]
    return [path for path in paths if path is not None]


def unpacked_dir(archive: Path) -> Path:
    """Where an archive's datapack lives while its project is open."""
    digest = hashlib.sha1(str(Path(archive).resolve()).encode()).hexdigest()[:10]
    return PATHS.CACHE / "projects" / f"{_safe_name(Path(archive).stem)}-{digest}"


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _pack_files(pack: Path, exclude: set[Path] = frozenset()) -> list[Path]:
    """Regular files of the pack, sorted. Symlinks (files and folders alike)
    are left out with a warning: following them could archive files from
    outside the pack or loop forever."""
    excluded = {path.resolve() for path in exclude}
    files = []
    for folder, directories, names in os.walk(pack):
        base = Path(folder)
        kept = []
        for name in sorted(directories):
            if name in SKIPPED_NAMES:
                continue
            if (base / name).is_symlink():
                log.warning("not archived (symbolic link): %s", base / name)
                continue
            kept.append(name)
        directories[:] = kept
        for name in sorted(names):
            file = base / name
            if name in SKIPPED_NAMES or file.resolve() in excluded:
                continue
            if file.is_symlink():
                log.warning("not archived (symbolic link): %s", file)
                continue
            files.append(file)
    return files


def _check_names(archive: zipfile.ZipFile, target: Path) -> None:
    """Refuse archives with names that would land outside ``target``."""
    root = target.resolve()
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        if destination != root and root not in destination.parents:
            raise ValueError(f"unsafe path in archive: {member.filename}")


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ ." else "-" for char in name)
    return cleaned.strip().strip(".") or "untitled"


def default_sample() -> Path | None:
    """The datapack to open on a cold start: the first one in ``samples/``."""
    if not PATHS.SAMPLES.is_dir():
        return None
    for candidate in sorted(PATHS.SAMPLES.iterdir()):
        if candidate.is_dir() and (candidate / "pack.mcmeta").is_file():
            return candidate
    return None
