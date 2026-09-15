"""Rows for the inspector dock: what is known about the selected thing."""

from __future__ import annotations

from src.emulator import versions
from src.emulator.datapack import Datapack
from src.emulator.engine import VersionRun
from src.emulator.resources import Function, Resource
from src.emulator.versions import Version

Rows = list[tuple[str, str]]


def _format(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return ".".join(str(part) for part in value)
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
            ("overlays", ", ".join(layer.entry.describe() for layer in datapack.overlays if layer.entry))
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
            from src.emulator.commands.registry import command_set

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
