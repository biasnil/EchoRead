"""The document as paragraphs. The whole text is kept, but only one section (chapter) is 'open' at a time:
the open section's paragraphs are what the reader shows, the cache precaches and playback plays."""
from __future__ import annotations

import bisect
import hashlib
import re

from PyQt6.QtCore import QObject, pyqtSignal

from .models import Paragraph, is_picture
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
            if not p.skip and not p.ignored and not p.picture:  # a picture is shown, never spoken
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


# ---------------------------------------------------------------------------------------------------------- math out loud
_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota",
          "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi",
          "χ": "chi", "ψ": "psi", "ω": "omega", "Γ": "capital gamma", "Δ": "delta", "Θ": "capital theta", "Λ": "capital lambda",
          "Π": "capital pi", "Σ": "sigma", "Φ": "capital phi", "Ψ": "capital psi", "Ω": "omega"}
_SYMBOLS = {"×": " times ", "÷": " divided by ", "±": " plus or minus ", "≠": " is not equal to ", "≤": " is less than or equal to ",
            "≥": " is greater than or equal to ", "≈": " is approximately ", "∞": " infinity ", "∑": " the sum of ", "∫": " the integral of ",
            "∂": " partial ", "∈": " is in ", "→": " goes to ", "∝": " is proportional to ", "−": " minus ", "·": " times ",
            "½": " one half ", "¼": " one quarter ", "¾": " three quarters ", "⅓": " one third ", "⅔": " two thirds "}
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_PROTECT = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+\.\S+|`[^`]*`)")
_NUM = r"\d+(?:[.,]\d+)*"
_OPERAND_BEFORE = r"(?<=[\w)\]])"
_OPERAND_AFTER = r"(?=[\w(\[\-])"


def _power(n: str) -> str:
    return {"2": " squared ", "3": " cubed "}.get(n, f" to the power of {n} ")


def math_to_speech(text: str) -> str:
    """Write formulas the way they are said: "x² + y² = z²" -> "x squared plus y squared equals z squared".
    Careful on purpose: an operator is only turned into a word when there are numbers or single letters around it, so ordinary
    prose, dates, hyphenated words and web addresses are left alone."""
    if not text or is_picture(text) or not re.search(r"[+=<>*^/×÷±≠≤≥≈∞∑∫∂∈→∝−·½¼¾⅓⅔√π%°²³⁰¹⁴⁵⁶⁷⁸⁹₀-₉α-ωΓΔΘΛΠΣΦΨΩ]|\d\s-\s\d", text):
        return text
    out = []
    for i, part in enumerate(_PROTECT.split(text)):
        out.append(part if i % 2 else _math_part(part))
    joined = re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()
    return re.sub(r"\s+([.,;:!?)\]])", r"\1", joined) if joined != text else joined  # no gap left before punctuation


def _math_part(s: str) -> str:
    s = s.replace("H₂O", "H2O").replace("CO₂", "CO2")  # chemistry formulas are read as written
    s = re.sub(r"√\s*\(([^)]*)\)", r" the square root of \1 ", s)
    s = re.sub(r"√\s*(\w+)", r" the square root of \1 ", s)
    s = re.sub(r"(?<=\w)([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)", lambda m: _power(m.group(1).translate(_SUPERSCRIPT)), s)
    s = re.sub(r"(?<=\w)([₀₁₂₃₄₅₆₇₈₉]+)", lambda m: " sub " + m.group(1).translate(_SUBSCRIPT) + " ", s)
    s = re.sub(r"(?<=[\w)])\^\(?(-?\w+)\)?", lambda m: _power(m.group(1)), s)
    for k, v in _SYMBOLS.items():
        s = s.replace(k, v)
    greek = "".join(_GREEK)
    s = re.sub(rf"(?<![\u0370-\u03ff])([{greek}])(?![\u0370-\u03ff])", lambda m: f" {_GREEK[m.group(1)]} ", s)
    s = re.sub(rf"({_NUM})\s*%", r"\1 percent", s)
    s = re.sub(rf"({_NUM})\s*°\s*([CF])\b", lambda m: f"{m.group(1)} degrees {'Celsius' if m.group(2) == 'C' else 'Fahrenheit'}", s)
    s = re.sub(rf"({_NUM})\s*°", r"\1 degrees", s)
    # ASCII operators. Written with spaces around them (x + y, a = b, 6 / 3) they are turned into words; stuck between two
    # letters or digits (2+3=5, x+y) too. Anything else (C++, dates, ranges, addresses) is left alone.
    for op, word in (("+", "plus"), ("=", "equals"), ("*", "times")):
        s = re.sub(rf"(?<=[\w)\]$])\s+{re.escape(op)}\s+(?=[\w(\[$\-])", f" {word} ", s)
        if True:
            s = re.sub(rf"(?<=[A-Za-z0-9)\]])\s*{re.escape(op)}\s*(?=[A-Za-z0-9(\[])", f" {word} ", s) if op != "*" else re.sub(
                r"(?<=\d)\s*\*\s*(?=\d)", " times ", s)
    # a minus needs spaces around it, a number or a single letter after it, and something formula-like before it
    s = re.sub(r"(\d|[)\]]|\b[A-Za-z]|(?<=\d)[A-Za-z]|squared|cubed)\s+-\s+(?=\d|[A-Za-z]\b|\()", r"\1 minus ", s)
    s = re.sub(r"(\d|\b[A-Za-z]|[)\]])\s+/\s+(?=\d|[A-Za-z]\b|\()", r"\1 divided by ", s)  # 6 / 3, a / b: never words around a slash
    s = re.sub(r"(?<=\w)\s+<\s+(?=\w)", " is less than ", s)
    s = re.sub(r"(?<=\w)\s+>\s+(?=\w)", " is greater than ", s)
    return s
