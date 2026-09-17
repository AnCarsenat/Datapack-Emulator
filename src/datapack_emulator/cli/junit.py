"""JUnit XML reports, which CI dashboards (GitHub, GitLab, Jenkins) read."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from datapack_emulator.emulator.engine import VersionRun


def junit_tree(results: list[VersionRun], name: str = "") -> ElementTree.ElementTree:
    """One ``testsuite`` per version, one ``testcase`` per test."""
    root = ElementTree.Element("testsuites", name=name or "datapack tests")
    total = failures = 0
    for run in results:
        suite = ElementTree.SubElement(
            root,
            "testsuite",
            name=f"{name} @ {run.version.id}" if name else run.version.id,
            tests=str(len(run.tests)),
            failures=str(sum(1 for result in run.tests if not result.passed)),
            errors="0",
            skipped="0",
        )
        properties = ElementTree.SubElement(suite, "properties")
        for key, value in (
            ("version", run.version.id),
            ("pack_format", run.version.format_string),
            ("status", run.status),
            ("overlays", ", ".join(run.overlays)),
        ):
            ElementTree.SubElement(properties, "property", name=key, value=value)
        for result in run.tests:
            total += 1
            case = ElementTree.SubElement(
                suite,
                "testcase",
                name=f"tick {result.test.at_tick}: {result.test.command}",
                classname=run.version.id,
                time="0",
            )
            if not result.passed:
                failures += 1
                failure = ElementTree.SubElement(case, "failure", message=result.reason)
                failure.text = result.reason
            if result.records:
                output = ElementTree.SubElement(case, "system-out")
                output.text = "\n".join(record.format() for record in result.records)
    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("errors", "0")
    ElementTree.indent(root)
    return ElementTree.ElementTree(root)


def write_junit(results: list[VersionRun], path: Path | str, name: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    junit_tree(results, name).write(path, encoding="utf-8", xml_declaration=True)
    return path
