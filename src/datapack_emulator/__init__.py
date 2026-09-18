"""Datapack Emulator: run, profile and version-test Minecraft datapacks.

``datapack_emulator.emulator`` is the Qt-free core (models, commands, runtime,
version engine); ``datapack_emulator.window`` is the PySide6 application.
"""


def _version() -> str:
    """The installed distribution's version (pyproject.toml is the only place
    it is written); the fallback is for a checkout that was never installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("datapack-emulator")
    except PackageNotFoundError:
        return "0.0.0+checkout"


__version__ = _version()
