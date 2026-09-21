"""Recent documents, reading positions, bookmarks and highlights (library.json)."""
from __future__ import annotations

import hashlib
import json
import os
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .models import DocInfo
from .paths import AppPaths


class Library(QObject):
    position_updated = pyqtSignal(str, int, float)  # doc id, paragraph, speed
    changed = pyqtSignal()  # the list of documents changed

    SAVE_DELAY_MS = 800
    MAX_ENTRIES = 300

    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._entries: dict[str, dict] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.SAVE_DELAY_MS)
        self._timer.timeout.connect(self.save)
        self.load()

    @staticmethod
    def make_id(kind: str, key: str) -> str:
        return hashlib.sha1(f"{kind}\x00{key}".encode("utf-8")).hexdigest()[:16]

    # -- persistence
    def load(self) -> None:
        try:
            data = json.loads(self._paths.library_file.read_text("utf-8"))
            self._entries = {e["id"]: e for e in data.get("documents", [])}
        except Exception:
            self._entries = {}

    def save(self) -> None:
        self._paths.root.mkdir(parents=True, exist_ok=True)
        tmp = self._paths.library_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"documents": list(self._entries.values())}, indent=1), "utf-8")
        os.replace(tmp, self._paths.library_file)

    def flush(self) -> None:
        """Write pending changes now (called on exit)."""
        self._timer.stop()
        self.save()

    def _schedule_save(self) -> None:
        self._timer.start()

    # -- queries
    def entries(self, kind: str | None = None) -> list[dict]:
        """Newest first. kind: None = all, "file" = files, "task" = pasted text and links."""
        items = sorted(self._entries.values(), key=lambda e: e.get("last_opened", 0), reverse=True)
        if kind == "file":
            return [e for e in items if e["kind"] == "file"]
        if kind == "task":
            return [e for e in items if e["kind"] != "file"]
        return items

    def get(self, doc_id: str) -> dict | None:
        return self._entries.get(doc_id)

    def read_text(self, doc_id: str) -> str | None:
        entry = self._entries.get(doc_id)
        if not entry:
            return None
        try:
            return (self._paths.docs_dir / entry["file"]).read_text("utf-8")
        except Exception:
            return None

    # -- updates
    def register(self, doc: DocInfo, text: str, default_speed: float = 1.0) -> dict:
        """Add or refresh a document. Saved position/bookmarks survive only if the text is unchanged."""
        entry = self._entries.get(doc.doc_id)
        if entry is None or entry.get("text_hash") != doc.text_hash:
            entry = {"id": doc.doc_id, "created": time.time(), "paragraph": 0, "speed": default_speed,
                     "bookmarks": [], "highlights": []}
        entry.update(
            title=doc.title, kind=doc.kind, source=doc.source, page_count=doc.page_count,
            text_hash=doc.text_hash, last_opened=time.time(), file=f"{doc.doc_id}.txt",
        )
        self._paths.docs_dir.mkdir(parents=True, exist_ok=True)
        (self._paths.docs_dir / entry["file"]).write_text(text, "utf-8")
        self._entries[doc.doc_id] = entry
        self._prune()
        self._schedule_save()
        self.changed.emit()
        return entry

    def touch(self, doc_id: str) -> None:
        if doc_id in self._entries:
            self._entries[doc_id]["last_opened"] = time.time()
            self._schedule_save()
            self.changed.emit()

    def update_position(self, doc_id: str, paragraph: int, speed: float) -> None:
        entry = self._entries.get(doc_id)
        if entry is None:
            return
        entry["paragraph"], entry["speed"] = int(paragraph), float(speed)
        self._schedule_save()
        self.position_updated.emit(doc_id, int(paragraph), float(speed))

    def set_flags(self, doc_id: str, bookmarks: list[int], highlights: list[int]) -> None:
        entry = self._entries.get(doc_id)
        if entry is None:
            return
        entry["bookmarks"], entry["highlights"] = list(bookmarks), list(highlights)
        self._schedule_save()

    def remove(self, doc_id: str) -> None:
        entry = self._entries.pop(doc_id, None)
        if entry:
            try:
                (self._paths.docs_dir / entry["file"]).unlink()
            except OSError:
                pass
            self._schedule_save()
            self.changed.emit()

    def _prune(self) -> None:
        if len(self._entries) <= self.MAX_ENTRIES:
            return
        oldest = sorted(self._entries.values(), key=lambda e: e.get("last_opened", 0))
        for entry in oldest[: len(self._entries) - self.MAX_ENTRIES]:
            self._entries.pop(entry["id"], None)
            try:
                (self._paths.docs_dir / entry["file"]).unlink()
            except OSError:
                pass
