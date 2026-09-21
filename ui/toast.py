"""A small message that fades in over the bottom of the window and goes away by itself."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMaximumWidth(560)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.last_text = ""
        self.hide()

    def show_message(self, text: str, ms: int = 4500) -> None:
        self.last_text = text
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(8, (parent.width() - self.width()) // 2), max(8, parent.height() - self.height() - 64))
        self.raise_()
        self.show()
        self._timer.start(ms)
