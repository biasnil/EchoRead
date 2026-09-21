"""Layout-aware reading of a PDF's text layer.

MuPDF hands back text in the order it sits in the file, and blocks sorted top-to-bottom, which mixes up columns, glues verse
numbers to words, splits sentences and lets page headers, footnotes and printer's marks into the text. But a PDF also knows the
size, font and position of every piece of text, and that is enough to do what a person does: find the columns, tell headings
from body text, the running head and page number from the page, footnotes from the text they explain, and (in Bibles) verse
numbers from words.

`PdfLayoutReader.read_page()` takes what `page.get_text("dict")` returns, so all of it is testable without a PDF.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .models import TextBlock

# ---------------------------------------------------------------------------------------------------------- public types
LAYOUT_VERSION = 3  # bump when the reader's output changes: saved extraction results from an older version are then redone


@dataclass(frozen=True)
class LayoutOptions:
    headings: bool = True  # read titles and section headings
    footnotes: bool = False  # read the notes at the foot of the page
    furniture: bool = False  # read running headers/footers, page numbers, printer's marks
    verses: str = "auto"  # "auto": Bible-like documents get one paragraph per verse | "always" | "never"
    tables: bool = True  # read a ruled table row by row
    picture_text: bool = False  # read the words written inside a figure (its labels)

    def signature(self) -> list:
        return [self.headings, self.footnotes, self.furniture, self.verses, self.tables, self.picture_text]


@dataclass
class LayoutBlock(TextBlock):
    """One paragraph (or heading / footnote) in reading order. cx, cy = centre as a fraction of the page (for Keep/Ignore regions)."""

    kind: str = "body"  # body | title | heading | chapter | footnote | table | figure | image
    has_text: bool = False  # a figure that has words of its own (labels) in the PDF


# ---------------------------------------------------------------------------------------------------------- text cleaning
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}
_SPACES = dict.fromkeys(map(ord, "\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u00a0\u3000\t"), " ")
_ZERO = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
SOFT_HYPHEN = "\u00ad"
_DIGITS = re.compile(r"\d{1,3}(?:\s*[-\u2013]\s*\d{1,3})?")
_CALLER = re.compile(r"[A-Za-z]{1,2}|[*\u2020\u2021\u00a7\u00b6]")
_BULLET = re.compile(r"^(?:[\u2022\u25aa\u25e6\u2023\u00b7\u25cf]\s*|\d{1,2}[.)]\s+(?=[A-Z\u201c\"(]))")
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_SENTENCE_END = re.compile(r"[.!?\u2026\u3002\uff01\uff1f][\"'\u201d\u2019)\]]*$")


def clean_text(text: str) -> str:
    """Tidy a piece of PDF text (soft hyphens stay: the line joiner needs them)."""
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = text.translate(_ZERO).translate(_SPACES)
    return "".join(ch for ch in text if ch >= " " or ch == "\n")


def finish_paragraph(text: str) -> str:
    """Final tidy of an assembled paragraph."""
    text = text.replace(SOFT_HYPHEN, "")
    text = re.sub(r"(?:\.\s+){3,}\.?|\.{5,}", " ", text)  # table-of-contents leaders ". . . . ."
    text = re.sub(r"^[\u2022\u25aa\u25e6\u2023\u00b7\u25cf]\s*", "", text.strip())  # a bullet is not read
    text = re.sub(r"\s+([\u201d\u2019\u00bb)\]\.,;:!?])", r"\1", text)  # a stray space before closing punctuation
    text = re.sub(r"([\u201c\u2018\u00ab(\[])\s+", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------------------------------------- page model
@dataclass
class Span:
    text: str
    size: float
    font: str
    flags: int
    x0: float
    y0: float
    x1: float
    y1: float
    origin_y: float

    @property
    def family(self) -> str:
        return re.split(r"[-,+]", self.font.split("+")[-1])[0].lower()

    @property
    def bold(self) -> bool:
        low = self.font.lower()
        return bool(self.flags & 16) or "bold" in low or "semibold" in low or "black" in low


@dataclass
class Line:
    spans: list[Span]
    x0: float
    y0: float
    x1: float
    y1: float
    role: str = "body"  # body | title | heading | footnote | furniture | table
    col: int = 0
    spanning: bool = False
    rows: list | None = None  # for a table: [(cells, row bbox), ...]; for a figure: (marker, label text)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        """Typical size of the line's own text (character-weighted median; tiny raised marks don't count)."""
        vals = []
        for s in self.spans:
            n = len(s.text.strip())
            vals += [s.size] * n
        return statistics.median(vals) if vals else max((s.size for s in self.spans), default=0.0)

    @property
    def baseline(self) -> float:
        ys = [s.origin_y for s in self.spans if len(s.text.strip()) > 1] or [s.origin_y for s in self.spans]
        return statistics.median(ys)

    def family(self) -> str:
        counts: dict[str, int] = {}
        for s in self.spans:
            t = s.text.strip()
            if t and not _DIGITS.fullmatch(t):
                counts[s.family] = counts.get(s.family, 0) + len(t)
        return max(counts, key=counts.get) if counts else ""


@dataclass
class _Ctx:
    body: float
    family: str
    width: float
    height: float
    opts: LayoutOptions
    scripture: bool  # split at verse numbers and chapter numbers
    vocab: set = field(default_factory=set)  # lower-case words on the page: decides whether a line-end hyphen is real
    verse_numbers: list[int] = field(default_factory=list)

    @property
    def marks(self) -> bool:
        """Footnote marks (small raised numbers and letters) are dropped from the text, except when the user asked for the
        numbers to stay ("never")."""
        return self.opts.verses != "never"


def _parse_page(page: dict) -> list[Line]:
    lines: list[Line] = []
    for block in page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for ln in block.get("lines", []):
            spans = []
            for sp in ln.get("spans", []):
                text = clean_text(sp.get("text", ""))
                if not text:
                    continue
                x0, y0, x1, y1 = sp["bbox"]
                spans.append(Span(text, float(sp["size"]), sp.get("font", ""), int(sp.get("flags", 0)), x0, y0, x1, y1,
                                  float(sp.get("origin", (0, y1))[1])))
            if spans and "".join(s.text for s in spans).strip():
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(Line(spans, x0, y0, x1, y1))
    return _merge_same_baseline(lines)


def _merge_same_baseline(lines: list[Line]) -> list[Line]:
    """MuPDF sometimes splits one visual line in two (a tab, a page number): put pieces on the same baseline back together
    when they touch; keep them apart when there is a wide gap (two columns never touch)."""
    lines = sorted(lines, key=lambda l: (round(l.baseline), l.x0))
    out: list[Line] = []
    for l in lines:
        prev = out[-1] if out else None
        if prev and abs(prev.baseline - l.baseline) < 1.5 and 0 <= l.x0 - prev.x1 < 0.3 * max(prev.size, 1.0):
            prev.spans += l.spans
            prev.x1, prev.y0, prev.y1 = max(prev.x1, l.x1), min(prev.y0, l.y0), max(prev.y1, l.y1)
        else:
            out.append(l)
    return out


def _body_metrics(lines: list[Line]) -> tuple[float, str]:
    """(size, font family) of the ordinary text: the most common size, weighted by how much text has it."""
    buckets: dict[float, list[tuple[float, str, int]]] = {}
    for l in lines:
        for s in l.spans:
            n = len(s.text.strip())
            if n:
                buckets.setdefault(round(s.size * 2) / 2, []).append((s.size, s.family, n))
    if not buckets:
        return 10.0, ""
    key = max(buckets, key=lambda k: sum(n for _s, _f, n in buckets[k]))
    items = buckets[key]
    size = sum(s * n for s, _f, n in items) / sum(n for _s, _f, n in items)
    fams: dict[str, int] = {}
    for _s, f, n in items:
        fams[f] = fams.get(f, 0) + n
    return size, max(fams, key=fams.get)


# ---------------------------------------------------------------------------------------------------------- classification
_NUMERIC_LINE = re.compile(r"^\W*(?:\d{1,4}|[ivxlcdm]{1,7})\W*$", re.I)


def _style_differs(l: Line, c: _Ctx) -> bool:
    return l.size < 0.97 * c.body or (l.family() not in ("", c.family))


def _classify_lines(lines: list[Line], c: _Ctx) -> None:
    H = c.height
    # 1. printer's marks and running heads: the very top and bottom of the page
    for l in lines:
        short = len(l.text.strip()) <= 70
        if l.role != "body" or not short or l.size > 1.15 * c.body:
            continue
        if l.y0 >= 0.955 * H or (l.y1 <= 0.085 * H and (_style_differs(l, c) or _NUMERIC_LINE.match(l.text))):
            l.role = "furniture"
        elif (l.y0 >= 0.93 * H or l.y1 <= 0.10 * H) and _NUMERIC_LINE.match(l.text):
            l.role = "furniture"  # a bare page number
    # 2. footnotes: small text that runs down to the bottom of the text area
    rest = sorted((l for l in lines if l.role == "body"), key=lambda l: l.y0)
    chain: list[Line] = []
    for l in reversed(rest):
        if l.size <= 0.86 * c.body and l.y0 >= 0.45 * H:
            if chain and chain[-1].y0 - l.y1 > 2.2 * l.size:
                break
            chain.append(l)
        else:
            break
    for l in chain:
        l.role = "footnote"
    # 3. a bar of small print above the footnotes' natural place, at the very bottom: page numbers etc.
    for l in lines:
        if l.role == "body" and l.y0 >= 0.91 * H and len(l.text.strip()) <= 70 and _style_differs(l, c) and l.size <= 0.97 * c.body:
            l.role = "furniture"
    # 4. headings
    for l in lines:
        if l.role != "body":
            continue
        text = l.text.strip()
        letters = sum(ch.isalpha() for ch in text)
        if letters < 2:
            continue
        if l.size >= 1.25 * c.body and len(text) <= 120:
            l.role = "title"
        elif (len(text) <= 80 and l.family() not in ("", c.family) and not _SENTENCE_END.search(text)
              and (text.isupper() or all(s.bold for s in l.spans if len(s.text.strip()) > 1))):
            l.role = "heading"


def _find_columns(lines: list[Line], c: _Ctx) -> list[tuple[float, float]]:
    """Gutters (x0, x1) between text columns; [] for a single column."""
    body = [l for l in lines if l.role in ("body", "heading")]
    if len(body) < 6:
        return []
    left, right = min(l.x0 for l in body), max(l.x1 for l in body)
    tw = right - left
    if tw < 12 * c.body:
        return []
    narrow = sorted(((l.x0, l.x1) for l in body if (l.x1 - l.x0) < 0.62 * tw))
    if len(narrow) < 6:
        return []
    merged: list[list[float]] = []
    for a, b in narrow:
        if merged and a <= merged[-1][1] + 0.35 * c.body:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gutters = []
    for (a0, b0), (a1, b1) in zip(merged, merged[1:]):
        gap = a1 - b0
        centre = (a1 + b0) / 2
        n_left = sum(1 for x0, x1 in narrow if x1 <= b0 + 1)
        n_right = sum(1 for x0, x1 in narrow if x0 >= a1 - 1)
        if gap >= 0.5 * c.body and left + 0.15 * tw <= centre <= right - 0.15 * tw and n_left >= 3 and n_right >= 3:
            gutters.append((b0, a1))
    return gutters


def _order_lines(lines: list[Line], gutters: list[tuple[float, float]], c: _Ctx) -> list[Line]:
    """Reading order of the text lines: spanning lines and columns, band by band."""
    text = [l for l in lines if l.role in ("body", "heading", "title", "table", "figure")]
    if not gutters:
        return sorted(text, key=lambda l: (l.y0, l.x0))

    def column_of(l: Line) -> int:
        centre = (l.x0 + l.x1) / 2
        return sum(1 for g0, g1 in gutters if centre > (g0 + g1) / 2)

    for l in text:
        l.col = column_of(l)
        l.spanning = any(l.x0 < g0 - 2 and l.x1 > g1 + 2 for g0, g1 in gutters)
    out: list[Line] = []
    band: dict[int, list[Line]] = {}

    def flush():
        for col in sorted(band):
            out.extend(sorted(band[col], key=lambda l: (l.y0, l.x0)))
        band.clear()

    for l in sorted(text, key=lambda l: (l.y0, l.x0)):
        if l.spanning:
            flush()
            out.append(l)
        else:
            band.setdefault(l.col, []).append(l)
    flush()
    return out


# ---------------------------------------------------------------------------------------------------------- span roles
def _span_role(s: Span, line: Line, before: str, c: _Ctx, after: str = "") -> str:
    """chapter | verse | caller | dropcap | text, for one span of a body line."""
    t = s.text.strip()
    if not t or not c.marks:
        return "text"
    r = s.size / c.body
    raised = (line.baseline - s.origin_y) >= 0.18 * c.body or bool(s.flags & 1)
    small = r <= 0.8
    preceded_by_space = (not before.strip()) or before.endswith(" ") or before.endswith("\n")
    if _DIGITS.fullmatch(t):
        if r >= 1.8:
            return "chapter" if c.scripture else "text"
        styled = s.family != c.family or (s.bold and not any(sp.bold for sp in line.spans if sp is not s and len(sp.text.strip()) > 3))
        # a small raised number is a verse number when it starts a new sentence: after a space, or right after a full stop
        # with a capital letter following. Stuck to the middle of a sentence it is a footnote mark.
        new_sentence = bool(re.search(r"[.?!:;\u201d\u2019)\"']$", before.rstrip())) and bool(re.match(r"\s*[A-Z\u201c\u2018\"']", after))
        if c.scripture and ((styled and not small and not raised) or ((small or raised) and (preceded_by_space or new_sentence))):
            return "verse"
        if small or raised:
            return "caller"
        return "text"
    if (small or raised) and _CALLER.fullmatch(t):
        return "caller"
    if r >= 1.8 and re.fullmatch(r"[A-Za-z\u201c\u2018\"']{1,2}", t):
        return "dropcap"
    return "text"


def _number(text: str) -> int:
    m = re.match(r"\d+", text.strip())
    return int(m.group()) if m else 0


# ---------------------------------------------------------------------------------------------------------- assembling text
class _Builder:
    """Collects text into paragraphs and blocks, applying the line-joining rules."""

    def __init__(self, c: _Ctx):
        self.c = c
        self.blocks: list[LayoutBlock] = []
        self._parts: list[str] = []
        self._box: list[float] | None = None
        self._kind = "body"
        self._join_next_is_tight = False

    def _grow(self, l: Line) -> None:
        if self._box is None:
            self._box = [l.x0, l.y0, l.x1, l.y1]
        else:
            b = self._box
            b[0], b[1], b[2], b[3] = min(b[0], l.x0), min(b[1], l.y0), max(b[2], l.x1), max(b[3], l.y1)

    def _last_token(self) -> str:
        joined = "".join(self._parts)
        m = re.search(r"[^\W\d_][^\s]*-$", joined)
        return m.group() if m else ""

    def add(self, text: str, l: Line) -> None:
        """Append a piece of text found on line `l` to the current paragraph."""
        if not text:
            return
        self._grow(l)
        if self._parts and self._parts[-1].endswith(SOFT_HYPHEN):
            self._parts[-1] = self._parts[-1][:-1]  # a hyphen the typesetter added at the line end: the word carries on
            self._parts.append(text.lstrip())
        elif self._parts and self._parts[-1].endswith("-") and text[:1].islower():
            prefix = self._last_token()[:-1].replace(SOFT_HYPHEN, "")
            after = _WORD.match(text.lstrip().replace(SOFT_HYPHEN, ""))
            keep = False
            if prefix and after:
                joined, hyphenated = (prefix + after.group()).lower(), (prefix + "-" + after.group()).lower()
                keep = hyphenated in self.c.vocab and joined not in self.c.vocab
            if not keep:
                self._parts[-1] = self._parts[-1][:-1]  # "in-" + "formation": a hyphen from the line break
            self._parts.append(text.lstrip())
            self._join_next_is_tight = True
        else:
            self._parts.append(text)

    def newline(self) -> None:
        """A line break inside a paragraph is a space (unless the line ended in a hyphen: see add)."""
        if self._parts and not self._parts[-1].endswith((" ", SOFT_HYPHEN)) and not (
                self._parts[-1].endswith("-") and self._hyphen_breaks_word()):
            self._parts.append(" ")

    def _hyphen_breaks_word(self) -> bool:
        """The paragraph so far ends in "-": is that a line-break hyphen (the next piece will decide) or a real one?"""
        return True  # add() removes or keeps it once it sees the next word; a space is never wanted right after it

    def flush(self, kind: str | None = None) -> None:
        text = finish_paragraph("".join(self._parts))
        if text and self._box is not None:
            self._emit(text, kind or self._kind, self._box)
        self._parts, self._box, self._kind = [], None, "body"

    def _emit(self, text: str, kind: str, box: list[float]) -> None:
        c = self.c
        self.blocks.append(LayoutBlock(((box[0] + box[2]) / 2) / c.width, ((box[1] + box[3]) / 2) / c.height, text, kind))

    def emit_line(self, text: str, l: Line, kind: str) -> None:
        text = finish_paragraph(text)
        if text:
            self._emit(text, kind, [l.x0, l.y0, l.x1, l.y1])

    @property
    def open(self) -> bool:
        return bool(self._parts)


def _new_paragraph_before(prev: Line, cur: Line, columns: dict, c: _Ctx) -> bool:
    """A paragraph break between two consecutive body lines (used when there are no verse numbers to split on)."""
    text = cur.text.lstrip()
    if _BULLET.match(text):
        return True
    p_left, p_right = columns.get(prev.col, (prev.x0, prev.x1))
    c_left, _c_right = columns.get(cur.col, (cur.x0, cur.x1))
    same_col = cur.col == prev.col
    ends = bool(_SENTENCE_END.search(prev.text.strip()))
    if same_col and cur.y0 - prev.y1 > 0.55 * max(prev.y1 - prev.y0, 1.0):
        return True  # an extra gap
    # first-line indent: the line is more indented than the one before, which finished a sentence
    if ends and (cur.x0 - c_left) - (prev.x0 - p_left) >= 0.6 * c.body:
        return True
    prev_short = (prev.x1 - prev.x0) < 0.72 * (p_right - p_left)
    return prev_short and ends and text[:1].isupper()


def _read_body(lines: list[Line], c: _Ctx, b: _Builder) -> None:
    columns: dict[int, tuple[float, float]] = {}
    for l in lines:
        if l.role == "body":
            lo, hi = columns.get(l.col, (l.x0, l.x1))
            columns[l.col] = (min(lo, l.x0), max(hi, l.x1))
    prev: Line | None = None
    pending_dropcap = ""
    head: list[Line] = []

    def flush_heading():
        if head and c.opts.headings:
            b.emit_line(" ".join(h.text.strip() for h in head), head[0], "title" if head[0].role == "title" else "heading")
            box = [min(h.x0 for h in head), min(h.y0 for h in head), max(h.x1 for h in head), max(h.y1 for h in head)]
            blk = b.blocks[-1]
            blk.cx, blk.cy = ((box[0] + box[2]) / 2) / c.width, ((box[1] + box[3]) / 2) / c.height
        head.clear()

    for l in lines:
        if l.role == "figure":
            b.flush()
            flush_heading()
            marker, labels = l.rows
            cx, cy = ((l.x0 + l.x1) / 2) / c.width, ((l.y0 + l.y1) / 2) / c.height
            b.blocks.append(LayoutBlock(cx, cy, marker, "figure", has_text=bool(labels)))
            if labels and c.opts.picture_text:
                b.blocks.append(LayoutBlock(cx, cy, labels, "image"))
            prev = None
            continue
        if l.role == "table":
            b.flush()
            flush_heading()
            _emit_table(b, l)
            prev = None
            continue
        if l.role in ("title", "heading"):
            b.flush()
            if head and (head[-1].role != l.role or abs(head[-1].size - l.size) > 0.6 or l.y0 - head[-1].y1 > 0.9 * head[-1].size
                         or head[-1].col != l.col):
                flush_heading()
            head.append(l)
            prev = None
            continue
        flush_heading()
        if not c.scripture and prev is not None and b.open and _new_paragraph_before(prev, l, columns, c):
            b.flush()
        elif b.open:
            b.newline()
        before = ""  # the text of this line so far (an empty string means we are at the start of the line)
        for k, s in enumerate(l.spans):
            role = _span_role(s, l, before, c, "".join(sp.text for sp in l.spans[k + 1:]))
            text = s.text
            if role == "chapter":
                b.flush()
                _emit_chapter(b, _number(text), l)
                continue
            if role == "verse":
                c.verse_numbers.append(_number(text))
                b.flush()
                continue
            if role == "caller":
                continue
            if role == "dropcap":
                pending_dropcap = text.strip()
                continue
            if pending_dropcap and text.strip():
                text = pending_dropcap + text.lstrip()
                pending_dropcap = ""
            b.add(text, l)
            before += text
        prev = l
    flush_heading()
    b.flush()


def _emit_table(b: _Builder, l: Line) -> None:
    """A table is read row by row. With a header row every cell is announced by its column name: "Name: Ana. Age: 30." """
    rows = [(cells, box) for cells, box in (l.rows or []) if any(cells)]
    if not rows:
        return
    first = rows[0][0]
    header = len(rows) > 1 and all(first) and not any(re.fullmatch(r"[\d\s.,%$€£-]+", h) for h in first)
    for cells, box in rows[1:] if header else rows:
        if header:
            parts = [f"{h}: {v}" for h, v in zip(first, cells) if v]
        else:
            parts = [v for v in cells if v]
        text = finish_paragraph(". ".join(parts) if header else ", ".join(parts))
        if text and not _SENTENCE_END.search(text):
            text += "."  # a full stop gives every row its pause
        if text:
            b.blocks.append(LayoutBlock(((box[0] + box[2]) / 2) / b.c.width, ((box[1] + box[3]) / 2) / b.c.height, text, "table"))


def _emit_chapter(b: _Builder, n: int, l: Line) -> None:
    """A chapter number becomes its own short paragraph, placed before the section heading that came just before it."""
    block = LayoutBlock(0.0, 0.0, f"Chapter {n}", "chapter")
    block.cx = ((l.x0 + l.x1) / 2) / b.c.width
    block.cy = ((l.y0 + l.y1) / 2) / b.c.height
    at = len(b.blocks)
    while at > 0 and b.blocks[at - 1].kind == "heading":
        at -= 1
    b.blocks.insert(at, block)


def _read_footnotes(lines: list[Line], c: _Ctx, b: _Builder) -> None:
    """Each note starts with its little raised letter (or number/symbol); that mark is dropped, the note text is kept."""
    notes: list[tuple[list[str], list[Line]]] = []
    for l in lines:
        size = l.size
        for s in l.spans:
            t = s.text.strip()
            is_mark = bool(t) and s.size <= 0.75 * size and (_CALLER.fullmatch(t) or _DIGITS.fullmatch(t))
            if is_mark:
                notes.append(([], [l]))
            else:
                if not notes:
                    notes.append(([], [l]))
                notes[-1][0].append(s.text)
                if notes[-1][1][-1] is not l:
                    notes[-1][1].append(l)
    for parts, ls in notes:
        text = finish_paragraph("".join(parts))
        if text:
            box = [min(l.x0 for l in ls), min(l.y0 for l in ls), max(l.x1 for l in ls), max(l.y1 for l in ls)]
            b._emit(text, "footnote", box)


def _take_figures(lines: list[Line], figures: list) -> list[Line]:
    """A figure keeps its place in the page; the words drawn inside it (labels) are no longer part of the text."""
    out = list(lines)
    for bbox, marker in figures:
        x0, y0, x1, y1 = bbox
        inside = [l for l in out if x0 - 1 <= (l.x0 + l.x1) / 2 <= x1 + 1 and y0 - 1 <= (l.y0 + l.y1) / 2 <= y1 + 1]
        out = [l for l in out if l not in inside]
        labels = finish_paragraph(" ".join(l.text.strip() for l in sorted(inside, key=lambda l: (round(l.y0 / 4), l.x0))))
        out.append(Line([], x0, y0, x1, y1, role="figure", rows=(marker, labels)))
    return out


def _take_tables(lines: list[Line], tables: list) -> list[Line]:
    """Remove the text lines that sit inside a table and put one 'table' line in their place."""
    out = list(lines)
    for bbox, rows in tables:
        x0, y0, x1, y1 = bbox
        inside = [l for l in out if x0 - 1 <= (l.x0 + l.x1) / 2 <= x1 + 1 and y0 - 1 <= (l.y0 + l.y1) / 2 <= y1 + 1]
        cleaned = [([finish_paragraph(clean_text(str(c or "").replace("\n", " "))) for c in cells], rb) for cells, rb in rows]
        if not inside or len(cleaned) < 2 or max(len(c) for c, _ in cleaned) < 2:
            continue
        out = [l for l in out if l not in inside]
        out.append(Line([], x0, y0, x1, y1, role="table", rows=cleaned))
    return out


# ---------------------------------------------------------------------------------------------------------- the reader
class PdfLayoutReader:
    """Stateless: give it a page dict and options, get blocks back in reading order."""

    @staticmethod
    def read_page(page: dict, width: float, height: float, opts: LayoutOptions, scripture: bool | None = None,
                  stats: dict | None = None, tables: list | None = None, figures: list | None = None) -> list[LayoutBlock]:
        """`tables`: [(bbox, [(cells, row bbox), ...]), ...] as found by the loader (the reader only decides how to say them)."""
        lines = _parse_page(page)
        if figures:
            lines = _take_figures(lines, figures)
        if opts.tables and tables:
            lines = _take_tables(lines, tables)
        if not lines:
            return []
        body, family = _body_metrics(lines)
        if scripture is None:
            scripture = opts.verses == "always"
        if opts.verses == "never":
            scripture = False
        vocab = {w.lower() for l in lines for w in _WORD.findall(l.text.replace(SOFT_HYPHEN, ""))}

        def context(verses: bool) -> _Ctx:
            return _Ctx(body, family, float(width), float(height), opts, verses, vocab=vocab)

        c = context(bool(scripture))
        _classify_lines(lines, c)
        gutters = _find_columns(lines, c)
        ordered = _order_lines(lines, gutters, c)
        builder = _Builder(c)
        _read_body(ordered, c, builder)
        if scripture and not c.verse_numbers and not any(b.kind == "chapter" for b in builder.blocks):  # no verses on this page
            # a page of a Bible that has no verse numbers (an introduction, a map page): read it like ordinary text
            c = context(False)
            _classify_lines(lines, c)
            builder = _Builder(c)
            _read_body(ordered, c, builder)
        if opts.footnotes:
            notes = sorted((l for l in lines if l.role == "footnote"), key=lambda l: (l.y0, l.x0))
            _read_footnotes(notes, c, builder)
        if opts.furniture:
            for l in sorted((l for l in lines if l.role == "furniture"), key=lambda l: (l.y0, l.x0)):
                builder.emit_line(l.text, l, "body")
        if stats is not None:
            stats["verse_numbers"] = list(c.verse_numbers)
            stats["columns"] = len(gutters) + 1
            stats["body_size"] = body
        return builder.blocks

    @staticmethod
    def verse_run(page: dict, width: float, height: float) -> int:
        """Longest run of verse numbers going 1, 2, 3... on this page (0 when there are none). Used to recognise Bibles."""
        stats: dict = {}
        PdfLayoutReader.read_page(page, width, height, LayoutOptions(verses="always"), scripture=True, stats=stats)
        best = run = 0
        prev = None
        for n in stats.get("verse_numbers", []):
            run = run + 1 if prev is not None and n == prev + 1 else 1
            best, prev = max(best, run), n
        return best


def stitch_pages(texts: list[str]) -> list[str]:
    """A paragraph that carries on over a page break is one paragraph: if a page's last paragraph doesn't end a sentence and the
    next page's first paragraph starts in lower case, join them (into the next page's text, leaving the earlier page shorter)."""
    out = [t for t in texts]
    for i in range(len(out) - 1):
        a, b = out[i].rstrip(), out[i + 1].lstrip()
        if not a or not b:
            continue
        last = a.split("\n\n")[-1]
        first = b.split("\n\n")[0]
        if (not _SENTENCE_END.search(last.strip()) and first[:1].islower() and len(last) > 20
                and not re.match(r"^(?:Chapter|CHAPTER)\s+\d+$", last.strip())):
            head = a.rsplit("\n\n", 1)[0] if "\n\n" in a else ""
            out[i] = head
            out[i + 1] = last.rstrip() + " " + b
    return out
