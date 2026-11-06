# Notes for AI agents

Two different jobs; read the part that is yours.

## Using the emulator on a datapack

Read [docs/agents.md](docs/agents.md). In short:

```python
from datapack_emulator import agent
agent.check(pack, version="1.21.4")   # static: cheap, run first
agent.run(pack, version="1.21.4", ticks=40, commands=["scoreboard players get #g t"])
agent.test(pack, ["5:"], checks=["score #g t = 5"], version="1.21.4")
```

or `datapack-emulator-cli --quiet run|test|matrix|check ... --json`. Always
pass the version the pack targets. Read `summary` first, `ok` next. A `game`
record is what Minecraft would say; an `emulator` record is a limit of the
emulation (see [docs/emulation.md](docs/emulation.md)), not a bug in the pack.
Timings are estimates from a cost model.

## Changing this repository

* Setup: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`
  (add `gui` for the window).
* Before a commit, what CI runs:
  `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/python -m pytest -q`
* The core (`src/datapack_emulator/emulator`, `cli`, `agent.py`) is standard
  library only and imports no Qt. Keep it that way.
* `agent.py` results are a public contract read by other tools
  (minecode-mcp): add keys freely, do not rename or remove them without a
  version bump that says so.
* Layout and conventions: [docs/architecture.md](docs/architecture.md) and
  [CONTRIBUTING.md](CONTRIBUTING.md).
