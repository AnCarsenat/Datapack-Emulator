"""Rows for the inspector dock: what is known about the selected thing."""

from __future__ import annotations

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.datapack import Datapack, format_label
from datapack_emulator.emulator.engine import VersionRun
from datapack_emulator.emulator.resources import Function, Resource
from datapack_emulator.emulator.versions import Version

Rows = list[tuple[str, str]]

#: what an inspector row means, by label (or the start of the label)
ROW_HELP: dict[str, str] = {
    "path": "the datapack folder",
    "pack_format": "the format pack.mcmeta declares; before 1.20.2 it is the only one read",
    "supported": "the format range the pack claims (supported_formats, min_format/max_format)",
    "matches versions": "releases whose pack format is in that range",
    "description": "the pack's description from pack.mcmeta",
    "namespaces": "folders under data/",
    "overlays": "overlay folders and the formats they apply to (1.20.2+)",
    "emulating": "the version selected in the environment tab",
    "pack supports it": "whether this version lists the pack as compatible; it loads either way",
    "active overlays": "overlays this version applies on top of the base pack (last listed wins)",
    "functions": "functions this version reads (folder spelling and overlays included)",
    "function tags": "function tags this version reads",
    "icon": "pack.png size",
    "id": "the resource location",
    "registry": "the folder it lives in",
    "source": "base pack, or the overlay it comes from",
    "file": "where it is on disk",
    "commands": "command lines in the function",
    "macro": "whether the function has $ macro lines (1.20.2+)",
    "estimated cost": "cost model estimate of one run, excluding functions it calls",
    "calls": "functions and tags it calls, schedules or tests",
    "unsupported in": "features the function uses that this version lacks: it fails to load",
    "your note": "your own note, saved with the project (right-click › edit note…)",
    "line": "the line being analyzed",
    "from": "where the line comes from",
    "command": "the command and what it does",
    "in the emulator": "how much of it the emulator runs",
    "step": "one execute subcommand, in order: each changes who, where or whether the rest runs",
    "run ›": "the command execute runs at the end",
    "targets": "what a selector matches",
    "references": "a function or tag the line uses, checked against the pack",
    "loads in": "whether a function with this line loads in the version",
    "macro line": "a $ line: filled in with the macro arguments when the function is called",
    "macro support": "macros need 1.20.2",
    "NBT": "the SNBT payload of the command",
    "text": "the text component of tellraw / title",
    "called by": "functions and tags that call it (from the call graph)",
    "edge": "one call graph edge and its kind: call, macro, schedule, condition or tag",
    "error": "a problem reading the file",
}


def row_help(label: str) -> str:
    """The explanation of an inspector row, matching the longest known prefix."""
    stripped = label.removeprefix("run › ")
    best = ""
    for key in ROW_HELP:
        if stripped.startswith(key) and len(key) > len(best):
            best = key
    return ROW_HELP.get(best, "")


def _format(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return format_label(value)
    return str(value)


def describe_datapack(datapack: Datapack, version: Version | None = None) -> Rows:
    minimum, maximum = datapack.format_range
    rows: Rows = [
        ("path", str(datapack.path)),
        ("pack_format", _format(datapack.pack_format)),
        ("supported", f"{_format(minimum)} .. {_format(maximum)}"),
        ("matches versions", datapack.minecraft_version),
        ("description", datapack.description.replace("\n", " ")),
        ("namespaces", ", ".join(datapack.namespaces) or "-"),
    ]
    if datapack.overlays:
        rows.append(
            (
                "overlays",
                ", ".join(layer.entry.describe() for layer in datapack.overlays if layer.entry),
            )
        )
    if version is not None:
        view = datapack.view_for(version)
        rows += [
            ("emulating", f"{version.id} (pack_format {version.format_string})"),
            ("pack supports it", "yes" if datapack.supports(version) else "no"),
            ("active overlays", ", ".join(view.active_overlays) or "-"),
            ("functions", str(len(view.functions))),
            ("function tags", str(len(view.function_tags))),
        ]
    else:
        rows += [
            ("functions", str(len(datapack.functions))),
            ("function tags", str(len(datapack.function_tags))),
        ]
    if datapack.icon is not None and datapack.icon.exists:
        rows.append(("icon", "{}x{}".format(*datapack.icon.size)))
    for error in datapack.errors:
        rows.append(("error", error))
    return rows


def describe_resource(resource: Resource, version: Version | None = None) -> Rows:
    rows: Rows = [
        ("id", resource.id),
        ("registry", resource.registry),
        ("namespace", resource.namespace),
        ("source", resource.overlay or "base pack"),
        ("file", str(resource.path)),
    ]
    if isinstance(resource, Function):
        rows.append(("commands", str(len(resource.content))))
        rows.append(("macro", "yes" if resource.is_macro else "no"))
        rows.append(("estimated cost", f"{resource.estimate_cost() / 1000:.3f} ms"))
        calls = {target for target, _ in resource.calls}
        rows.append(("calls", ", ".join(sorted(calls)) or "-"))
        if version is not None:
            from datapack_emulator.emulator.commands.registry import command_set

            missing = command_set(version).missing_features(resource.features())
            rows.append(
                (
                    f"unsupported in {version.id}",
                    ", ".join(
                        f"{feature}" + (f" (since {since.id})" if since else "")
                        for feature, since in missing
                    )
                    or "-",
                )
            )
    if resource.error:
        rows.append(("error", resource.error))
    return rows


def describe_run(run: VersionRun) -> Rows:
    rows: Rows = [
        ("version", run.version.id),
        ("pack_format", run.version.format_string),
        ("status", run.status),
        ("pack declares support", "yes" if run.supported else "no"),
        ("active overlays", ", ".join(run.overlays) or "-"),
        ("ticks", str(run.ticks)),
        ("commands", str(run.commands)),
        ("total", f"{run.total_us / 1000:.2f} ms"),
        ("worst tick", f"{run.worst_tick_us / 1000:.2f} ms"),
        ("warnings", str(run.warnings)),
        ("errors", str(run.errors)),
        ("unknown commands", ", ".join(sorted(run.unknown_commands)) or "-"),
        ("missing functions", ", ".join(run.missing_functions) or "-"),
        ("never called", ", ".join(run.unreachable) or "-"),
        ("recursion", "; ".join(" -> ".join(cycle) for cycle in run.cycles) or "-"),
    ]
    return rows


def describe_version(version: Version) -> Rows:
    return [
        ("version", version.id),
        ("pack_format", version.format_string),
        ("data version", str(version.data_version)),
        ("singular registries", "yes" if versions.uses_singular_registries(version) else "no"),
        ("overlays", "yes" if versions.supports_overlays(version) else "no"),
        ("macros", "yes" if versions.supports_macros(version) else "no"),
    ]
