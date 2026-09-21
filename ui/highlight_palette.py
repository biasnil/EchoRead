"""The small popup that appears when you finish dragging across text: five colours, then Copy.
Also builds the right-click menu of an existing highlight (Change color / Remove / Copy)."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QVBoxLayout, QWidget

from .theme import HIGHLIGHT_COLORS


def color_icon(color_name: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    from PyQt6.QtGui import QPainter

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(HIGHLIGHT_COLORS[color_name]["hex"]))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


class HighlightPalette(QWidget):
    """Colour picker popup. Emits `color_chosen(name)` or `copy_requested()`, then closes; `closed` fires whichever way it ended."""

    color_chosen = pyqtSignal(str)
    copy_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self._frame = QFrame()
        self._frame.setObjectName("palette")
        outer.addWidget(self._frame)
        lay = QVBoxLayout(self._frame)
        lay.setContentsMargins(12, 12, 12, 8)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.swatches: dict[str, QPushButton] = {}
        for name, spec in HIGHLIGHT_COLORS.items():
            b = QPushButton()
            b.setObjectName("swatch")
            b.setToolTip(spec["label"])
            b.setStyleSheet(f"QPushButton#swatch {{ background: {spec['hex']}; }}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, n=name: self._choose(n))
            self.swatches[name] = b
            row.addWidget(b)
        lay.addLayout(row)
        div = QFrame()
        div.setObjectName("divider")
        lay.addWidget(div)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("palrow")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy)
        lay.addWidget(self.copy_button)

    def _choose(self, name: str) -> None:
        self.color_chosen.emit(name)
        self.close()

    def _copy(self) -> None:
        self.copy_requested.emit()
        self.close()

    def show_above(self, global_pos: QPoint) -> None:
        """Open just above `global_pos`, kept inside the screen."""
        self.adjustSize()
        size = self.sizeHint()
        x, y = global_pos.x() - size.width() // 2, global_pos.y() - size.height() - 14
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 4, min(x, area.right() - size.width() - 4))
            if y < area.top() + 4:
                y = global_pos.y() + 22  # no room above: open below
        self.move(x, y)
        self.show()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.closed.emit()


def build_highlight_menu(parent, current_color: str, on_color, on_remove, on_copy) -> QMenu:
    """Right-click menu of an existing highlight."""
    menu = QMenu(parent)
    colors = menu.addMenu("Change color")
    for name, spec in HIGHLIGHT_COLORS.items():
        act = QAction(color_icon(name), spec["label"], colors)
        act.setCheckable(True)
        act.setChecked(name == current_color)
        act.triggered.connect(lambda checked=False, n=name: on_color(n))
        colors.addAction(act)
    menu.addAction("Remove highlight").triggered.connect(lambda checked=False: on_remove())
    menu.addAction("Copy").triggered.connect(lambda checked=False: on_copy())
    return menu
