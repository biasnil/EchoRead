"""Library: everything you've read, with where you stopped."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from core.library import Library
from .widgets import make_button, make_label


class LibraryView(QWidget):
    open_requested = pyqtSignal(str)  # doc id

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 36, 40, 24)
        lay.setSpacing(14)
        lay.addWidget(make_label("Library", "h1"))
        self._empty = make_label("Nothing here yet. Things you read are saved on this computer.", "muted")
        lay.addWidget(self._empty)
        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Delete", callback=self._delete))
        row.addWidget(make_button("Open", "primary", self._open))
        lay.addLayout(row)
        self._list.itemDoubleClicked.connect(lambda _item: self._open())
        library.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        items = self._library.entries()
        self._empty.setVisible(not items)
        for e in items:
            when = time.strftime("%b %d, %Y", time.localtime(e.get("last_opened", 0)))
            bits = [e["kind"], when, f"paragraph {e.get('paragraph', 0) + 1}"]
            if e.get("bookmarks"):
                bits.append(f"{len(e['bookmarks'])} bookmark{'s' if len(e['bookmarks']) != 1 else ''}")
            item = QListWidgetItem(f"{e['title']}\n{' · '.join(bits)}")
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            self._list.addItem(item)

    def _selected(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _open(self) -> None:
        doc_id = self._selected()
        if doc_id:
            self.open_requested.emit(doc_id)

    def _delete(self) -> None:
        doc_id = self._selected()
        if doc_id:
            self._library.remove(doc_id)
