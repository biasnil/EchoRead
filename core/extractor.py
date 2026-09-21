"""PDF/image -> text: a PDF's own text layer when it has one, otherwise OCR, honouring Keep/Ignore regions.

Every finished page is saved to disk as it completes, so a cancelled, crashed or interrupted run of a
big scan resumes where it stopped instead of starting over.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from .errors import get_logger
from .loader import DocumentLoader
from .ocr import OCRService
from .pdflayout import LAYOUT_VERSION, LayoutOptions, clean_text, finish_paragraph, stitch_pages
from .paragraphs import ParagraphModel, math_to_speech
from .regions import RegionMask
from .settings import SettingsManager
from .workers import TaskWorker


class TextExtractor:
    STAGES = ("layout", "ocr")
    WEIGHTS = {"layout": 20, "ocr": 50}
    LABELS = {"layout": "Detecting layout", "ocr": "Recognizing text"}
    MAX_CONSECUTIVE_FAILURES = 3  # this many pages in a row failing means something systemic: stop and say why

    def __init__(self, loader: DocumentLoader, ocr: OCRService, regions: RegionMask, settings: SettingsManager, store_image=None):
        self._loader = loader
        self._ocr = ocr
        self._regions = regions
        self._settings = settings
        self._store_image = store_image  # callable(png bytes) -> file name; None: pictures are not kept
        self.checkpoint_dir: Path | None = None  # set per document by the window
        self.fresh = False  # True: ignore and delete saved progress first
        self.failed_pages: list[int] = []  # 1-based pages that couldn't be read in the last run
        self.resumed_pages = 0

    # -- progress text
    def _report(self, worker: TaskWorker, page: int, pages: int, stage: str, frac: float) -> None:
        total = sum(self.WEIGHTS[s] for s in self.STAGES)
        within = sum(self.WEIGHTS[s] for s in self.STAGES[: self.STAGES.index(stage)]) + self.WEIGHTS[stage] * frac
        pct = max(0, min(100, int(100 * ((page - 1) + within / total) / max(pages, 1))))
        worker.report(f"Page {page} / {pages} — {self.LABELS[stage]} — {pct}%", pct)

    # -- checkpoints
    def layout_options(self) -> LayoutOptions:
        s = self._settings
        return LayoutOptions(headings=s.pdf_headings, footnotes=s.pdf_footnotes, furniture=s.pdf_furniture, verses=s.pdf_verses,
                             tables=s.pdf_tables, picture_text=s.pdf_read_images)

    OCR_ZOOM = {"standard": 2.0, "sharp": 3.5}  # page render scale for OCR (2.0 is about 144 dpi)

    def _signature(self) -> str:
        raw = json.dumps([self._regions.signature(), self._settings.ocr_engine, self._settings.ocr_lang,
                          self.layout_options().signature(), LAYOUT_VERSION, self._settings.ocr_quality, self._settings.read_math,
                          self._settings.pdf_read_images, self._settings.ocr_smart,
                          self._settings.pdf_show_pictures and self._store_image is not None])
        return hashlib.sha1(raw.encode()).hexdigest()

    def _open_checkpoint(self, pages: int) -> Path | None:
        cp = self.checkpoint_dir
        if cp is None:
            return None
        meta = cp / "meta.json"
        want = {"sig": self._signature(), "pages": pages}
        try:
            have = json.loads(meta.read_text("utf-8"))
        except Exception:
            have = None
        if self.fresh or have != want:  # different regions/settings/file: saved pages don't apply
            shutil.rmtree(cp, ignore_errors=True)
        cp.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps(want), "utf-8")
        return cp

    @staticmethod
    def _page_file(cp: Path, i: int) -> Path:
        return cp / f"page_{i:05d}.txt"

    def _save_page(self, cp: Path | None, i: int, text: str) -> None:
        if cp is not None:
            tmp = self._page_file(cp, i).with_suffix(".tmp")
            tmp.write_text(text, "utf-8")
            os.replace(tmp, self._page_file(cp, i))

    def _keeps_pictures(self) -> bool:
        return bool(self._store_image) and self._settings.pdf_show_pictures

    def _save_picture(self, image) -> str:
        """Store a picture (PIL image) and return the paragraph that stands for it."""
        import io

        from .models import picture_marker

        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG", optimize=True)
        return picture_marker(self._store_image(buf.getvalue()))

    def _figures(self, i: int):
        """Pictures and drawings on a PDF page that has text: ([(bbox, marker)], {marker: png}) for the layout reader. Pictures are
        only found (and their pixels only rendered) when they are to be kept or read."""
        from .models import picture_marker

        if not self._store_image or not (self._settings.pdf_show_pictures or self._settings.pdf_read_images):
            return None, {}
        figures, images = [], {}
        for bbox, png in self._loader.figure_regions(i):
            marker = picture_marker(self._store_image(png))
            images[marker] = png
            if self._settings.pdf_show_pictures:
                figures.append((bbox, marker))
        return (figures or None), images

    def _read_figure_images(self, blocks: list, images: dict) -> list:
        """"Read text inside pictures": OCR the pictures that had no text of their own, and put the words after the picture."""
        import io

        from PIL import Image as PILImage

        from .pdflayout import LayoutBlock

        out = []
        for b in blocks:
            out.append(b)
            if b.kind == "figure" and not getattr(b, "has_text", False) and b.text in images:
                text = finish_paragraph(clean_text(self._ocr.recognize_text(PILImage.open(io.BytesIO(images[b.text])))))
                if len(text) >= 3:
                    out.append(LayoutBlock(b.cx, b.cy, text, "image"))
        return out

    def _add_picture_text(self, worker: TaskWorker, i: int, pages: int, blocks: list) -> list:
        """Only when asked for: read the text inside the pictures of a PDF that has real text, and put it where the picture is."""
        from .pdflayout import LayoutBlock

        blocks = list(blocks)
        regions = self._loader.image_regions(i)
        for k, ((x0, y0, x1, y1), image) in enumerate(regions):
            if worker.cancelled:
                break
            self._report(worker, i + 1, pages, "ocr", k / max(len(regions), 1))
            text = finish_paragraph(clean_text(self._ocr.recognize_text(image)))
            if len(text) < 3:
                continue
            block = LayoutBlock((x0 + x1) / 2, (y0 + y1) / 2, text, "image")
            same_column = [n for n, b in enumerate(blocks) if abs(b.cx - block.cx) < 0.3 or abs(b.cx - 0.5) < 0.05]
            before = [n for n in same_column if blocks[n].cy < block.cy]
            after = [n for n in same_column if blocks[n].cy >= block.cy]
            at = before[-1] + 1 if before else (after[0] if after else len(blocks))
            blocks.insert(at, block)
        return blocks

    def _speak(self, text: str) -> str:
        """Formulas are written the way they are said (when that is switched on)."""
        return math_to_speech(text) if self._settings.read_math else text

    # -- one page
    def _extract_page(self, worker: TaskWorker, i: int, pages: int) -> str | None:
        """Text of page i (0-based), or None if cancelled."""
        self._report(worker, i + 1, pages, "layout", 0.0)
        if self._loader.kind == "pdf" and self._settings.ocr_engine == "auto" and self._loader.has_text_layer(i):
            try:  # layout-aware: columns, headings, footnotes, verse numbers
                figures, images = self._figures(i)
                blocks = self._loader.layout_blocks(i, self.layout_options(), figures=figures)
                if self._settings.pdf_read_images and images:
                    blocks = self._read_figure_images(blocks, images)
            except Exception:
                get_logger("extractor").error("layout reading failed on page %s; using plain text blocks", i + 1, exc_info=True)
                blocks = self._loader.text_blocks(i)
            if self._settings.pdf_read_images and not self._keeps_pictures():
                blocks = self._add_picture_text(worker, i, pages, blocks)
            blocks = self._regions.filter_blocks(blocks, i)
            self._report(worker, i + 1, pages, "ocr", 1.0)
            return self._speak("\n\n".join(t for t in (finish_paragraph(clean_text(ParagraphModel.clean(b.text))) for b in blocks) if t))
        zoom = self.OCR_ZOOM.get(self._settings.ocr_quality, 2.0)
        crops = self._regions.apply_to_image(self._loader.render_page(i, zoom=zoom), i)
        parts = []
        for k, crop in enumerate(crops):
            if worker.cancelled:
                return None
            self._report(worker, i + 1, pages, "ocr", k / len(crops))
            if self._keeps_pictures() and getattr(self._ocr, "supports_pictures", False):
                parts.append(self._ocr.recognize_with_pictures(crop, self._save_picture))
            else:
                parts.append(self._ocr.recognize_text(crop))
        return self._speak("\n\n".join(p for p in parts if p))

    # -- the whole document
    def extract(self, worker: TaskWorker) -> str:
        """Run inside a TaskWorker. Returns the whole document's text (paragraphs separated by blank lines)."""
        pages = self._loader.page_count
        self.failed_pages, self.resumed_pages = [], 0
        cp = self._open_checkpoint(pages)
        self.fresh = False
        texts: list[str] = []
        streak = 0
        for i in range(pages):
            if worker.cancelled:
                return ""  # saved pages stay on disk for next time
            saved = self._page_file(cp, i) if cp is not None else None
            if saved is not None and saved.exists():
                texts.append(saved.read_text("utf-8"))
                self.resumed_pages += 1
                worker.report(f"Page {i + 1} / {pages} — already done, resuming", int(100 * (i + 1) / pages))
                continue
            try:
                page_text = self._extract_page(worker, i, pages)
            except Exception:
                streak += 1
                if streak >= self.MAX_CONSECUTIVE_FAILURES:
                    raise  # e.g. OCR isn't installed: don't grind through hundreds of pages failing the same way
                self.failed_pages.append(i + 1)
                continue
            if page_text is None:
                return ""
            streak = 0
            self._save_page(cp, i, page_text)
            texts.append(page_text)
        if cp is not None and not self.failed_pages:
            shutil.rmtree(cp, ignore_errors=True)  # finished cleanly: the text is now kept by the library
        return "\n\n".join(t for t in stitch_pages([t for t in texts if t.strip()]) if t.strip())
