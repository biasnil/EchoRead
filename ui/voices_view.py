"""Voices: Kokoro presets and imported voice packs."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from core.settings import SettingsManager
from core.tts import VoiceCatalog
from .widgets import make_button, make_label


class VoicesView(QWidget):
    preview_requested = pyqtSignal(str)
    import_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)
    use_requested = pyqtSignal(str)

    def __init__(self, catalog: VoiceCatalog, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._settings = settings
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 36, 40, 24)
        lay.setSpacing(14)
        lay.addWidget(make_label("Voices", "h1"))
        lay.addWidget(make_label("Pick the voice EchoRead reads with. Changing it re-caches the document in the new voice.", "muted", wrap=True))
        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Import voice pack…", callback=self.import_requested.emit))
        row.addWidget(make_button("Delete pack", callback=self._delete))
        row.addStretch(1)
        row.addWidget(make_button("▶  Preview", callback=lambda: self._selected() and self.preview_requested.emit(self._selected())))
        row.addWidget(make_button("Use this voice", "primary", lambda: self._selected() and self.use_requested.emit(self._selected())))
        lay.addLayout(row)
        self._list.itemDoubleClicked.connect(lambda _i: self._selected() and self.use_requested.emit(self._selected()))
        settings.changed.connect(lambda key: key == "voice" and self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        current = self._settings.voice
        last_group = None
        for v in self._catalog.list():
            if v["group"] != last_group:
                head = QListWidgetItem(v["group"].upper())
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                self._list.addItem(head)
                last_group = v["group"]
            item = QListWidgetItem(("✓  " if v["id"] == current else "     ") + v["label"])
            item.setData(Qt.ItemDataRole.UserRole, v["id"])
            self._list.addItem(item)
            if v["id"] == current:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)

    def _selected(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _delete(self) -> None:
        vid = self._selected()
        if vid and self._catalog.is_pack(vid):
            self.delete_requested.emit(vid)
