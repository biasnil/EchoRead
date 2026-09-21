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


@dataclass
class Paragraph:
    index: int
    text: str
    skip: bool = False
    ignored: bool = False
    bookmarked: bool = False
    highlighted: bool = False


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