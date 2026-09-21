"""Opens PDFs and images and renders their pages."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image, ImageOps

try:  # PyMuPDF >= 1.24 exposes `pymupdf`; older installs only have `fitz`
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

from .models import TextBlock


class DocumentLoader:
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

    def __init__(self):
        self._lock = threading.RLock()  # MuPDF is not thread-safe
        self._pdf = None
        self._images: list[Image.Image] = []
        self._pdfium = None  # a PDF MuPDF couldn't open, rendered by pdfium instead (see open_recovered)
        self.kind: str | None = None  # "pdf" | "image" | "recovered"
        self.path: Path | None = None

    @classmethod
    def supports(cls, path) -> bool:
        ext = Path(path).suffix.lower()
        return ext == ".pdf" or ext in cls.IMAGE_EXTS

    @property
    def is_open(self) -> bool:
        return self.kind is not None

    @property
    def page_count(self) -> int:
        with self._lock:
            if self.kind == "pdf":
                return self._pdf.page_count
            if self.kind == "recovered":
                return len(self._pdfium)
            return len(self._images)

    def open(self, path) -> int:
        path = Path(path)
        ext = path.suffix.lower()
        with self._lock:
            self.close()
            if ext == ".pdf":
                self._pdf = fitz.open(str(path))
                self.kind = "pdf"
            elif ext in self.IMAGE_EXTS:
                self._images = [ImageOps.exif_transpose(Image.open(path)).convert("RGB")]
                self.kind = "image"
            else:
                raise ValueError(f"Unsupported file type: {ext or path.name}")
            self.path = path
            return self.page_count

    def open_recovered(self, path) -> int:
        """Last resort for a damaged PDF that MuPDF refuses: pdfium is more forgiving. Its pages can only be OCR'd
        (they are drawn as pictures), so there is no text layer."""
        path = Path(path)
        with self._lock:
            self.close()
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(path))
            if len(doc) == 0:
                doc.close()
                raise ValueError("The PDF has no readable pages.")
            self._pdfium = doc
            self.kind = "recovered"
            self.path = path
            return len(doc)

    def close(self) -> None:
        with self._lock:
            if self._pdfium is not None:
                try:
                    self._pdfium.close()
                except Exception:
                    pass
            self._pdfium = None
            if self._pdf is not None:
                try:
                    self._pdf.close()
                except Exception:
                    pass
            self._pdf = None
            self._images = []
            self.kind = None
            self.path = None

    def render_page(self, index: int, zoom: float = 2.0) -> Image.Image:
        with self._lock:
            if self.kind == "image":
                return self._images[index].copy()
            if self.kind == "recovered":
                return self._pdfium[index].render(scale=zoom).to_pil().convert("RGB")
            page = self._pdf[index]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    def has_text_layer(self, index: int) -> bool:
        with self._lock:
            if self.kind != "pdf":
                return False
            return len(self._pdf[index].get_text("text").strip()) >= 20

    def text_blocks(self, index: int) -> list[TextBlock]:
        """Text-layer blocks in reading order, positioned as fractions of the (rotated) page."""
        with self._lock:
            if self.kind != "pdf":
                return []
            page = self._pdf[index]
            width, height = page.rect.width, page.rect.height
            blocks = []
            for b in page.get_text("blocks", sort=True):
                if b[6] != 0 or not b[4].strip():
                    continue
                r = fitz.Rect(b[0], b[1], b[2], b[3]) * page.rotation_matrix
                blocks.append(TextBlock((r.x0 + r.x1) / 2 / width, (r.y0 + r.y1) / 2 / height, b[4]))
            return blocks
