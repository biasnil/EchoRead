"""Splitting a long document into chapters so only one chapter is ever loaded in the reader."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Section:
    title: str
    start: int  # first paragraph (index into the whole document)
    end: int  # one past the last paragraph

    @property
    def count(self) -> int:
        return self.end - self.start


class SectionSplitter:
    MIN_TO_SPLIT = 300  # shorter documents stay a single section
    MAX_SECTION = 250  # no section is ever longer than this (long chapters are cut into parts)

    BIBLE_BOOKS = [
        "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges", "Ruth",
        "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah",
        "Esther", "Job", "Psalms", "Psalm", "Proverbs", "Ecclesiastes", "Song of Solomon", "Song of Songs",
        "Isaiah", "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah",
        "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke",
        "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
        "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
        "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude", "Revelation",
    ]
    _BOOKS = {b.lower(): b for b in BIBLE_BOOKS}
    _NUMWORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                 "sixteen|seventeen|eighteen|nineteen|twenty")
    # "Chapter 34: Title", "Part II", "Psalm 23" ...  (a number-ish word must follow, so "Part of him left." is not a heading)
    _NUMBERED = re.compile(
        r"^(?P<kw>chapter|part|book|section|psalm|canto|act|volume)\s+(?:\d{1,4}|[ivxlcdm]{1,7}|" + _NUMWORDS +
        r")\b[\s.:\-–—]*.{0,70}$", re.I)
    _UNNUMBERED = re.compile(r"^(prologue|epilogue|prelude|interlude|preface|foreword|introduction|appendix)\b[^.!?]{0,50}$", re.I)
    _CJK = re.compile(r"^(第\s*[0-9〇零一二三四五六七八九十百千两]+\s*[章节回卷部篇]|제\s*\d+\s*[장화권부])")
    _LEVEL1 = {"part", "book", "volume", "canto"}
    BIBLE_MIN_BOOKS = 5  # only trust bare book names ("Ruth", "John") if at least this many different ones appear

    # ------------------------------------------------------------------ public
    @classmethod
    def split(cls, paragraphs: list[str]) -> list[Section]:
        n = len(paragraphs)
        if n < cls.MIN_TO_SPLIT:
            return [Section("", 0, n)]
        marks = cls._find_marks(paragraphs)
        sections = cls._from_marks(marks, n)
        if len(sections) < 2:
            return cls._fixed_parts(n)
        return cls._enforce_max(sections)

    # ------------------------------------------------------------------ heading detection
    @classmethod
    def _classify(cls, text: str):
        """-> (kind, title, book, is_bible) or None. kind is "book" or "chapter"."""
        t = re.sub(r"\s+", " ", text.strip())
        if not t or len(t) > 90:
            return None
        low = t.lower().rstrip(":.")
        if low in cls._BOOKS:
            return ("book", cls._BOOKS[low], cls._BOOKS[low], True)
        m = re.match(r"^(.+?)\s+(\d{1,3})$", low)
        if m and m.group(1) in cls._BOOKS:
            book = cls._BOOKS[m.group(1)]
            return ("chapter", f"{book} {m.group(2)}", book, True)
        m = cls._NUMBERED.match(t)
        if m:
            kind = "book" if m.group("kw").lower() in cls._LEVEL1 else "chapter"
            return (kind, t, None, False)
        if cls._UNNUMBERED.match(t) or cls._CJK.match(t):
            return ("chapter", t, None, False)
        return None

    @classmethod
    def _find_marks(cls, paragraphs: list[str]) -> list[tuple]:
        marks = []
        for i, p in enumerate(paragraphs):
            if len(p) <= 90:
                c = cls._classify(p)
                if c:
                    marks.append((i,) + c)
        books = {m[3] for m in marks if m[4]}
        if len(books) < cls.BIBLE_MIN_BOOKS:  # probably not a Bible: a lone "Mark" or "Ruth" is just a name
            marks = [m for m in marks if not m[4]]
        return marks

    @classmethod
    def _from_marks(cls, marks: list[tuple], n: int) -> list[Section]:
        starts: list[tuple[int, str]] = []
        book = None
        pending = None
        for k, (idx, kind, title, bk, _bible) in enumerate(marks):
            if kind == "book":
                book = title
                nxt = marks[k + 1] if k + 1 < len(marks) else None
                if nxt and nxt[1] == "chapter" and nxt[0] == idx + 1:
                    pending = idx  # "Genesis" directly followed by "Chapter 1": one section, starting at the book title
                    continue
                starts.append((idx, title))
            else:
                if bk:
                    book = bk
                label = f"{book} — {title}" if (book and not bk and book != title) else title
                starts.append((pending if pending is not None else idx, label))
                pending = None
        if not starts:
            return []
        if starts[0][0] > 0:
            starts.insert(0, (0, "Beginning"))
        sections = []
        for k, (s, title) in enumerate(starts):
            e = starts[k + 1][0] if k + 1 < len(starts) else n
            if e > s:
                sections.append(Section(title, s, e))
        return sections

    @classmethod
    def _enforce_max(cls, sections: list[Section]) -> list[Section]:
        out = []
        for sec in sections:
            if sec.count <= cls.MAX_SECTION:
                out.append(sec)
                continue
            parts = -(-sec.count // cls.MAX_SECTION)
            size = -(-sec.count // parts)
            for j in range(parts):
                s = sec.start + j * size
                out.append(Section(f"{sec.title} (part {j + 1}/{parts})", s, min(sec.end, s + size)))
        return out

    @classmethod
    def _fixed_parts(cls, n: int) -> list[Section]:
        parts = -(-n // cls.MAX_SECTION)
        size = -(-n // parts)
        return [Section(f"Part {j + 1} · paragraphs {j * size + 1}–{min(n, (j + 1) * size)}", j * size, min(n, (j + 1) * size))
                for j in range(parts)]
