"""`EchoRead.exe --selftest`: the frozen app checks itself, so a bad build shows up in seconds instead of mid-use.

Every check is small on purpose ("reproduce small"): each proves one thing a PyInstaller build can silently lose,
namely package metadata, native libraries loaded by path, data files, and lazily imported modules. Writes
selftest_report.txt next to the exe and exits 0 when nothing critical failed.
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from .frozen import is_frozen, resource_root

CRITICAL, WARN = "FAIL", "warn"


class SelfTest:
    def __init__(self):
        self.lines: list[str] = []
        self.failures = 0
        self.warnings = 0
        self.flavor = "?"

    # -- plumbing
    def check(self, name: str, fn, severity: str = CRITICAL) -> None:
        try:
            detail = fn()
            self.lines.append(f"  ok    {name}" + (f"  —  {detail}" if detail else ""))
        except Exception as exc:
            first = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0][:240]
            self.lines.append(f"  {severity:<5} {name}  —  {exc.__class__.__name__}: {first}")
            if severity == CRITICAL:
                self.failures += 1
            else:
                self.warnings += 1
            if os.environ.get("ECHOREAD_SELFTEST_TRACE"):
                self.lines.append(traceback.format_exc())

    def section(self, title: str) -> None:
        self.lines += ["", title]

    # -- the checks
    def run(self) -> int:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        info = resource_root() / "build_info.json"
        if info.exists():
            self.flavor = json.loads(info.read_text("utf-8")).get("flavor", "?")
        self.lines.append(f"EchoRead self-test   flavor={self.flavor}   frozen={is_frozen()}   python={sys.version.split()[0]}")
        self.lines.append(f"resource root: {resource_root()}")

        self.section("Our own code and bundled files")
        self.check("import EchoRead modules (hidden imports)", self._own_modules)
        self.check("assets, icon and stylesheet", self._assets)
        self.check("services can be built", self._services)
        self.check("SVG icons render (QtSvg is in the build)", self._icons)
        self.check("bundled reader fonts load and text lays out", self._fonts)
        self.check("log file is written (rotating handler) and errors are reported", self._logging)
        self.check("word timeline and highlight store", self._highlight_logic)
        self.check("PDF layout reader puts two columns in reading order", self._pdf_layout)
        self.check("tables in a PDF are found and read row by row (PyMuPDF's table finder is in the build)", self._pdf_tables, WARN)
        self.check("formulas are written the way they are said", self._math)
        self.check("drawings and pictures in a PDF are found and kept as pictures", self._pictures)
        self.check("EPUB and Word files can be read (lxml is in the build)", self._book_formats)

        self.section("Package metadata (PaddleX reads dist-info; PyInstaller drops it unless told to)")
        self.check("dist-info for the key packages", self._metadata)

        self.section("Qt and documents")
        self.check("Qt starts (offscreen) and the Windows platform plugin is bundled", self._qt)
        self.check("PyMuPDF renders a page", self._pymupdf)
        self.check("soundfile writes/reads FLAC and MP3 (libsndfile bundled)", self._soundfile)
        # Windows wheels bundle PortAudio, so it must load there; Linux/macOS use the system library
        self.check("sounddevice / PortAudio loads", self._sounddevice_import, CRITICAL if os.name == "nt" else WARN)
        self.check("an audio output device exists", self._audio_device, WARN)
        self.check("web extraction (readability + lxml)", self._web, WARN)
        self.check("pypdfium2 (fallback for damaged PDFs) renders a page", self._pdfium, WARN)

        self.section("Speech (Kokoro)")
        self.check("pykokoro + kokorog2p import", self._kokoro_import)
        self.check("Kokoro's text front-end (numbers, abbreviations: loads its language modules by name)", self._text_frontend)
        self.check("every module the spec collected by name is inside the build", self._collected_modules)
        self.check("Chinese / Japanese voice add-ons (only those voices need them)", self._language_addons, WARN)
        self.check("eSpeak NG library and data files bundled", self._espeak)
        self.check("ONNX Runtime and its providers", self._onnx)
        self.check("ONNX Runtime really runs on the GPU", self._onnx_gpu_session, WARN)

        self.section("OCR (PaddleOCR)")
        self.check("paddle imports; CPU/GPU build matches this exe", self._paddle)
        self.check("PaddleX finds its optional dependencies (the classic 'requires additional dependencies' error)", self._paddlex_deps)
        self.check("PaddleOCR imports", self._paddleocr_import)
        self.check("Smart layout (PP-StructureV3) can be imported; its models download on first use", self._smart_import, WARN)

        verdict = "PASSED" if self.failures == 0 else f"FAILED ({self.failures} critical)"
        self.lines += ["", f"RESULT: {verdict}   ({self.warnings} warning{'s' if self.warnings != 1 else ''})"]
        self._write()
        return 0 if self.failures == 0 else 1

    # ---------------- individual checks (each raises on failure)
    def _own_modules(self):
        for name in ("core.services", "core.playback", "core.exporter", "core.extractor", "core.sections", "core.gpu",
                     "ui.main_window", "ui.reader_view", "ui.settings_dialog", "ui.regions_dialog", "ui.theme",
                     "core.errors", "core.highlights", "core.profile", "core.wordtiming", "core.pdflayout", "core.formats", "ui.widgets", "ui.onboarding",
                     "ui.highlight_palette", "ui.error_dialog", "ui.toast"):
            importlib.import_module(name)
        return "all modules import"

    def _assets(self):
        from core.paths import AppPaths
        from ui.theme import build_stylesheet

        from PyQt6.QtGui import QImage

        assets = AppPaths.assets_dir()
        for f in ("icon.ico", "icons/icon.png", "icons/chevron_dark.png", "icons/chevron_light.png"):
            if not (assets / f).exists():
                raise FileNotFoundError(f"missing asset {assets / f}")
        for f in ("icon.png", "chevron_dark.png", "chevron_light.png"):  # a white box behind them is the classic mistake
            img = QImage(str(assets / "icons" / f))
            if img.isNull() or not img.hasAlphaChannel() or img.pixelColor(0, 0).alpha() != 0:
                raise RuntimeError(f"assets/icons/{f} must have a transparent background (corner pixel is not transparent)")
        if "chevron_dark.png" not in build_stylesheet("dark"):
            raise RuntimeError("stylesheet has no chevron")
        return str(assets)

    ICONS = ("play", "pause", "resume", "previous", "next", "stop", "bookmark", "bookmark_filled", "volume", "volume_muted")

    def _icons(self):
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication
        from ui.theme import icons_dir, svg_pixmap

        app = QApplication.instance() or QApplication([])  # keep a reference: Qt aborts if the app object is collected
        for name in self.ICONS:
            if not (icons_dir() / f"{name}.svg").exists():
                raise FileNotFoundError(f"missing icon assets/icons/{name}.svg")
            image = svg_pixmap(name, "#FFFFFF", 24, 1.0).toImage().convertToFormat(QImage.Format.Format_ARGB32)
            if not any(image.pixelColor(x, y).alpha() > 0 for x in range(24) for y in range(24)):
                raise RuntimeError(f"{name}.svg rendered blank (QtSvg missing or the file is broken)")
        return f"{len(self.ICONS)} icons"

    def _fonts(self):
        from PyQt6.QtWidgets import QApplication
        from ui.theme import FontLibrary
        from ui.widgets import ParagraphText

        app = QApplication.instance() or QApplication([])  # keep a reference: Qt aborts if the app object is collected
        lib = FontLibrary()
        loaded = lib.load()
        wanted = {"Inter 18pt", "Lora", "Source Code Pro", "Source Sans 3"}
        missing = wanted - set(loaded)
        if missing:
            raise RuntimeError("fonts not loaded from assets/fonts: " + ", ".join(sorted(missing)))
        w = ParagraphText("A short line of text to lay out.")
        w.set_font_spec(FontLibrary.make_font("Lora", 18), 1.65)
        if w.heightForWidth(300) <= 10:
            raise RuntimeError("text layout gave no height")
        return ", ".join(sorted(wanted))

    def _logging(self):
        from core.errors import ErrorReporter, get_logger
        from core.paths import AppPaths

        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths(Path(tmp) / "E")
            paths.ensure()
            reporter = ErrorReporter(paths)
            try:
                seen = []
                reporter.error_occurred.connect(lambda summary, details: seen.append(details))
                reporter.attach_ui(True)
                get_logger("selftest").error("selftest line")
                try:
                    raise ValueError("selftest boom")
                except ValueError:
                    reporter.report(*sys.exc_info(), where="selftest")
                for h in list(__import__("logging").getLogger("echoread").handlers):
                    h.flush()
                text = paths.log_file.read_text("utf-8")
                if "selftest boom" not in text or "selftest line" not in text:
                    raise RuntimeError("log file has no entries")
            finally:
                reporter.close()
        return "logs/echoread.log"

    def _pdf_layout(self):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        from core.loader import DocumentLoader
        from core.pdflayout import LayoutOptions

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "columns.pdf"
            doc = fitz.open()
            page = doc.new_page(width=400, height=500)
            for i in range(8):
                page.insert_text((40, 100 + 14 * i), f"left column line number {i} of text", fontsize=10)
                page.insert_text((215, 100 + 14 * i), f"right column line number {i} of text", fontsize=10)
            doc.save(str(path))
            doc.close()
            loader = DocumentLoader()
            try:
                loader.open(path)
                text = " ".join(b.text for b in loader.layout_blocks(0, LayoutOptions(verses="never")))
            finally:
                loader.close()
        if not text or text.index("left column line number 7") > text.index("right column line number 0"):
            raise RuntimeError("columns came out in the wrong order: " + text[:80])
        return "2 columns"

    def _pdf_tables(self):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        from core.loader import DocumentLoader
        from core.pdflayout import LayoutOptions

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.pdf"
            doc = fitz.open()
            page = doc.new_page(width=400, height=300)
            rows = [["Name", "Age"], ["Ana", "30"], ["Ben", "25"]]
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    page.insert_text((56 + c * 100, 96 + r * 24), cell, fontsize=10)
            for r in range(4):
                page.draw_line((50, 80 + r * 24), (250, 80 + r * 24))
            for c in range(3):
                page.draw_line((50 + c * 100, 80), (50 + c * 100, 152))
            doc.save(str(path))
            doc.close()
            loader = DocumentLoader()
            try:
                loader.open(path)
                text = " ".join(b.text for b in loader.layout_blocks(0, LayoutOptions(verses="never")))
            finally:
                loader.close()
        if "Name: Ana. Age: 30." not in text:
            raise RuntimeError("the table was not recognised: " + text[:80])
        return "1 table"

    def _book_formats(self):
        import zipfile

        from core.formats import read_docx, read_epub

        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "a.docx"
            with zipfile.ZipFile(docx, "w") as z:
                z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Hello Word</w:t></w:r></w:p></w:body></w:document>')
            epub = Path(tmp) / "a.epub"
            with zipfile.ZipFile(epub, "w") as z:
                z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                           '<rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
                z.writestr("c.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                           '</manifest><spine><itemref idref="a"/></spine></package>')
                z.writestr("a.xhtml", "<html><body><p>Hello EPUB</p></body></html>")
            if read_docx(docx)[1] != "Hello Word" or read_epub(epub)[1] != "Hello EPUB":
                raise RuntimeError("the test books were not read back correctly")
        return "epub + docx"

    def _pictures(self):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        from PIL import Image

        from core.loader import DocumentLoader
        from core.models import picture_marker
        from core.pdflayout import LayoutOptions

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "figure.pdf"
            doc = fitz.open()
            page = doc.new_page(width=400, height=500)
            page.insert_text((40, 60), "A sentence before the figure with several words.", fontsize=10)
            for k in range(6):  # a drawing: six coloured blocks close together, one label
                page.draw_rect(fitz.Rect(60 + 40 * k, 150, 90 + 40 * k, 200 + 10 * k), color=None, fill=(0.2, 0.5, 0.2 + 0.1 * k))
            page.insert_text((150, 260), "Label", fontsize=9)
            page.insert_text((40, 420), "A sentence after the figure with several words.", fontsize=10)
            doc.save(str(path))
            doc.close()
            loader = DocumentLoader()
            try:
                loader.open(path)
                figures = loader.figure_regions(0)
                blocks = loader.layout_blocks(0, LayoutOptions(verses="never"), figures=[(b, picture_marker("x.png")) for b, _png in figures])
            finally:
                loader.close()
        if len(figures) != 1 or Image.open(__import__("io").BytesIO(figures[0][1])).size[0] < 100:
            raise RuntimeError(f"expected one figure, found {len(figures)}")
        kinds = [b.kind for b in blocks]
        if kinds != ["body", "figure", "body"]:
            raise RuntimeError("the figure is not between the two sentences: " + str(kinds))
        return "1 figure kept between 2 paragraphs"

    def _math(self):
        from core.paragraphs import math_to_speech

        said = math_to_speech("x² + y² = z²")
        if said != "x squared plus y squared equals z squared":
            raise RuntimeError(said)
        return said

    def _highlight_logic(self):
        from core.wordtiming import WordTimeline

        tl = WordTimeline("Hello there, world. Bye now.")
        if len(tl.words) != 5 or len(tl.sentences) != 2:
            raise RuntimeError("word/sentence split is wrong")
        return "ok"

    def _pdfium(self):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument.new()
        pdf.new_page(200, 200)
        pdf[0].render(scale=1).to_pil()
        pdf.close()
        return f"pypdfium2 {pdfium.version.PYPDFIUM_INFO}"

    def _services(self):
        from core.paths import AppPaths
        from core.services import Services

        with tempfile.TemporaryDirectory() as tmp:
            Services.build(AppPaths(Path(tmp) / "E"))

    def _metadata(self):
        gpu = self.flavor == "gpu"
        wanted = ["paddleocr", "paddlex", "pykokoro", "kokorog2p", "PyQt6", "pymupdf", "numpy", "soundfile",
                  "paddlepaddle-gpu" if gpu else "paddlepaddle", "onnxruntime-gpu" if gpu else "onnxruntime"]
        found, missing = [], []
        for name in wanted:
            try:
                found.append(f"{name} {md.version(name)}")
            except md.PackageNotFoundError:
                missing.append(name)
        if missing:
            raise RuntimeError("no dist-info bundled for: " + ", ".join(missing))
        return "; ".join(found)

    def _qt(self):
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication, QLabel

        app = QApplication.instance() or QApplication([])
        QLabel("x").show()
        QImage(10, 10, QImage.Format.Format_RGB32)
        if is_frozen() and os.name == "nt":
            plugin = next(iter(resource_root().rglob("qwindows.dll")), None)
            if plugin is None:
                raise FileNotFoundError("qwindows.dll (Qt's Windows platform plugin) is not in the build")
        return f"Qt {app.applicationVersion() or 'ok'}"

    def _pymupdf(self):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "hello")
        pix = page.get_pixmap()
        return f"{pix.width}x{pix.height}"

    def _soundfile(self):
        import numpy as np
        import soundfile as sf

        data = (np.sin(np.linspace(0, 200, 24000)) * 0.1).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            for fmt, ext in (("FLAC", "flac"), ("MP3", "mp3")):
                path = Path(tmp) / f"t.{ext}"
                sf.write(str(path), data, 24000, format=fmt)
                back, sr = sf.read(str(path))
                if sr != 24000 or len(back) < 20000:
                    raise RuntimeError(f"{fmt} round trip returned {len(back)} samples at {sr} Hz")
        return f"libsndfile {sf.__libsndfile_version__}"

    def _sounddevice_import(self):
        import sounddevice as sd

        return f"sounddevice {sd.__version__}"

    def _audio_device(self):
        import sounddevice as sd

        device = sd.query_devices(kind="output")
        return str(device["name"])

    def _web(self):
        from core.web import WebExtractor

        html = "<html><head><title>T</title></head><body><article><h1>Heading</h1>" + \
               "<p>" + "A real sentence for the extractor. " * 6 + "</p></article></body></html>"
        title, text = WebExtractor.parse_html(html, "x")
        if "real sentence" not in text:
            raise RuntimeError("extraction returned no text")
        return title

    def _kokoro_import(self):
        import kokorog2p  # noqa: F401
        import pykokoro  # noqa: F401

        return f"pykokoro {md.version('pykokoro')}"

    def _text_frontend(self):
        """This is the code path that broke real playback once: numeralform imports its Icelandic renderer with
        importlib.import_module(".is") ('is' is a keyword, so no normal import), invisible to PyInstaller's scan."""
        import abbr2words  # noqa: F401
        import spokenform  # noqa: F401
        from numeralform.renderers import IcelandicRenderer  # noqa: F401
        import numeralform.renderers as renderers

        return f"{len([n for n in dir(renderers) if n.endswith('Renderer')])} renderers loaded"

    def _collected_modules(self):
        info = resource_root() / "build_info.json"
        modules = json.loads(info.read_text("utf-8")).get("modules", []) if info.exists() else []
        if not modules:
            return "no manifest (running from source)"
        expected = set(modules)
        missing, skipped, ok = [], 0, 0
        saved_argv = sys.argv
        sys.argv = sys.argv[:1]  # some library modules are command-line tools that read sys.argv when imported
        try:
            for name in modules:
                if name.rsplit(".", 1)[-1] == "__main__":
                    continue  # entry points, not library code: importing one would RUN it
                try:
                    importlib.import_module(name)
                    ok += 1
                except ModuleNotFoundError as exc:
                    if exc.name in expected:  # a module we asked PyInstaller to include is not there
                        missing.append(exc.name)
                    else:  # needs an optional third-party package that isn't installed
                        skipped += 1
                except BaseException:  # incl. SystemExit from a CLI-style module: fine in principle, just not importable here
                    skipped += 1
        finally:
            sys.argv = saved_argv
        if missing:
            shown = ", ".join(sorted(set(missing))[:8])
            raise RuntimeError(f"{len(set(missing))} collected module(s) missing from the build: {shown}")
        return f"{ok} imported, {skipped} skipped (optional dependencies)"

    def _language_addons(self):
        """Japanese and Chinese voices need extra packages. Missing ones only matter to someone who picks those voices."""
        missing, present = [], []
        for label, modules, fix in (("Chinese", ("jieba", "pypinyin", "pypinyin_dict"), 'pip install "kokorog2p[zh]"'),
                                    ("Japanese", ("pyopenjtalk",), "pip install pyopenjtalk-plus")):
            try:
                for name in modules:
                    importlib.import_module(name)
                present.append(label)
            except ImportError as exc:
                missing.append(f"{label} ({exc.name or exc}: {fix})")
        if missing:
            raise RuntimeError("not available: " + "; ".join(missing))
        return " and ".join(present)

    def _espeak(self):
        import espeakng_loader

        lib, data = Path(espeakng_loader.get_library_path()), Path(espeakng_loader.get_data_path())
        if not lib.exists():
            raise FileNotFoundError(f"eSpeak library missing: {lib}")
        if not data.is_dir() or not any(data.iterdir()):
            raise FileNotFoundError(f"eSpeak data folder missing or empty: {data}")
        return lib.name

    def _onnx(self):
        import onnxruntime as ort

        providers = ort.get_available_providers()
        if self.flavor == "gpu" and "CUDAExecutionProvider" not in providers:
            raise RuntimeError(f"this is the GPU build but ONNX Runtime lists {providers}: the CPU onnxruntime was bundled")
        if self.flavor == "cpu" and "CUDAExecutionProvider" in providers:
            raise RuntimeError("this is the CPU build but onnxruntime-gpu was bundled")
        return f"{ort.__version__} {providers}"

    def _onnx_gpu_session(self):
        if self.flavor != "gpu":
            return "skipped (CPU build)"
        from .gpu import CudaLibraries

        cuda = CudaLibraries()
        cuda.prepare()
        ok, providers, error = cuda.check_session()
        if not ok:
            missing = [n for n, f in cuda.found.items() if not f]
            raise RuntimeError((error or f"session fell back to {providers}") + (f" | not found: {missing}" if missing else ""))
        return str(providers)

    def _paddle(self):
        import paddle

        gpu_build = bool(paddle.device.is_compiled_with_cuda())
        if self.flavor == "gpu" and not gpu_build:
            raise RuntimeError("this is the GPU build but paddle is the CPU build")
        if self.flavor == "cpu" and gpu_build:
            raise RuntimeError("this is the CPU build but paddle-gpu was bundled")
        return f"paddle {paddle.__version__} ({'GPU' if gpu_build else 'CPU'} build)"

    def _paddlex_deps(self):
        from paddlex.utils.deps import is_extra_available

        results = {extra: is_extra_available(extra) for extra in ("ocr-core",)}
        bad = [extra for extra, ok in results.items() if not ok]
        if bad:
            raise RuntimeError(f"PaddleX reports missing extras {bad}: package metadata (dist-info) was not bundled")
        return str(results)

    def _smart_import(self):
        from paddleocr import PPStructureV3

        return PPStructureV3.__name__

    def _paddleocr_import(self):
        from paddleocr import PaddleOCR  # noqa: F401

        return f"paddleocr {md.version('paddleocr')}"

    # -- output
    def _write(self) -> None:
        text = "\n".join(self.lines) + "\n"
        target = Path(sys.executable).parent if is_frozen() else Path.cwd()
        try:
            (target / "selftest_report.txt").write_text(text, "utf-8")
        except OSError:
            pass
        try:
            print(text)
        except Exception:
            pass


def run() -> int:
    return SelfTest().run()
