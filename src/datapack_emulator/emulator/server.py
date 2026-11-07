"""A vanilla dedicated server, run and driven from Python.

Shared by :mod:`calibration` (``/perf`` reports of a benchmark pack) and
:mod:`server_check` (what a real server refuses in a pack). The server runs
offline in a folder of its own, on a flat world with no players, and is
driven through its console; :meth:`DedicatedServer.stop` always stops it.

Running a Minecraft server means accepting the Minecraft EULA
(https://aka.ms/MinecraftEULA): :func:`prepare` refuses unless told the user
did (``accept_eula=True``, ``--accept-eula`` on the command line).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from datapack_emulator.emulator.versions import Version

log = logging.getLogger(__name__)

Progress = Callable[[str], None]

EULA_URL = "https://aka.ms/MinecraftEULA"

SERVER_PROPERTIES = {
    "online-mode": "false",
    "level-type": "minecraft\\:flat",
    "generate-structures": "false",
    "view-distance": "3",
    "simulation-distance": "3",
    "spawn-protection": "0",
    "max-tick-time": "-1",
    "spawn-monsters": "false",
    "spawn-animals": "false",
    "spawn-npcs": "false",
    "level-name": "world",
    "sync-chunk-writes": "false",
    # an empty server stops ticking after a minute (1.21.2+): never here
    "pause-when-empty-seconds": "-1",
    "enable-rcon": "false",
    "enable-query": "false",
    "log-ips": "false",
}


def download_server_jar(
    version: Version, cache_dir: Path, progress: Progress | None = None
) -> Path:
    """Mojang's dedicated server jar for ``version``, cached and SHA-1 checked."""
    from datapack_emulator.emulator.vanilla import VanillaLibrary

    target = cache_dir / version.id / f"minecraft-{version.id}-server.jar"
    if target.is_file():
        return target
    entry = next(
        (v for v in VanillaLibrary.manifest().get("versions", []) if v["id"] == version.id), None
    )
    if entry is None:
        raise KeyError(f"Mojang's manifest has no version {version.id!r}")
    with urllib.request.urlopen(entry["url"], timeout=30) as response:
        meta = json.loads(response.read())
    server = meta.get("downloads", {}).get("server", {})
    if not server.get("url"):
        raise KeyError(f"{version.id} has no server download")
    if progress:
        size = int(server.get("size", 0)) // 1024
        progress(f"downloading the {version.id} server jar ({size} KiB)")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha1()
    try:
        with (
            urllib.request.urlopen(server["url"], timeout=120) as response,
            partial.open("wb") as out,
        ):
            while chunk := response.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
        expected = str(server.get("sha1", "")).lower()
        if expected and digest.hexdigest() != expected:
            raise OSError(f"server jar is corrupt: sha1 {digest.hexdigest()} != {expected}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


class DedicatedServer:
    """A dedicated server driven through its console."""

    def __init__(self, jar: Path, workdir: Path, java: str, memory: str, progress: Progress):
        self.workdir = workdir
        self.progress = progress
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.process = subprocess.Popen(
            [java, f"-Xmx{memory}", f"-Xms{memory}", "-jar", str(jar), "--nogui"],
            cwd=workdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._lock:
                self.lines.append(line.rstrip("\n"))

    def wait_for(self, text: str, timeout: float, since: int = 0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for line in self.lines[since:]:
                    if text in line:
                        return line
            if self.process.poll() is not None:
                raise RuntimeError("the server exited:\n" + "\n".join(self.lines[-30:]))
            time.sleep(0.2)
        raise TimeoutError(f"waited {timeout:.0f}s for {text!r}:\n" + "\n".join(self.lines[-30:]))

    def command(self, line: str) -> int:
        assert self.process.stdin is not None
        with self._lock:
            mark = len(self.lines)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return mark

    def perf(self) -> Path:
        mark = self.command("perf start")
        done = self.wait_for("Created debug report in", 60, since=mark)
        name = done.rsplit(" ", 1)[-1].strip()
        report = self.workdir / "debug" / "profiling" / name
        if not report.is_file():
            raise FileNotFoundError(f"the server said it wrote {report}, which is not there")
        return report

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.command("stop")
                self.process.wait(60)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()


def require_eula(accept_eula: bool) -> None:
    if not accept_eula:
        raise PermissionError(
            "running a Minecraft server means accepting the Minecraft EULA "
            f"({EULA_URL}); pass accept_eula=True / --accept-eula "
            "only if the user agreed to it"
        )


def prepare(
    version: Version,
    workdir: Path,
    *,
    accept_eula: bool,
    jar: Path | None = None,
    cache_dir: Path | None = None,
    java: str = "java",
    progress: Progress | None = None,
    properties: dict[str, str] | None = None,
) -> Path:
    """Get a server folder ready (EULA, properties) and return the jar to run.

    Nothing is written before the EULA check passes.
    """
    require_eula(accept_eula)
    say = progress or (lambda text: log.info("%s", text))
    if shutil.which(java) is None and not Path(java).is_file():
        raise FileNotFoundError(
            f"no Java runtime {java!r}: install one (Minecraft {version.id} needs it)"
        )
    if jar is None:
        from datapack_emulator.settings import PATHS

        jar = download_server_jar(version, cache_dir or PATHS.CACHE / "servers", say)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    settings = {**SERVER_PROPERTIES, **(properties or {}), "server-port": str(free_port())}
    (workdir / "server.properties").write_text(
        "".join(f"{key}={value}\n" for key, value in settings.items()), encoding="utf-8"
    )
    return jar


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


__all__ = [
    "DedicatedServer",
    "EULA_URL",
    "SERVER_PROPERTIES",
    "download_server_jar",
    "free_port",
    "prepare",
    "require_eula",
]
