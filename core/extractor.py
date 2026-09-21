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

from .loader import DocumentLoader
from .ocr import OCRService
from .paragraphs import ParagraphModel
from .regions import RegionMask
from .settings import SettingsManager
from .workers import TaskWorker


class TextExtractor:
    STAGES = ("layout", "ocr")
    WEIGHTS = {"layout": 20, "ocr": 50}
    LABELS = {"layout": "Detecting layout", "ocr": "Recognizing text"}
    MAX_CONSECUTIVE_FAILURES = 3  # this many pages in a row failing means something systemic: stop and say why

    def __init__(self, loader: DocumentLoader, ocr: OCRService, regions: RegionMask, settings: SettingsManager):
        self._loader = loader
        self._ocr = ocr
        self._regions = regions
        self._settings = settings
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
    def _signature(self) -> str:
        raw = json.dumps([self._regions.signature(), self._settings.ocr_engine, self._settings.ocr_lang])
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

    # -- one page
    def _extract_page(self, worker: TaskWorker, i: int, pages: int) -> str | None:
        """Text of page i (0-based), or None if cancelled."""
        self._report(worker, i + 1, pages, "layout", 0.0)
        if self._loader.kind == "pdf" and self._settings.ocr_engine == "auto" and self._loader.has_text_layer(i):
            blocks = self._regions.filter_blocks(self._loader.text_blocks(i), i)
            self._report(worker, i + 1, pages, "ocr", 1.0)
            return "\n\n".join(t for t in (ParagraphModel.clean(b.text) for b in blocks) if t)
        crops = self._regions.apply_to_image(self._loader.render_page(i), i)
        parts = []
        for k, crop in enumerate(crops):
            if worker.cancelled:
                return None
            self._report(worker, i + 1, pages, "ocr", k / len(crops))
            parts.append(self._ocr.recognize_text(crop))
        return "\n\n".join(p for p in parts if p)

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
        return "\n\n".join(t for t in texts if t.strip())
