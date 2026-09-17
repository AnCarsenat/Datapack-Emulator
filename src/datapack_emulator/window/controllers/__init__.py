"""One controller per concern of the main window.

The window owns the shared state (datapack, emulator, output bus, project,
widgets looked up from window.ui); each controller holds the behaviour for one
area and reaches the others through the window.
"""

from datapack_emulator.window.controllers.console import ConsoleController
from datapack_emulator.window.controllers.datapacks import DatapackController
from datapack_emulator.window.controllers.debug import DebugController
from datapack_emulator.window.controllers.environment import EnvironmentController
from datapack_emulator.window.controllers.jars import JarController
from datapack_emulator.window.controllers.logs import LogController
from datapack_emulator.window.controllers.navigation import NavigationController
from datapack_emulator.window.controllers.notes import NotesController
from datapack_emulator.window.controllers.problems import ProblemsController
from datapack_emulator.window.controllers.projects import ProjectController
from datapack_emulator.window.controllers.runs import RunController
from datapack_emulator.window.controllers.session import SessionController
from datapack_emulator.window.controllers.world import WorldController

__all__ = [
    "ConsoleController",
    "DatapackController",
    "DebugController",
    "EnvironmentController",
    "JarController",
    "LogController",
    "NavigationController",
    "NotesController",
    "ProblemsController",
    "ProjectController",
    "RunController",
    "SessionController",
    "WorldController",
]
