"""Coloured text highlights: character ranges inside paragraphs, saved per document.

Stored in `highlights/<doc id>.json`. Paragraph numbers are GLOBAL (whole document), like bookmarks. A highlight file belongs to one
version of the text: when the text changes (different hash) the old highlights are dropped, exactly as bookmarks are.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .paths import AppPaths


@dataclass
class Highlight:
    id: int
    para: int  # global paragraph number
    start: int  # first character
    end: int  # one past the last character
    color: str  # colour name, e.g. "yellow" (the theme maps names to actual colours)


class HighlightStore(QObject):
    changed = pyqtSignal(int)  # global paragraph number whose highlights changed (-1 = everything)

    SAVE_DELAY_MS = 600
    DEFAULT_COLOR = "yellow"

    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._doc_id: str | None = None
        self._text_hash = ""
        self._items: dict[int, Highlight] = {}
        self._by_para: dict[int, list[int]] = {}
        self._next_id = 1
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

    # ------------------------------------------------------------------ document
    def _file(self, doc_id: str):
        return self._paths.highlights_dir / f"{doc_id}.json"

    def open(self, doc_id: str, text_hash: str) -> None:
        """Switch to a document. Highlights saved for a different version of its text are ignored."""
        self.flush()
        self._doc_id, self._text_hash = doc_id, text_hash
        self._items, self._by_para, self._next_id = {}, {}, 1
        try:
            data = json.loads(self._file(doc_id).read_text("utf-8"))
            if data.get("text_hash") == text_hash:
                for raw in data.get("highlights", []):
                    h = Highlight(int(raw["id"]), int(raw["p"]), int(raw["s"]), int(raw["e"]), str(raw["c"]))
                    if h.end > h.start >= 0:
                        self._items[h.id] = h
                        self._by_para.setdefault(h.para, []).append(h.id)
                        self._next_id = max(self._next_id, h.id + 1)
        except Exception:
            pass
        self.changed.emit(-1)

    def close(self) -> None:
        self.flush()
        self._doc_id = None
        self._items, self._by_para = {}, {}
        self.changed.emit(-1)

    @property
    def doc_id(self) -> str | None:
        return self._doc_id

    # ------------------------------------------------------------------ queries
    def for_paragraph(self, para: int) -> list[Highlight]:
        return sorted((self._items[i] for i in self._by_para.get(para, [])), key=lambda h: h.start)

    def at(self, para: int, char: int) -> Highlight | None:
        for h in self.for_paragraph(para):
            if h.start <= char < h.end:
                return h
        return None

    def get(self, hid: int) -> Highlight | None:
        return self._items.get(hid)

    def count(self) -> int:
        return len(self._items)

    def paragraphs(self) -> list[int]:
        return sorted(p for p, ids in self._by_para.items() if ids)

    # ------------------------------------------------------------------ editing
    def _link(self, h: Highlight) -> None:
        self._items[h.id] = h
        self._by_para.setdefault(h.para, []).append(h.id)

    def _unlink(self, hid: int) -> None:
        h = self._items.pop(hid, None)
        if h is not None:
            ids = self._by_para.get(h.para, [])
            if hid in ids:
                ids.remove(hid)

    def add(self, para: int, start: int, end: int, color: str | None = None, text_length: int | None = None) -> Highlight | None:
        """Highlight characters [start, end) of a paragraph. Whatever it overlaps is trimmed or replaced."""
        start, end = min(start, end), max(start, end)
        if text_length is not None:
            end = min(end, text_length)
        if start < 0 or end <= start or self._doc_id is None:
            return None
        for old in list(self.for_paragraph(para)):
            if old.end <= start or old.start >= end:
                continue
            if old.start < start and old.end > end:  # the new one sits inside the old one: split it in two
                tail = Highlight(self._next_id, para, end, old.end, old.color)
                self._next_id += 1
                old.end = start
                self._link(tail)
            elif old.start < start:  # overlaps the old one's end
                old.end = start
            elif old.end > end:  # overlaps the old one's start
                old.start = end
            else:  # completely covered
                self._unlink(old.id)
        h = Highlight(self._next_id, para, start, end, color or self.DEFAULT_COLOR)
        self._next_id += 1
        self._link(h)
        self._touched(para)
        return h

    def set_color(self, hid: int, color: str) -> None:
        h = self._items.get(hid)
        if h is not None and h.color != color:
            h.color = color
            self._touched(h.para)

    def remove(self, hid: int) -> None:
        h = self._items.get(hid)
        if h is not None:
            self._unlink(hid)
            self._touched(h.para)

    def clear_paragraph(self, para: int) -> None:
        for hid in list(self._by_para.get(para, [])):
            self._unlink(hid)
        self._touched(para)

    def import_paragraph_flags(self, paragraphs: list[int], lengths, color: str | None = None) -> int:
        """Old whole-paragraph highlights (library.json) become full-paragraph ranges. `lengths(para)` gives a text length.
        Only used once, on a document that has no highlight file yet."""
        made = 0
        for para in paragraphs:
            n = lengths(para)
            if n and not self.for_paragraph(para) and self.add(para, 0, n, color) is not None:
                made += 1
        return made

    # ------------------------------------------------------------------ saving
    def _touched(self, para: int) -> None:
        self._dirty = True
        self._timer.start()
        self.changed.emit(para)

    def has_saved_file(self, doc_id: str) -> bool:
        return self._file(doc_id).exists()

    def flush(self) -> None:
        """Write pending changes now."""
        self._timer.stop()
        if not self._dirty or self._doc_id is None:
            return
        self._dirty = False
        path = self._file(self._doc_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"doc_id": self._doc_id, "text_hash": self._text_hash,
                       "highlights": [{"id": h.id, "p": h.para, "s": h.start, "e": h.end, "c": h.color}
                                      for h in sorted(self._items.values(), key=lambda x: (x.para, x.start))]}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1), "utf-8")
            os.replace(tmp, path)
        except OSError:
            self._dirty = True  # try again on the next change / exit
            raise
