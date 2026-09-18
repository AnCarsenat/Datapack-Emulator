"""Finding things in a pack: quick open by id and text search in functions.

The window's *quick open* / *search in pack* and ``datapack-emulator-cli
search`` use these.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datapack_emulator.emulator.datapack import PackView


@dataclass(frozen=True)
class Hit:
    label: str
    path: Path
    line: int = 0


def open_candidates(view: PackView) -> list[Hit]:
    """Every function and tag by id, and every other file by its path."""
    hits = [Hit(f"{fid}  (function)", f.path) for fid, f in sorted(view.functions.items())]
    hits += [Hit(f"{tid}  (function tag)", t.path) for tid, t in sorted(view.function_tags.items())]
    return hits


def id_hits(view: PackView, text: str) -> list[Hit]:
    """Functions and tags whose id contains ``text`` (case-insensitive)."""
    wanted = text.lower()
    return [hit for hit in open_candidates(view) if wanted in hit.label.split("  ")[0].lower()]


def text_hits(view: PackView, text: str) -> list[Hit]:
    """Every function line containing ``text`` (case-insensitive)."""
    wanted = text.lower()
    hits: list[Hit] = []
    for function_id, function in sorted(view.functions.items()):
        try:
            lines = function.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            if wanted in line.lower():
                hits.append(Hit(f"{function_id}:{number}  {line.strip()}", function.path, number))
    return hits
