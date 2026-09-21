"""Draw Keep / Ignore boxes on a page image."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QImage
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QRadioButton, QScrollArea, QVBoxLayout)
from PIL import Image

from core.loader import DocumentLoader
from core.regions import RegionMask
from .widgets import make_button, make_label


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGB")
    w, h = img.size
    return QImage(img.tobytes("raw", "RGB"), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class RegionCanvas(QLabel):
    """The page image. Emits the dragged rectangle (pixels) when the mouse is released."""

    rect_drawn = pyqtSignal(float, float, float, float)
    dragging = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.start: tuple[float, float] | None = None
        self.cur: tuple[float, float] | None = None

    def _pos(self, event) -> tuple[float, float]:
        p, pm = event.position(), self.pixmap()
        return max(0.0, min(p.x(), pm.width())), max(0.0, min(p.y(), pm.height()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            self.start = self.cur = self._pos(event)

    def mouseMoveEvent(self, event) -> None:
        if self.start is not None:
            self.cur = self._pos(event)
            self.dragging.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self.start is None:
            return
        self.cur = self._pos(event)
        (ax, ay), (bx, by) = self.start, self.cur
        self.start = self.cur = None
        self.rect_drawn.emit(ax, ay, bx, by)


class RegionsDialog(QDialog):
    DISPLAY_WIDTH = 820

    def __init__(self, loader: DocumentLoader, mask: RegionMask, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._mask = mask
        self._page = 0
        self._base: QPixmap | None = None
        self.run_ocr = False
        self.setWindowTitle("Regions — choose what to read")
        self.resize(1000, 820)
        root = QVBoxLayout(self)

        tools = QHBoxLayout()
        self._keep = QRadioButton("Keep")
        self._keep.setChecked(True)
        self._ignore = QRadioButton("Ignore")
        self._only = QCheckBox("Only (read just the Keep regions)")
        self._only.setChecked(mask.only)
        self._all = QCheckBox("Apply to all pages")
        self._all.setChecked(mask.apply_all)
        for w in (self._keep, self._ignore, self._only, self._all):
            tools.addWidget(w)
        tools.addStretch(1)
        root.addLayout(tools)
        root.addWidget(make_label(
            "Drag on the page to draw a box. Unmarked areas are kept. With 'Only', Keep boxes are read in the order you drew them.",
            "muted", wrap=True))

        self._canvas = RegionCanvas()
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        root.addWidget(scroll, 1)

        nav = QHBoxLayout()
        self._prev = make_button("◀", callback=lambda: self._go(-1))
        self._next = make_button("▶", callback=lambda: self._go(1))
        self._page_label = QLabel("")
        for w in (self._prev, self._page_label, self._next):
            nav.addWidget(w)
        nav.addStretch(1)
        nav.addWidget(make_button("Undo", callback=self._undo))
        nav.addWidget(make_button("Clear all", callback=self._clear))
        nav.addWidget(make_button("Close", callback=self.accept))
        nav.addWidget(make_button("Run OCR", "primary", self._run))
        root.addLayout(nav)

        self._canvas.rect_drawn.connect(self._on_rect)
        self._canvas.dragging.connect(self._redraw)
        self._only.toggled.connect(lambda v: setattr(self._mask, "only", bool(v)))
        self._all.toggled.connect(lambda v: setattr(self._mask, "apply_all", bool(v)))
        self._load_page()

    # -- pages
    def _load_page(self) -> None:
        img = self._loader.render_page(self._page, zoom=1.6)
        qimg = pil_to_qimage(img).scaledToWidth(self.DISPLAY_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self._base = QPixmap.fromImage(qimg)
        count = self._loader.page_count
        self._page_label.setText(f"Page {self._page + 1} / {count}")
        self._prev.setEnabled(self._page > 0)
        self._next.setEnabled(self._page < count - 1)
        self._redraw()

    def _go(self, delta: int) -> None:
        self._page = max(0, min(self._loader.page_count - 1, self._page + delta))
        self._load_page()

    # -- drawing
    def _redraw(self) -> None:
        pm = self._base.copy()
        p = QPainter(pm)
        keep_n = 0
        for r in self._mask.for_page(self._page):
            x0, y0, x1, y1 = r.rect
            rect = (int(x0 * pm.width()), int(y0 * pm.height()), int((x1 - x0) * pm.width()), int((y1 - y0) * pm.height()))
            color = QColor("#22C55E") if r.kind == "keep" else QColor("#EF4444")
            fill = QColor(color)
            fill.setAlpha(55)
            p.setPen(QPen(color, 2))
            p.setBrush(fill)
            p.drawRect(*rect)
            if r.kind == "keep":
                keep_n += 1
            p.setPen(color.darker(160))
            p.drawText(rect[0] + 6, rect[1] + 16, f"Keep {keep_n}" if r.kind == "keep" else "Ignore")
        c = self._canvas
        if c.start is not None and c.cur is not None:
            p.setPen(QPen(QColor("#22C55E") if self._keep.isChecked() else QColor("#EF4444"), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(int(min(c.start[0], c.cur[0])), int(min(c.start[1], c.cur[1])),
                       int(abs(c.cur[0] - c.start[0])), int(abs(c.cur[1] - c.start[1])))
        p.end()
        self._canvas.setPixmap(pm)
        self._canvas.resize(pm.size())

    def _on_rect(self, ax: float, ay: float, bx: float, by: float) -> None:
        if abs(bx - ax) >= 8 and abs(by - ay) >= 8:
            w, h = self._base.width(), self._base.height()
            rect = (min(ax, bx) / w, min(ay, by) / h, max(ax, bx) / w, max(ay, by) / h)
            page = None if self._all.isChecked() else self._page
            self._mask.add("keep" if self._keep.isChecked() else "ignore", rect, page)
        self._redraw()

    def _undo(self) -> None:
        self._mask.undo()
        self._redraw()

    def _clear(self) -> None:
        self._mask.clear()
        self._redraw()

    def _run(self) -> None:
        self.run_ocr = True
        self.accept()
