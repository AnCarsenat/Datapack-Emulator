from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

from src.settings import WINDOW

from src.window.panels import ExplorerPanel,GraphView,InspectorPanel,ConsolePanel

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(
            parent=None,
        )
        # Inherit the parent class's attributes

        self.setWindowTitle("Datapack Emulator")
        self.resize(WINDOW.WINDOW_WIDTH, WINDOW.WINDOW_HEIGHT)
        self.setCentralWidget(QLabel("Hello World", alignment=Qt.Alignment.AlignCenter))

        self.explorer = ExplorerPanel()
        self.graph_view = GraphView()
        self.inspector = InspectorPanel()
        self.console = ConsolePanel()

        self.setCentralWidget(self.graph_view)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.explorer)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.console)
