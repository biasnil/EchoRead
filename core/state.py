"""Central runtime state: the open document, playback status, current speed."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .models import DocInfo, nearest_speed


class AppState(QObject):
    doc_changed = pyqtSignal(object)  # DocInfo | None
    playback_changed = pyqtSignal(str)  # "stopped" | "loading" | "playing" | "paused"
    speed_changed = pyqtSignal(float)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc: DocInfo | None = None
        self._playback = "stopped"
        self._speed = 1.0
        self._busy = False

    @property
    def doc(self) -> DocInfo | None:
        return self._doc

    @doc.setter
    def doc(self, value: DocInfo | None) -> None:
        self._doc = value
        self.doc_changed.emit(value)

    @property
    def playback(self) -> str:
        return self._playback

    @playback.setter
    def playback(self, value: str) -> None:
        if value != self._playback:
            self._playback = value
            self.playback_changed.emit(value)

    @property
    def is_active(self) -> bool:
        return self._playback != "stopped"

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        value = nearest_speed(value)
        if value != self._speed:
            self._speed = value
            self.speed_changed.emit(value)

    @property
    def busy(self) -> bool:
        return self._busy

    @busy.setter
    def busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)
