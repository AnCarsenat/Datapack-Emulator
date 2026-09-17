#!/usr/bin/env sh
# Start the emulator from a checkout, installed or not.
#
#   src/main.sh                          the window
#   src/main.sh --cli run samples/hat    the headless runner (see docs/cli.md)
#
# Uses .venv/ or src/.venv/ when one exists, otherwise python3.
set -eu

src="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$src")"

python=python3
for venv in "$root/.venv" "$src/.venv"; do
    if [ -x "$venv/bin/python" ]; then
        python="$venv/bin/python"
        break
    fi
done

# the package is importable even without `pip install -e .`
export PYTHONPATH="$src${PYTHONPATH:+:$PYTHONPATH}"

if [ "${1:-}" = "--cli" ]; then
    shift
    exec "$python" -m datapack_emulator.cli "$@"
fi
exec "$python" -m datapack_emulator "$@"
