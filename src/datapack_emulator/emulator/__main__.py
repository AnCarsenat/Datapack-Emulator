"""``python -m datapack_emulator.emulator`` — the same as ``datapack-emulator-cli``.

The command line itself lives in :mod:`datapack_emulator.cli`.
"""

from datapack_emulator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
