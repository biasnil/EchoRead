"""PaddleOCR wrapper: images in, text + bounding boxes out."""
from __future__ import annotations

import os
import re
import tempfile
import threading
import warnings
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal

from .models import OcrLine
from .settings import SettingsManager

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


class OCRService(QObject):
    status = pyqtSignal(str)  # e.g. "Loading OCR model…"

    MAX_LEVEL = 1  # 0: oneDNN off.  1: oneDNN off + Paddle's new-IR executor off

    def __init__(self, settings: SettingsManager, engine_factory=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._factory = engine_factory or self._make_paddle
        self._engine = None
        self._mode = "v3"
        self._level = 0
        self._lock = threading.Lock()
        # Paddle 3.x's CPU oneDNN (MKL-DNN) backend crashes on some processors with
        # "ConvertPirAttribute2RuntimeAttribute not support". PaddleX reads this switch when it is first imported,
        # so it has to be set here, before anything imports paddle. Set the variables yourself to override.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        # Routine console noise from Paddle (errors still print). Set these yourself to see everything again.
        os.environ.setdefault("GLOG_minloglevel", "2")  # its C++ "Logging before InitGoogleLogging…" and device notes
        self._silence_ccache_search()

    @staticmethod
    def _silence_ccache_search() -> None:
        """Paddle looks for `ccache` (a build cache used only when compiling C++ extensions, which EchoRead never does).
        When it isn't found Paddle prints a warning plus a raw `where` message. A placeholder file with that name on the
        PATH ends the search quietly, and the warning itself is filtered as well."""
        warnings.filterwarnings("ignore", message=".*ccache.*")
        try:
            folder = Path(tempfile.gettempdir()) / "echoread-ccache-stub"
            folder.mkdir(exist_ok=True)
            (folder / "ccache").touch(exist_ok=True)
            path = os.environ.get("PATH", "")
            if str(folder) not in path.split(os.pathsep):
                os.environ["PATH"] = path + os.pathsep + str(folder)  # last, so a real ccache would still win
        except OSError:
            pass

    # -- engine
    def _make_paddle(self):
        from paddleocr import PaddleOCR

        lang = self._settings.ocr_lang
        device = "gpu:0" if self._settings.device == "GPU" else "cpu"
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        base = dict(lang=lang, device=device, use_doc_orientation_classify=False,
                    use_doc_unwarping=False, use_textline_orientation=False)
        extras = dict(enable_mkldnn=False)  # oneDNN off (also off through the environment, see __init__)
        if self._level >= 1:  # last resort: also switch off the new-IR executor that contains the failing instruction
            extras["engine_config"] = {"paddle_static": {"run_mode": "paddle", "enable_new_ir": False, "enable_cinn": False}}
        try:  # PaddleOCR 3.x
            self._mode = "v3"
            try:
                return PaddleOCR(**base, **extras)
            except (TypeError, ValueError):  # this version doesn't know one of the extra arguments
                return PaddleOCR(**base)
        except (TypeError, ValueError):  # PaddleOCR 2.x
            self._mode = "v2"
            return PaddleOCR(lang=lang, use_angle_cls=True, use_gpu=(device != "cpu"), show_log=False, enable_mkldnn=False)

    def _ensure(self):
        with self._lock:
            if self._engine is None:
                self.status.emit("Loading OCR model (the first run downloads it)…")
                self._engine = self._factory()
            return self._engine

    # -- recognition
    @staticmethod
    def _is_onednn_failure(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in ("onednn", "mkldnn", "convertpirattribute"))

    def recognize(self, image: Image.Image) -> list[OcrLine]:
        arr = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])  # Paddle wants BGR
        try:
            return self._recognize(self._ensure(), arr)
        except Exception as exc:
            # Paddle's CPU engine can still trip on this on some machines: step up to a safer configuration
            # (once per level) and retry the page. If that also fails, the error is shown as it is.
            if not self._is_onednn_failure(exc) or self._level >= self.MAX_LEVEL:
                raise
            with self._lock:
                self._level += 1
                self._engine = None
            self.status.emit("Paddle's CPU engine failed on this page; retrying in compatibility mode…")
            return self._recognize(self._ensure(), arr)

    def _recognize(self, engine, arr) -> list[OcrLine]:
        lines: list[OcrLine] = []
        if self._mode == "v3":
            for res in engine.predict(arr):
                texts = res["rec_texts"]
                boxes = res.get("rec_boxes")
                if boxes is None or len(boxes) != len(texts):
                    boxes = []
                    for poly in res["rec_polys"]:
                        xs = [p[0] for p in poly]
                        ys = [p[1] for p in poly]
                        boxes.append([min(xs), min(ys), max(xs), max(ys)])
                for text, b in zip(texts, boxes):
                    lines.append(OcrLine(text, float(b[0]), float(b[1]), float(b[2]), float(b[3])))
        else:
            for page in engine.ocr(arr, cls=True) or []:
                for box, (text, _conf) in page or []:
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    lines.append(OcrLine(text, min(xs), min(ys), max(xs), max(ys)))
        return lines

    def recognize_text(self, image: Image.Image) -> str:
        return self.lines_to_text(self.recognize(image))

    # -- layout: OCR lines -> paragraphs
    @staticmethod
    def join_pieces(pieces: list[str]) -> str:
        out = ""
        for p in pieces:
            p = p.strip()
            if not p:
                continue
            if not out:
                out = p
            elif out.endswith("-") and p[:1].islower():
                out = out[:-1] + p
            elif CJK_RE.match(out[-1]) or CJK_RE.match(p[0]):
                out += p
            else:
                out += " " + p
        return out

    @classmethod
    def lines_to_text(cls, lines: list[OcrLine]) -> str:
        """Group lines into visual rows, then rows into paragraphs (blank-line separated)."""
        lines = [l for l in lines if l.text and l.text.strip()]
        if not lines:
            return ""
        heights = sorted(l.y1 - l.y0 for l in lines)
        med_h = heights[len(heights) // 2] or 1.0
        lines.sort(key=lambda l: (l.y0 + l.y1) / 2)
        rows: list[dict] = []
        for l in lines:
            cy = (l.y0 + l.y1) / 2
            if rows and abs(cy - rows[-1]["cy"]) < med_h * 0.5:
                rows[-1]["items"].append(l)
            else:
                rows.append({"cy": cy, "items": [l]})
        merged = []
        for r in rows:
            items = sorted(r["items"], key=lambda l: l.x0)
            merged.append({
                "text": cls.join_pieces([i.text for i in items]),
                "y0": min(i.y0 for i in items), "y1": max(i.y1 for i in items),
                "x0": min(i.x0 for i in items), "x1": max(i.x1 for i in items),
            })
        max_w = max(r["x1"] - r["x0"] for r in merged) or 1.0
        paras: list[str] = []
        cur: list[str] = []
        for k, r in enumerate(merged):
            if cur:
                prev = merged[k - 1]
                gap = r["y0"] - prev["y1"]
                prev_short = (prev["x1"] - prev["x0"]) < 0.7 * max_w
                ends = re.search(r"[.!?。！？…\"”')\]]$", prev["text"])
                if gap > 0.8 * med_h or (prev_short and ends):
                    paras.append(cls.join_pieces(cur))
                    cur = []
            cur.append(r["text"])
        if cur:
            paras.append(cls.join_pieces(cur))
        return "\n\n".join(p for p in paras if p)
