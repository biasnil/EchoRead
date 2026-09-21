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
        self._scripture: bool | None = None  # does this PDF look like a Bible? (decided on first use)
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
            self._scripture = None
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

    def layout_blocks(self, index: int, options, figures: list | None = None) -> list:
        """The page's text as paragraphs in reading order (columns, headings, footnotes, verse numbers handled).
        Pages MuPDF reports as rotated fall back to the plain text blocks."""
        from .pdflayout import PdfLayoutReader

        with self._lock:
            if self.kind != "pdf":
                return []
            page = self._pdf[index]
            if page.rotation:
                return self.text_blocks(index)
            data = page.get_text("dict")
            width, height = page.rect.width, page.rect.height
            scripture = options.verses == "always" or (options.verses == "auto" and self._looks_like_scripture())
            tables = self._find_tables(page) if options.tables else []
        return PdfLayoutReader.read_page(data, width, height, options, scripture=scripture, tables=tables, figures=figures)

    def figure_regions(self, index: int) -> list[tuple[tuple[float, float, float, float], bytes]]:
        """Figures on a PDF page that has text: pictures placed on it, and drawings (arrows, boxes, diagrams built from vector
        shapes with text labels). Each comes as (bbox in points, the region rendered as PNG bytes). Small things, rules, table
        grids, mostly-text areas and page-sized backgrounds are not figures."""
        with self._lock:
            if self.kind != "pdf":
                return []
            page = self._pdf[index]
            if page.rotation:
                return []
            W, H = page.rect.width, page.rect.height
            page_area = W * H
            boxes: list[list[float]] = []
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                w, h = x1 - x0, y1 - y0
                if w >= 40 and h >= 30 and 0.02 <= w * h / page_area <= 0.85:
                    boxes.append([x0, y0, x1, y1])
            rects = []
            drawings = page.get_drawings()
            if len(drawings) <= 3000:
                for d in drawings:
                    r = d["rect"]
                    w, h = r.width, r.height
                    if (w > 0.6 * W and h < 4) or (h > 0.6 * H and w < 4) or w * h > 0.6 * page_area or (w < 0.5 and h < 0.5):
                        continue
                    rects.append((r.x0, r.y0, r.x1, r.y1))
            words = [(w[0], w[1], w[2], w[3]) for w in page.get_text("words")] if rects else []
            for cluster in self._drawing_clusters(rects, 40.0):
                x0, y0, x1, y1 = (min(r[0] for r in cluster), min(r[1] for r in cluster), max(r[2] for r in cluster), max(r[3] for r in cluster))
                area = (x1 - x0) * (y1 - y0)
                thin = sum(1 for r in cluster if (r[2] - r[0]) < 2 or (r[3] - r[1]) < 2)
                text = sum((w[2] - w[0]) * (w[3] - w[1]) for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1)
                if len(cluster) < 4 or area < 0.03 * page_area or area > 0.85 * page_area or thin >= 0.8 * len(cluster) or text > 0.3 * area:
                    continue
                boxes.append([x0, y0, x1, y1])
            boxes = self._merge_boxes(boxes, 10.0)
            if boxes:  # short labels sitting just outside a figure ("Upload" under an arrow) belong to it
                short = []
                for block in page.get_text("dict").get("blocks", []):
                    for ln in block.get("lines", []):
                        text = "".join(s["text"] for s in ln["spans"]).strip()
                        if text and len(text.split()) <= 3 and len(text) <= 40:
                            short.append(ln["bbox"])
                for box in boxes:
                    for _ in range(2):
                        for x0, y0, x1, y1 in short:
                            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                            if box[0] - 24 <= cx <= box[2] + 24 and box[1] - 24 <= cy <= box[3] + 24:
                                box[0], box[1], box[2], box[3] = min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)
                boxes = self._merge_boxes(boxes, 10.0)
            out = []
            for x0, y0, x1, y1 in boxes:
                clip = fitz.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 4) & page.rect
                if clip.is_empty or clip.width < 20 or clip.height < 20:
                    continue
                zoom = max(1.0, min(2.0, 1600 / clip.width))
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csRGB, alpha=False)
                out.append(((clip.x0, clip.y0, clip.x1, clip.y1), pix.tobytes("png")))
            return sorted(out, key=lambda r: (r[0][1], r[0][0]))

    @staticmethod
    def _drawing_clusters(rects: list, gap: float) -> list[list]:
        """Group rectangles that touch or lie within `gap` points of each other (union-find over a sweep in x)."""
        n = len(rects)
        parent = list(range(n))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        order = sorted(range(n), key=lambda i: rects[i][0])
        active: list[int] = []
        for i in order:
            x0, y0, x1, y1 = rects[i]
            active = [j for j in active if rects[j][2] + gap >= x0]
            for j in active:
                a = rects[j]
                if y0 - gap <= a[3] and a[1] - gap <= y1:
                    parent[find(i)] = find(j)
            active.append(i)
        groups: dict[int, list] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(rects[i])
        return list(groups.values())

    @staticmethod
    def _merge_boxes(boxes: list[list[float]], gap: float) -> list[list[float]]:
        boxes = [list(b) for b in boxes]
        changed = True
        while changed:
            changed = False
            for a in range(len(boxes)):
                for b in range(a + 1, len(boxes)):
                    A, B = boxes[a], boxes[b]
                    if A[0] - gap <= B[2] and B[0] - gap <= A[2] and A[1] - gap <= B[3] and B[1] - gap <= A[3]:
                        boxes[a] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                        del boxes[b]
                        changed = True
                        break
                if changed:
                    break
        return boxes

    @staticmethod
    def _find_tables(page) -> list:
        """Ruled tables on a page as [(bbox, [(cells, row bbox), ...])]. PyMuPDF's finder takes ~150 ms a page, so it only runs on
        pages that have at least three long horizontal and two long vertical rules."""
        try:
            h = v = 0
            for dr in page.get_drawings():
                r = dr["rect"]
                if r.height <= 1.5 and r.width >= 40:
                    h += 1
                elif r.width <= 1.5 and r.height >= 20:
                    v += 1
            if h < 3 or v < 2:
                return []
            found = []
            for t in page.find_tables().tables:
                cells = t.extract()
                boxes = [tuple(r.bbox) for r in t.rows]
                if len(boxes) == len(cells):
                    found.append((tuple(t.bbox), list(zip(cells, boxes))))
            return found
        except Exception:
            return []

    IMAGE_ZOOM = 2.5

    def image_regions(self, index: int) -> list[tuple[tuple[float, float, float, float], Image.Image]]:
        """Pictures placed on a PDF page, each as (bbox as fractions of the page, the picture rendered as an image). Small ones
        (icons, bullets, rules) and pictures that cover nearly the whole page (a scan behind its own text layer) are left out."""
        with self._lock:
            if self.kind != "pdf":
                return []
            page = self._pdf[index]
            if page.rotation:
                return []
            width, height = page.rect.width, page.rect.height
            out = []
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                w, h = x1 - x0, y1 - y0
                area = w * h / (width * height)
                if w < 60 or h < 40 or area < 0.03 or area > 0.85:
                    continue
                clip = fitz.Rect(x0, y0, x1, y1) & page.rect
                if clip.is_empty:
                    continue
                pix = page.get_pixmap(matrix=fitz.Matrix(self.IMAGE_ZOOM, self.IMAGE_ZOOM), clip=clip, colorspace=fitz.csRGB, alpha=False)
                out.append(((clip.x0 / width, clip.y0 / height, clip.x1 / width, clip.y1 / height),
                            Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
            return sorted(out, key=lambda r: (r[0][1], r[0][0]))

    def looks_like_scripture(self) -> bool:
        with self._lock:
            return self._looks_like_scripture()

    SAMPLE_PAGES = 12

    def _looks_like_scripture(self) -> bool:
        """Verse numbers going 1, 2, 3... on several pages spread over the document: a Bible. Decided once per file."""
        from .pdflayout import PdfLayoutReader

        if self._scripture is not None:
            return self._scripture
        n = self._pdf.page_count if self._pdf is not None else 0
        picks = list(range(n)) if n <= self.SAMPLE_PAGES else sorted({round(k * (n - 1) / (self.SAMPLE_PAGES - 1)) for k in range(self.SAMPLE_PAGES)})
        hits = text_pages = 0
        for i in picks:
            page = self._pdf[i]
            if page.rotation or len(page.get_text("text").strip()) < 20:
                continue
            text_pages += 1
            try:
                run = PdfLayoutReader.verse_run(page.get_text("dict"), page.rect.width, page.rect.height)
            except Exception:
                run = 0
            hits += run >= 5
        self._scripture = hits >= 2 or (hits >= 1 and text_pages <= 2)
        return self._scripture

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
