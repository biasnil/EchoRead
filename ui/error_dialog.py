"""The friendly 'something went wrong' dialog: short message, expandable traceback, Copy traceback, Open log file."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout

from .widgets import make_button, make_label


class ErrorDialog(QDialog):
    def __init__(self, summary: str, details: str, log_path: Path | None = None, opener=None, parent=None):
        super().__init__(parent)
        self._details = details
        self._log_path = log_path
        self._opener = opener or self._open_default
        self.setWindowTitle("EchoRead")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label(summary, "h2", wrap=True))
        lay.addWidget(make_label("You can carry on, but if this keeps happening, copy the details below so it can be fixed.", "muted", wrap=True))
        self.toggle = make_button("▸  Show details", "linkbtn", self._toggle)
        lay.addWidget(self.toggle)
        self.details_box = QPlainTextEdit(details)
        self.details_box.setObjectName("details")
        self.details_box.setReadOnly(True)
        self.details_box.setMinimumHeight(200)
        self.details_box.setVisible(False)
        lay.addWidget(self.details_box)
        row = QHBoxLayout()
        self.copy_button = make_button("Copy traceback", callback=self._copy)
        self.log_button = make_button("Open log file", callback=self._open_log)
        self.log_button.setEnabled(log_path is not None)
        row.addWidget(self.copy_button)
        row.addWidget(self.log_button)
        row.addStretch(1)
        row.addWidget(make_button("Close", "primary", self.accept))
        lay.addLayout(row)

    def _toggle(self) -> None:
        show = not self.details_box.isVisible()
        self.details_box.setVisible(show)
        self.toggle.setText("▾  Hide details" if show else "▸  Show details")
        self.adjustSize()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._details)

    def _open_log(self) -> None:
        if self._log_path is not None:
            self._opener(self._log_path)

    @staticmethod
    def _open_default(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
