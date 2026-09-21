"""Small plain data types shared across EchoRead."""
from __future__ import annotations

from dataclasses import dataclass

SPEEDS = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)

# Chinese voices that were listed but never existed in the model pykokoro uses for Chinese (Kokoro v1.1-zh) -> their replacements.
RENAMED_VOICES = {"zf_xiaobei": "zf_001", "zm_yunxi": "zm_009"}


def speed_key(speed: float) -> str:
    """Stable folder/file name for a speed, e.g. 1.25 -> '1.25'."""
    return f"{float(speed):.2f}"


def speed_label(speed: float) -> str:
    text = speed_key(speed).rstrip("0")
    if text.endswith("."):
        text += "0"
    return text + "x"


def nearest_speed(value: float) -> float:
    return min(SPEEDS, key=lambda s: abs(s - float(value)))


PICTURE_PREFIX, PICTURE_SUFFIX = "\u27e6image:", "\u27e7"  # a paragraph that is a picture: "⟦image:<file>.png⟧"


def picture_marker(name: str) -> str:
    return f"{PICTURE_PREFIX}{name}{PICTURE_SUFFIX}"


def picture_name(text: str) -> str | None:
    """The stored picture file a paragraph stands for, or None when it is ordinary text."""
    t = text.strip()
    if t.startswith(PICTURE_PREFIX) and t.endswith(PICTURE_SUFFIX) and len(t) > len(PICTURE_PREFIX) + 1:
        return t[len(PICTURE_PREFIX):-1]
    return None


def is_picture(text: str) -> bool:
    return picture_name(text) is not None


@dataclass
class Paragraph:
    index: int
    text: str
    skip: bool = False
    ignored: bool = False
    bookmarked: bool = False
    highlighted: bool = False

    @property
    def picture(self) -> str | None:
        return picture_name(self.text)


@dataclass
class Region:
    kind: str  # "keep" | "ignore"
    rect: tuple  # (x0, y0, x1, y1), each 0..1 of the page


@dataclass
class OcrLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class TextBlock:
    """A block of a PDF text layer, positioned by its centre (0..1 of the page)."""

    cx: float
    cy: float
    text: str


@dataclass
class DocInfo:
    doc_id: str
    title: str
    kind: str  # "file" | "text" | "link"
    source: str | None = None
    page_count: int = 0
    text_hash: str = ""
