"""Write a calibration's measured constants into costs.py, as the new defaults.

    datapack-emulator-cli calibrate --version 26.2 --accept-eula --out calibration.json
    python tools/apply_calibration.py calibration.json

Only numbers change: each ``"key": value,`` inside the four dicts and each
``NAME: float = value`` scalar is rewritten in place, so comments and order
stay as they are. The header's provenance block (between the
``calibration:begin`` / ``calibration:end`` markers) is replaced with where
and when the numbers were measured. Review the diff before committing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COSTS = Path(__file__).resolve().parents[1] / "src" / "datapack_emulator" / "emulator" / "costs.py"
DICTS = ("COMMAND_COST_US", "SUBCOMMAND_COST_US", "CONDITION_COST_US", "SELECTOR_BASE_COST_US")


def _number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if "." in text else text + ".0"


def apply(document: dict, text: str) -> str:
    values = document["values"]
    for name in DICTS:
        start = text.index(f"{name}: dict[str, float] = {{")
        end = text.index("\n}", start)
        block = text[start:end]
        for key, value in values.get(name, {}).items():
            block = re.sub(
                rf'(\n\s*"{re.escape(key)}":\s*)[-\d._]+',
                lambda m, v=value: m.group(1) + _number(v),
                block,
                count=1,
            )
        text = text[:start] + block + text[end:]
    for name, value in values.items():
        if name in DICTS or isinstance(value, dict):
            continue
        text, count = re.subn(
            rf"^({name}: float = )[-\d._]+",
            lambda m, v=value: m.group(1) + _number(v),
            text,
            flags=re.M,
        )
        if not count:
            raise KeyError(f"costs.py has no scalar {name}")
    machine = document.get("machine", {})
    provenance = (
        "# calibration:begin\n"
        f"#: measured on Minecraft {document['minecraft']}, {document['measured_at']}\n"
        f"#: {machine.get('cpu', '?')} ({machine.get('cores', '?')} threads), "
        f"{machine.get('system', '?')}\n"
        f"#: {machine.get('java', '?')}\n"
        f"#: {len(document.get('measured', []))} constants fitted from "
        f"{len(document.get('benchmarks', []))} benchmark runs; the rest are the old guesses "
        f"x {document.get('scale_of_old_model')}\n"
        f"CALIBRATED_ON: str = {json.dumps(document['minecraft'])}\n"
        "# calibration:end"
    )
    text = re.sub(r"# calibration:begin.*?# calibration:end", provenance, text, flags=re.S)
    return text


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    document = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    COSTS.write_text(apply(document, COSTS.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"updated {COSTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
