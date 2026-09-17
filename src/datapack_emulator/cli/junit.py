"""JUnit XML reports, which CI dashboards (GitHub, GitLab, Jenkins) read."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from datapack_emulator.emulator.engine import VersionRun

#: characters XML 1.0 cannot hold, even escaped
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _text(value: str) -> str:
    return _INVALID_XML.sub("\N{REPLACEMENT CHARACTER}", value)


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
                name=_text(f"tick {result.test.at_tick}: {result.test.command}"),
                classname=run.version.id,
                time="0",
            )
            if not result.passed:
                failures += 1
                reason = _text(result.reason)
                failure = ElementTree.SubElement(case, "failure", message=reason)
                failure.text = reason
            if result.records:
                output = ElementTree.SubElement(case, "system-out")
                output.text = _text("\n".join(record.format() for record in result.records))
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
