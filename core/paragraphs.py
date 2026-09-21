"""The document as paragraphs. The whole text is kept, but only one section (chapter) is 'open' at a time:
the open section's paragraphs are what the reader shows, the cache precaches and playback plays."""
from __future__ import annotations

import bisect
import hashlib
import re

from PyQt6.QtCore import QObject, pyqtSignal

from .models import Paragraph
from .sections import Section, SectionSplitter


class ParagraphModel(QObject):
    current_changed = pyqtSignal(int, int)  # new index, old index (inside the open section)
    reset = pyqtSignal()  # the open section's paragraphs were replaced
    document_changed = pyqtSignal()  # a whole new document (new section list)
    section_changed = pyqtSignal(int)  # a different section was opened
    paragraph_changed = pyqtSignal(int)  # one paragraph's flags changed
    flags_changed = pyqtSignal()  # bookmarks / highlights changed by the user (for saving)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: list[str] = []
        self._sections: list[Section] = [Section("", 0, 0)]
        self._starts: list[int] = [0]
        self._section = 0
        self._offset = 0
        self._items: list[Paragraph] = []
        self._current = -1
        self._flags: dict[str, set[int]] = {"bookmarked": set(), "highlighted": set(), "skip": set(), "ignored": set()}

    # ------------------------------------------------------------------ text -> paragraphs
    @staticmethod
    def clean(text: str) -> str:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
        text = re.sub(r"\s*\n\s*", " ", text)
        return re.sub(r"[ \t\u00a0]+", " ", text).strip()

    @classmethod
    def split(cls, text: str) -> list[str]:
        """Blank-line separated paragraphs; if there are none, every line is a paragraph."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        parts = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
        return [p for p in (cls.clean(x) for x in parts) if p]

    @staticmethod
    def hash_of(strings: list[str]) -> str:
        return hashlib.sha1("\x1e".join(strings).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ loading
    def set_text(self, text: str) -> None:
        self.set_document(self.split(text))

    def set_paragraphs(self, strings: list[str]) -> None:
        self.set_document(strings)

    def clear(self) -> None:
        self.set_document([])

    def set_document(self, strings: list[str], sections: list[Section] | None = None, bookmarks=(), highlights=(),
                     position: int = 0) -> None:
        """Load a whole document; open the section containing paragraph `position` with it selected."""
        self._all = list(strings)
        n = len(self._all)
        self._sections = list(sections) if sections is not None else SectionSplitter.split(self._all)
        if not self._sections:
            self._sections = [Section("", 0, 0)]
        self._starts = [s.start for s in self._sections]
        self._flags = {"bookmarked": {b for b in bookmarks if 0 <= b < n}, "highlighted": {h for h in highlights if 0 <= h < n},
                       "skip": set(), "ignored": set()}
        self.document_changed.emit()
        section, local = self.locate(position)
        self._open(section, local)

    def _open(self, section: int, local: int = 0) -> None:
        sec = self._sections[section]
        self._section, self._offset = section, sec.start
        f = self._flags
        self._items = [
            Paragraph(i, self._all[sec.start + i], skip=(sec.start + i) in f["skip"], ignored=(sec.start + i) in f["ignored"],
                      bookmarked=(sec.start + i) in f["bookmarked"], highlighted=(sec.start + i) in f["highlighted"])
            for i in range(sec.count)
        ]
        self._current = max(0, min(local, len(self._items) - 1)) if self._items else -1
        self.reset.emit()
        self.section_changed.emit(section)

    # ------------------------------------------------------------------ sections
    @property
    def sections(self) -> list[Section]:
        return self._sections

    @property
    def section_count(self) -> int:
        return len(self._sections)

    @property
    def section_index(self) -> int:
        return self._section

    @property
    def offset(self) -> int:
        return self._offset

    def open_section(self, index: int, local: int = 0) -> bool:
        if not 0 <= index < len(self._sections):
            return False
        self._open(index, local)
        return True

    def locate(self, global_index: int) -> tuple[int, int]:
        """(section, index inside it) for a paragraph number in the whole document."""
        if not self._all:
            return 0, 0
        g = max(0, min(global_index, len(self._all) - 1))
        section = max(0, bisect.bisect_right(self._starts, g) - 1)
        return section, g - self._sections[section].start

    def open_global(self, global_index: int) -> None:
        section, local = self.locate(global_index)
        if section != self._section:
            self._open(section, local)
        else:
            self.set_current(local)

    def document_texts(self) -> list[str]:
        return self._all

    def document_length(self) -> int:
        return len(self._all)

    @property
    def current_global(self) -> int:
        return self._offset + self._current if self._current >= 0 else 0

    # ------------------------------------------------------------------ the open section (local indices)
    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> Paragraph:
        return self._items[index]

    def __iter__(self):
        return iter(self._items)

    def all_texts(self) -> list[str]:
        """Text of the open section (what gets precached and read)."""
        return [p.text for p in self._items]

    def active_texts(self) -> list[str]:
        return [p.text for p in self._items if not p.ignored]

    def text_hash(self) -> str:
        return self.hash_of(self._all)

    @property
    def current(self) -> int:
        return self._current

    def set_current(self, index: int) -> None:
        if not self._items:
            return
        index = max(0, min(index, len(self._items) - 1))
        old, self._current = self._current, index
        if old != index:
            self.current_changed.emit(index, old)

    def next_playable(self, index: int, step: int = 1) -> int:
        """First paragraph at or after `index` (moving by `step`) that isn't skipped or ignored, else -1."""
        while 0 <= index < len(self._items):
            p = self._items[index]
            if not p.skip and not p.ignored:
                return index
            index += step
        return -1

    def next_visible(self, index: int, step: int = 1) -> int:
        while 0 <= index < len(self._items):
            if not self._items[index].ignored:
                return index
            index += step
        return -1

    # ------------------------------------------------------------------ flags (stored for the whole document)
    def _toggle(self, local: int, attr: str) -> bool:
        p = self._items[local]
        store = self._flags[attr]
        g = self._offset + local
        on = g not in store
        (store.add if on else store.discard)(g)
        setattr(p, attr if attr != "skip" else "skip", on)
        self.paragraph_changed.emit(local)
        if attr in ("bookmarked", "highlighted"):
            self.flags_changed.emit()
        return on

    def toggle_bookmark(self, local: int) -> bool:
        return self._toggle(local, "bookmarked")

    def toggle_highlight(self, local: int) -> bool:
        return self._toggle(local, "highlighted")

    def toggle_skip(self, local: int) -> bool:
        return self._toggle(local, "skip")

    def ignore(self, local: int) -> None:
        self._flags["ignored"].add(self._offset + local)
        self._items[local].ignored = True
        self.paragraph_changed.emit(local)

    def unignore_all(self) -> None:
        self._flags["ignored"].clear()
        for p in self._items:
            if p.ignored:
                p.ignored = False
                self.paragraph_changed.emit(p.index)

    def is_ignored(self, g: int) -> bool:
        return g in self._flags["ignored"]

    def is_skipped(self, g: int) -> bool:
        return g in self._flags["skip"]

    def bookmarks(self) -> list[int]:
        """Bookmarked paragraphs, numbered across the whole document."""
        return sorted(self._flags["bookmarked"])

    def highlights(self) -> list[int]:
        return sorted(self._flags["highlighted"])

    def describe(self, g: int) -> tuple[str, int, str]:
        """(section title, paragraph number within it, text) for a paragraph of the whole document."""
        section, local = self.locate(g)
        return self._sections[section].title, local + 1, self._all[g]

    def load_flags(self, bookmarks: list[int], highlights: list[int]) -> None:
        n = len(self._all)
        self._flags["bookmarked"] = {b for b in bookmarks if 0 <= b < n}
        self._flags["highlighted"] = {h for h in highlights if 0 <= h < n}
        for p in self._items:
            g = self._offset + p.index
            p.bookmarked, p.highlighted = g in self._flags["bookmarked"], g in self._flags["highlighted"]
            self.paragraph_changed.emit(p.index)
