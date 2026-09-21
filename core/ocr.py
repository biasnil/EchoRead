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

from .errors import get_logger
from .models import is_picture, picture_marker

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
        self._structure_factory = self._make_structure
        self._engine = None
        self._mode = "v3"
        self._level = 0
        self._structure = None  # the Smart layout pipeline (PP-StructureV3), built on first use
        self._smart_broken = False  # it failed once this session: the standard OCR is used instead, without retrying every page
        self._lock = threading.Lock()
        # Paddle 3.x's CPU oneDNN (MKL-DNN) backend crashes on some processors with
        # "ConvertPirAttribute2RuntimeAttribute not support". PaddleX reads this switch when it is first imported,
        # so it has to be set here, before anything imports paddle. Set the variables yourself to override.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        # Routine console noise from Paddle (errors still print). Set these yourself to see everything again.
        os.environ.setdefault("GLOG_minloglevel", "2")  # its C++ "Logging before InitGoogleLogging…" and device notes
        self._silence_ccache_search()
        settings.changed.connect(self._on_setting_changed)

    def _on_setting_changed(self, key: str) -> None:
        if key == "ocr_quality":  # the detector's size limit is part of the engine: build it again on next use
            with self._lock:
                self._engine = None
        elif key == "ocr_smart":  # a new choice deserves a new try
            self._smart_broken = False

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
        sharp = dict(text_det_limit_side_len=2560, text_det_limit_type="max")  # don't shrink a big page render before detecting
        try:  # PaddleOCR 3.x
            self._mode = "v3"
            if self._settings.ocr_quality == "sharp":
                try:
                    return PaddleOCR(**base, **extras, **sharp)
                except (TypeError, ValueError):  # this version doesn't know the size-limit arguments: carry on without them
                    pass
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

    supports_pictures = True

    def recognize_with_pictures(self, image: Image.Image, save_picture) -> str:
        """Like recognize_text, but pictures on the page are kept: `save_picture(PIL image)` stores one and returns its
        marker paragraph. Text found inside a picture (its labels) is left out, unless "read text inside pictures" is on."""
        if self._settings.ocr_smart and not self._smart_broken:
            try:
                return self.recognize_smart(image, save_picture)
            except Exception:
                self._fail_smart()
        lines = self.recognize(image)
        regions = self.find_picture_regions(image, lines)
        if not regions:
            return self.lines_to_text(lines)
        labels_wanted = self._settings.pdf_read_images
        kept = list(lines)
        extra: list[OcrLine] = []
        for (x0, y0, x1, y1) in regions:
            inside = [l for l in kept if x0 <= (l.x0 + l.x1) / 2 <= x1 and y0 <= (l.y0 + l.y1) / 2 <= y1]
            kept = [l for l in kept if l not in inside]
            extra.append(OcrLine(save_picture(image.crop((x0, y0, x1, y1))), x0, y0, x1, y1))
            if labels_wanted and inside:
                inside.sort(key=lambda l: ((l.y0 + l.y1) / 2, l.x0))
                extra.append(OcrLine(self.join_pieces([l.text for l in inside]), x0, y1 + 1, x1, y1 + 1 + (inside[0].y1 - inside[0].y0)))
        return self.lines_to_text(kept + extra)

    def _fail_smart(self) -> None:
        import sys

        self._smart_broken = True
        get_logger("ocr").error("Smart layout failed; using standard OCR", exc_info=True)
        exc = sys.exc_info()[1]
        first = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0][:140] if exc else "unknown error"
        self.status.emit(f"Smart layout isn't available ({first}). Using standard OCR instead.")

    @staticmethod
    def find_picture_regions(image: Image.Image, lines: list[OcrLine], min_area: float = 0.03) -> list[tuple[int, int, int, int]]:
        """Pictures on a scanned page: big connected areas of ink (or colour) that are left when every recognised line of text is
        blanked out. Text-heavy regions (tables, dense paragraphs) and page-edge shadows are not pictures."""
        rgb = np.asarray(image.convert("RGB"))
        H, W = rgb.shape[:2]
        ink = (rgb.max(axis=2) < 205) | ((rgb.max(axis=2).astype(int) - rgb.min(axis=2)) > 45)
        for l in lines:  # blank the text (a little generously: descenders, anti-aliasing)
            ink[max(0, int(l.y0) - 3):int(l.y1) + 4, max(0, int(l.x0) - 3):int(l.x1) + 4] = False
        cell = max(4, round(W / 150))
        gh, gw = -(-H // cell), -(-W // cell)
        padded = np.zeros((gh * cell, gw * cell), dtype=bool)
        padded[:H, :W] = ink
        grid = padded.reshape(gh, cell, gw, cell).any(axis=(1, 3))
        near = grid.copy()  # bridge small gaps between the parts of a drawing
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                near |= np.roll(np.roll(grid, dy, axis=0), dx, axis=1)
        seen = np.zeros_like(near)
        found: list[list[int]] = []
        for sy in range(gh):
            for sx in range(gw):
                if not near[sy, sx] or seen[sy, sx]:
                    continue
                stack, seen[sy, sx] = [(sy, sx)], True
                y0c = y1c = sy
                x0c = x1c = sx
                while stack:
                    y, x = stack.pop()
                    y0c, y1c, x0c, x1c = min(y0c, y), max(y1c, y), min(x0c, x), max(x1c, x)
                    for ny in (y - 1, y, y + 1):
                        for nx in (x - 1, x, x + 1):
                            if 0 <= ny < gh and 0 <= nx < gw and near[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                stack.append((ny, nx))
                found.append([x0c * cell, y0c * cell, min(W, (x1c + 1) * cell), min(H, (y1c + 1) * cell)])
        out: list[list[int]] = []
        for box in found:
            w, h = box[2] - box[0], box[3] - box[1]
            area = w * h / (W * H)
            edges = (box[0] <= cell) + (box[1] <= cell) + (box[2] >= W - cell) + (box[3] >= H - cell)
            if area < min_area or w < 60 or h < 40 or area > 0.85 or edges >= 3 or max(w, h) > 15 * min(w, h):
                continue
            share = float(ink[box[1]:box[3], box[0]:box[2]].mean())
            if share < 0.01:
                continue
            out.append(box)
        # merge boxes that overlap or nearly touch, then drop the ones that are mostly text
        merged = True
        while merged:
            merged = False
            for a in range(len(out)):
                for b in range(a + 1, len(out)):
                    A, B = out[a], out[b]
                    if A[0] - 12 <= B[2] and B[0] - 12 <= A[2] and A[1] - 12 <= B[3] and B[1] - 12 <= A[3]:
                        out[a] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                        del out[b]
                        merged = True
                        break
                if merged:
                    break
        final = []
        for x0, y0, x1, y1 in out:
            x0, y0, x1, y1 = max(0, x0 - 6), max(0, y0 - 6), min(W, x1 + 6), min(H, y1 + 6)
            covered = sum((l.x1 - l.x0) * (l.y1 - l.y0) for l in lines if x0 <= (l.x0 + l.x1) / 2 <= x1 and y0 <= (l.y0 + l.y1) / 2 <= y1)
            if covered / max((x1 - x0) * (y1 - y0), 1) > 0.35:
                continue
            final.append((x0, y0, x1, y1))
        return sorted(final, key=lambda r: (r[1], r[0]))

    def recognize_text(self, image: Image.Image) -> str:
        if self._settings.ocr_smart and not self._smart_broken:
            try:
                return self.recognize_smart(image)
            except Exception:  # models missing, no internet on first use, an incompatible version...: the plain OCR still works
                self._fail_smart()
        return self.lines_to_text(self.recognize(image))

    # -- Smart layout (PaddleOCR's PP-StructureV3): layout regions with labels and a reading order
    SMART_FURNITURE = {"header", "header_image", "footer", "footer_image", "number", "aside_text"}
    SMART_SKIP = {"image", "chart", "seal", "figure", "formula", "formula_number", "algorithm"}

    def _make_structure(self):
        from paddleocr import PPStructureV3

        device = "gpu:0" if self._settings.device == "GPU" else "cpu"
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        self.status.emit("Loading Smart layout models (the first run downloads them)…")
        options = dict(device=device, use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                       use_seal_recognition=False, use_chart_recognition=False, use_formula_recognition=False,
                       use_table_recognition=True)
        try:
            return PPStructureV3(**options)
        except (TypeError, ValueError):  # this version doesn't know one of the switches
            return PPStructureV3(device=device)

    def recognize_smart(self, image: Image.Image, save_picture=None) -> str:
        with self._lock:
            if self._structure is None:
                self._structure = self._structure_factory()
            structure = self._structure
        arr = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])  # BGR, as for the standard engine
        paragraphs: list[str] = []
        for res in structure.predict(arr):
            paragraphs += self.blocks_to_paragraphs(self._parsing_list(res), self._settings, image, save_picture)
        return "\n\n".join(p for p in paragraphs if p)

    @staticmethod
    def _get(obj, key, default=None):
        try:
            return obj[key]
        except Exception:
            return getattr(obj, key, default)

    @classmethod
    def _parsing_list(cls, res) -> list:
        items = cls._get(res, "parsing_res_list")
        if items is None:
            inner = cls._get(cls._get(res, "json", {}) or {}, "res", {}) or {}
            items = cls._get(inner, "parsing_res_list", [])
        return list(items or [])

    @classmethod
    def blocks_to_paragraphs(cls, items, settings, image=None, save_picture=None) -> list[str]:
        """Layout regions (already in reading order) -> paragraphs, leaving out what the settings say to leave out."""
        from .formats import rows_to_sentences
        from .paragraphs import ParagraphModel
        from .pdflayout import clean_text, finish_paragraph

        out: list[str] = []
        for item in items:
            label = str(cls._get(item, "block_label", None) or cls._get(item, "label", "") or "").lower()
            content = str(cls._get(item, "block_content", None) or cls._get(item, "content", "") or "")
            if label in ("image", "chart", "figure") and save_picture is not None and image is not None:
                box = cls._get(item, "block_bbox", None) or cls._get(item, "bbox", None)
                try:
                    x0, y0, x1, y1 = [int(v) for v in box]
                    if x1 - x0 >= 40 and y1 - y0 >= 30:
                        out.append(save_picture(image.crop((max(0, x0), max(0, y0), min(image.width, x1), min(image.height, y1)))))
                except (TypeError, ValueError):
                    pass
                continue
            if not content.strip() or label in cls.SMART_SKIP:
                continue
            if label in cls.SMART_FURNITURE and not settings.pdf_furniture:
                continue
            if label == "footnote" and not settings.pdf_footnotes:
                continue
            if label in ("doc_title", "paragraph_title") and not settings.pdf_headings:
                continue
            if label == "table" or content.lstrip().lower().startswith("<table"):
                if settings.pdf_tables:
                    try:
                        from lxml import html as lxml_html

                        rows = [[" ".join(td.itertext()) for td in tr.xpath("./th|./td")] for tr in lxml_html.fromstring(content).xpath(".//tr")]
                        out += rows_to_sentences(rows)
                        continue
                    except Exception:
                        pass
                content = re.sub(r"<[^>]+>", " ", content)
            text = finish_paragraph(clean_text(ParagraphModel.clean(content)))
            if text:
                out.append(text)
        return out

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
        """Text of a page, in reading order: columns are read one after the other (a full-width heading first), and inside
        each the lines are grouped into rows and the rows into paragraphs (blank-line separated)."""
        lines = [l for l in lines if l.text and l.text.strip()]
        if not lines:
            return ""
        groups = cls.column_groups(lines)
        if len(groups) == 1:
            return cls._rows_to_text(groups[0])
        from .pdflayout import stitch_pages

        return "\n\n".join(t for t in stitch_pages([cls._rows_to_text(g) for g in groups]) if t)

    @staticmethod
    def column_groups(lines: list[OcrLine]) -> list[list[OcrLine]]:
        """Split a page's lines into reading-order groups: [all lines] for one column, otherwise band by band
        (a run of full-width lines, then each column of the band from left to right)."""
        heights = sorted(l.y1 - l.y0 for l in lines)
        unit = heights[len(heights) // 2] or 1.0
        if len(lines) < 8:
            return [lines]
        left, right = min(l.x0 for l in lines), max(l.x1 for l in lines)
        tw = right - left
        narrow = sorted((l.x0, l.x1) for l in lines if (l.x1 - l.x0) < 0.62 * tw)
        if tw < 8 * unit or len(narrow) < 8:
            return [lines]
        merged: list[list[float]] = []
        for a, b in narrow:
            if merged and a <= merged[-1][1] + 0.3 * unit:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        gutters = []
        for (a0, b0), (a1, b1) in zip(merged, merged[1:]):
            centre = (a1 + b0) / 2
            n_left = sum(1 for x0, x1 in narrow if x1 <= b0 + 1)
            n_right = sum(1 for x0, x1 in narrow if x0 >= a1 - 1)
            if (a1 - b0) >= 0.45 * unit and left + 0.15 * tw <= centre <= right - 0.15 * tw and n_left >= 4 and n_right >= 4:
                gutters.append((b0, a1))
        if not gutters:
            return [lines]

        def column_of(l: OcrLine) -> int:
            centre = (l.x0 + l.x1) / 2
            return sum(1 for g0, g1 in gutters if centre > (g0 + g1) / 2)

        groups: list[list[OcrLine]] = []
        band: dict[int, list[OcrLine]] = {}
        run: list[OcrLine] = []

        def flush_band():
            for col in sorted(band):
                groups.append(band[col])
            band.clear()

        def flush_run():
            if run:
                groups.append(list(run))
                run.clear()

        for l in sorted(lines, key=lambda l: ((l.y0 + l.y1) / 2, l.x0)):
            if any(l.x0 < g0 - 0.15 * unit and l.x1 > g1 + 0.15 * unit for g0, g1 in gutters):  # crosses a gutter: full width
                flush_band()
                run.append(l)
            else:
                flush_run()
                band.setdefault(column_of(l), []).append(l)
        flush_run()
        flush_band()
        return groups

    @classmethod
    def _rows_to_text(cls, lines: list[OcrLine]) -> str:
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
            pic = is_picture(l.text)
            if rows and not pic and not rows[-1]["pic"] and abs(cy - rows[-1]["cy"]) < med_h * 0.5:
                rows[-1]["items"].append(l)
            else:
                rows.append({"cy": cy, "items": [l], "pic": pic})
        merged = []
        for r in rows:
            items = sorted(r["items"], key=lambda l: l.x0)
            merged.append({
                "text": cls.join_pieces([i.text for i in items]),
                "pic": r["pic"], "y0": min(i.y0 for i in items), "y1": max(i.y1 for i in items),
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
                prev_h = max(prev["y1"] - prev["y0"], 1.0)
                if r["pic"] or prev["pic"] or gap > 0.65 * prev_h or (prev_short and ends):
                    paras.append(cls.join_pieces(cur))
                    cur = []
            cur.append(r["text"])
        if cur:
            paras.append(cls.join_pieces(cur))
        return "\n\n".join(p for p in paras if p)
