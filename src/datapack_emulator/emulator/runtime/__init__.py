"""Runtime layer: world state, execution context, output bus, emulator.

Intentionally empty of re-exports: the command modules import ``runtime.context``
while ``commands`` is still initialising, so importing the emulator here would
make that a cycle.  Use ``from datapack_emulator.emulator import Emulator`` instead.
"""
