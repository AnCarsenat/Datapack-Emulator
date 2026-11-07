"""What a real server refuses in a pack: run it on a vanilla dedicated server
and turn the server's log into problems.

The static check (:mod:`analysis.problems`) knows a lot, but only a server
knows everything its codecs reject. This starts one (offline, flat world, no
players, a fresh world each time) with the packs installed, waits until it
is up -- or until it gives up loading the world -- stops it, and reads its
log. Every line the server writes about one of the pack's resources becomes
a :class:`Problem`:

=========================================================  ========  ==================
server log                                                 severity  code
=========================================================  ========  ==================
``Registry loading errors`` (a worldgen, dimension, ...    fatal     ``world-registry``
element that does not parse)
``Failed to load datapacks, can't proceed with server      fatal     ``pack``
load`` (the world does not start, for any pack)
``Failed to load function ns:f`` + ``Whilst parsing        error     ``function-not-loaded``
command on line N: ...``
``Couldn't load tag ...  missing following references``    error     ``tag-not-loaded``
``Couldn't parse data file 'ns:x' from 'ns:folder/x.json'``  error   by folder (``json``,
                                                                     ``loot-table``, ...)
``Error reading pack metadata``                            warning   ``pack``
=========================================================  ========  ==================

Lines about resources of other packs (or of the world itself) are left out.
Running a server means accepting the Minecraft EULA; see :mod:`server`.
Tested against 26.2's log; earlier versions word some lines differently.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import server as dedicated
from datapack_emulator.emulator.analysis.problems import SEVERITIES, Problem, count
from datapack_emulator.emulator.versions import Version

log = logging.getLogger(__name__)

STARTED = "Done ("
GAVE_UP = "Failed to load datapacks, can't proceed with server load"

_LOG_LINE = re.compile(r"^\[[\d:]+\] \[(?P<thread>[^\]]*)/(?P<level>[A-Z]+)\]: (?P<message>.*)$")
_REGISTRY = re.compile(r"^> Errors in registry (?P<registry>\S+?):$")
_ELEMENT = re.compile(r"^>> Errors in element (?P<element>\S+?):$")
_CAUSE = re.compile(r"^(?:Caused by: )?[\w.$]+(?:Exception|Error): (?P<message>.*)$")
_FUNCTION = re.compile(r"^Failed to load function (?P<id>\S+)$")
_WHILST = re.compile(r"Whilst parsing command on line (?P<line>\d+): (?P<message>.*)$")
_TAG = re.compile(
    r"^Couldn't load tag (?P<id>\S+) as it is missing following references: (?P<refs>.*?)"
    r"(?: \(from (?P<pack>[^)]*)\))?$"
)
_DATA_FILE = re.compile(
    r"^Couldn't parse data file '(?P<id>[^']+)' from '(?P<file>[^']+)'(?:: (?P<message>.*))?$"
)
_DATA_RESULT = re.compile(r"^DataResult\.Error\['(?P<inner>.*)'(?:: .*)?\]$")
_FROM_PACK = re.compile(r"\bfrom pack (?P<pack>\S+)$")
_UNBOUND = re.compile(r"Unbound values in registry .*?: \[(?P<ids>[^\]]*)\]")

#: the folder of a data file -> the problem code the static check uses for it
FOLDER_CODES = {
    "loot_table": "loot-table",
    "recipe": "recipe",
    "advancement": "advancement",
    "predicate": "condition",
    "item_modifier": "item-function",
}


def _namespace(resource_id: str) -> str:
    return resource_id.lstrip("#").split(":", 1)[0] if ":" in resource_id else "minecraft"


def _mine(resource_id: str, namespaces: set[str]) -> bool:
    return _namespace(resource_id) in namespaces


def _cause(lines: list[str], start: int) -> str:
    """The deepest ``Caused by`` (else the first exception) from ``start``
    until the next log line or registry marker."""
    message = ""
    for line in lines[start:]:
        if _LOG_LINE.match(line) or line.startswith(">"):
            break
        match = _CAUSE.match(line.strip())
        if match:
            message = match["message"]
    return message


def _from_pack(lines: list[str], start: int) -> str | None:
    """The pack a registry element's failure names (``Failed to parse x from
    pack file/p``), from ``start`` until the next log line or marker."""
    for line in lines[start:]:
        if _LOG_LINE.match(line) or line.startswith(">"):
            break
        match = _FROM_PACK.search(line.strip())
        if match:
            return match["pack"]
    return None


def _short(message: str, limit: int = 300) -> str:
    match = _DATA_RESULT.match(message)
    if match:
        message = match["inner"]
    # the server quotes the whole offending object ("MapLike[{...}]"): keep it short
    message = re.sub(r"MapLike\[\{.{60,}\}\]", "the object", message)
    return message if len(message) <= limit else message[: limit - 1] + "…"


def parse_log(
    text: str, namespaces: Iterable[str], pack_names: Iterable[str] = ()
) -> list[Problem]:
    """The problems a server log reports about resources in ``namespaces``.

    ``pack_names`` (``file/<name>`` as the server calls them): a tag or
    registry line that names its pack counts if, and only if, that pack is
    one of them -- so a pack writing into ``minecraft:`` does not take on
    lines about another pack's (or vanilla's) ``minecraft:`` resources.
    """
    mine = {namespace.lower() for namespace in namespaces}
    packs = {f"file/{name}" for name in pack_names}
    lines = text.splitlines()
    problems: list[Problem] = []
    registry = ""
    failed_elements: set[str] = set()
    #: elements other files refer to that did not load; reported only when
    #: the element's own failure is not (the log lists those blocks first)
    unbound_found: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        marker = _REGISTRY.match(stripped)
        if marker:
            registry = marker["registry"]
            continue
        element = _ELEMENT.match(stripped)
        if element:
            element_id = element["element"]
            cause = _cause(lines, index + 1)
            if registry == "minecraft:root":
                unbound = _UNBOUND.search(cause)
                ids = [i.strip() for i in unbound["ids"].split(",")] if unbound else []
                for resource in (i for i in ids if _mine(i, mine)):
                    unbound_found.append((element_id, resource))
                continue
            source = _from_pack(lines, index + 1)
            # the line names the pack: trust it over the namespace, which
            # vanilla shares with any pack that writes into minecraft:
            if source is not None and packs:
                ours = source in packs
            else:
                ours = _mine(element_id, mine)
            if ours:
                failed_elements.add(element_id)
                problems.append(
                    Problem(
                        "fatal",
                        "world-registry",
                        f"server: {registry.removeprefix('minecraft:')} {element_id} does not "
                        f"load: {_short(cause) or 'see the server log'}",
                        element_id,
                    )
                )
            continue
        entry = _LOG_LINE.match(line)
        if not entry:
            continue
        registry = ""
        message = entry["message"]
        if GAVE_UP in message:
            problems.append(
                Problem(
                    "fatal",
                    "pack",
                    "server: it refused to load the world (\"Failed to load datapacks, can't "
                    'proceed with server load"): nothing of any pack runs until its fatal '
                    "problems are fixed",
                )
            )
            continue
        function = _FUNCTION.match(message)
        if function and _mine(function["id"], mine):
            whilst = next(
                (
                    m
                    for m in (
                        _WHILST.search(lines[j])
                        for j in range(index + 1, min(index + 4, len(lines)))
                    )
                    if m
                ),
                None,
            )
            problems.append(
                Problem(
                    "error",
                    "function-not-loaded",
                    f"server: Failed to load function {function['id']}"
                    + (f": {_short(whilst['message'])}" if whilst else ""),
                    function["id"],
                    None,
                    int(whilst["line"]) if whilst else 0,
                )
            )
            continue
        tag = _TAG.match(message)
        if tag and (tag["pack"] in packs if tag["pack"] and packs else _mine(tag["id"], mine)):
            problems.append(
                Problem(
                    "error",
                    "tag-not-loaded",
                    f"server: tag {tag['id']} does not load: it names {tag['refs']}, which "
                    "do not exist",
                    "#" + tag["id"],
                )
            )
            continue
        data = _DATA_FILE.match(message)
        if data and _mine(data["id"], mine):
            folder = data["file"].split(":", 1)[-1].split("/", 1)[0]
            detail = data["message"] or _cause(lines, index + 1)
            is_json = not data["message"] and "Json" in (
                lines[index + 1] if index + 1 < len(lines) else ""
            )
            problems.append(
                Problem(
                    "error",
                    "json" if is_json else FOLDER_CODES.get(folder, "json"),
                    f"server: {folder} {data['id']} does not load: {_short(detail)}",
                    data["id"],
                )
            )
            continue
        if message.startswith("Error reading pack metadata"):
            detail = _cause(lines, index + 1)
            problems.append(
                Problem(
                    "warning",
                    "pack",
                    "server: a pack's metadata does not read"
                    + (f" ({_short(detail)})" if detail else "")
                    + "; the server falls back to reading it as an older pack",
                )
            )
    for registry_id, resource in unbound_found:
        if resource not in failed_elements:
            problems.append(
                Problem(
                    "fatal",
                    "world-registry",
                    f"server: {registry_id.removeprefix('minecraft:')} {resource} is referred "
                    "to but did not load",
                    resource,
                )
            )
    order = {severity: rank for rank, severity in enumerate(SEVERITIES)}
    return sorted(dict.fromkeys(problems), key=lambda p: (order[p.severity], p.resource, p.line))


@dataclass
class ServerCheck:
    version: str
    started: bool
    problems: list[Problem] = field(default_factory=list)
    log: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "started": self.started,
            "seconds": round(self.seconds, 1),
            "counts": count(self.problems),
            "problems": [problem.to_dict() for problem in self.problems],
            "log": self.log,
        }


def _install(packs: Iterable[Path], datapacks: Path) -> list[str]:
    names = []
    datapacks.mkdir(parents=True, exist_ok=True)
    for pack in packs:
        pack = Path(pack)
        target = datapacks / pack.name
        if pack.is_dir():
            shutil.copytree(pack, target)
        else:
            shutil.copy2(pack, target)
        names.append(pack.name)
    return names


#: written in every folder this module runs a server in: its world and logs
#: are deleted before each run, so a folder without it is never used
MARKER = ".datapack-emulator-server-check"


def claim_workdir(workdir: Path) -> None:
    """Make ``workdir`` ours, or refuse it.

    A run deletes ``workdir/world`` and ``workdir/logs`` and rewrites
    ``server.properties``: pointed at someone's real server folder, that
    would destroy their world. So a folder is used only if it is new, empty,
    or carries :data:`MARKER` from an earlier run.
    """
    marker = workdir / MARKER
    if workdir.exists():
        if not workdir.is_dir():
            raise NotADirectoryError(f"{workdir} is not a folder")
        if not marker.is_file() and any(workdir.iterdir()):
            raise FileExistsError(
                f"{workdir} is not empty and was not made by a server check (no {MARKER}): "
                "each run deletes its world/ and logs/, so it is left alone. Pick a new or "
                "empty folder"
            )
    workdir.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "made by datapack-emulator check --server: its world/ and logs/ are deleted on every run\n",
        encoding="utf-8",
    )


def check_on_server(
    packs: Iterable[Path | str],
    namespaces: Iterable[str],
    version: Version,
    workdir: Path,
    *,
    accept_eula: bool,
    jar: Path | None = None,
    java: str = "java",
    memory: str = "2G",
    timeout: float = 300.0,
    progress: dedicated.Progress | None = None,
) -> ServerCheck:
    """Start a server on a fresh world with ``packs`` installed, stop it once
    it is up (or has given up), and return what its log says about them."""
    say = progress or (lambda text: log.info("%s", text))
    dedicated.require_eula(accept_eula)
    claim_workdir(workdir)
    jar = dedicated.prepare(
        version, workdir, accept_eula=accept_eula, jar=jar, java=java, progress=say
    )
    for stale in ("world", "logs"):
        shutil.rmtree(workdir / stale, ignore_errors=True)
    names = _install([Path(p) for p in packs], workdir / "world" / "datapacks")
    say(f"starting the {version.id} server with {', '.join(names)}")
    started_at = time.monotonic()
    running = dedicated.DedicatedServer(jar, workdir, java, memory, say)
    started = False
    try:
        try:
            running.wait_for(STARTED, timeout)
            started = True
        except RuntimeError:
            started = False  # it exited: the world did not load
    finally:
        running.stop()
    seconds = time.monotonic() - started_at
    log_path = workdir / "logs" / "latest.log"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if not text:
        text = "\n".join(running.lines)
    problems = parse_log(text, namespaces, names)
    if not started and not any(p.code == "pack" and p.severity == "fatal" for p in problems):
        problems.append(
            Problem("fatal", "pack", "server: it stopped before the world loaded; see the log")
        )
    return ServerCheck(version.id, started, problems, str(log_path), seconds)


__all__ = ["MARKER", "ServerCheck", "check_on_server", "claim_workdir", "parse_log"]
