"""Background tasks with progress and cancel."""
from __future__ import annotations

import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from .errors import get_logger


class TaskWorker(QThread):
    """Runs `fn(worker)` off the GUI thread. `fn` calls worker.report(...) and checks worker.cancelled."""

    progress = pyqtSignal(str, int)  # text, percent (-1 = busy)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def cancel(self) -> None:
        self._cancel = True

    def report(self, text: str, percent: int = -1) -> None:
        self.progress.emit(text, percent)

    def run(self) -> None:
        try:
            result = self._fn(self)
        except Exception as exc:
            traceback.print_exc()
            get_logger("workers").error("background task failed", exc_info=True)
            self.failed.emit(self.friendly(exc))
            return
        if not self._cancel:
            self.succeeded.emit(result)

    # Optional language add-ons of the speech front-end (kokorog2p): missing module -> (language, what to run)
    LANGUAGE_ADDONS = {
        "jieba": ("Chinese", 'pip install "kokorog2p[zh]"'),
        "pypinyin": ("Chinese", 'pip install "kokorog2p[zh]"'),
        "pypinyin_dict": ("Chinese", 'pip install "kokorog2p[zh]"'),
        "ordered_set": ("Chinese", 'pip install "kokorog2p[zh]"'),
        "pyopenjtalk": ("Japanese", "pip install pyopenjtalk-plus"),
    }

    @classmethod
    def _missing_module(cls, exc: BaseException) -> str | None:
        """Name of the module an import failed on, also when it was wrapped (`raise ImportError(...) from ModuleNotFoundError`)."""
        seen = 0
        while exc is not None and seen < 4:
            if isinstance(exc, ModuleNotFoundError) and exc.name:
                return exc.name.split(".")[0]
            exc, seen = exc.__cause__ or exc.__context__, seen + 1
        return None

    @classmethod
    def friendly(cls, exc: Exception) -> str:
        missing = cls._missing_module(exc)
        if missing is None and isinstance(exc, ImportError) and "openjtalk" in str(exc).lower():
            missing = "pyopenjtalk"
        if missing in cls.LANGUAGE_ADDONS:
            language, command = cls.LANGUAGE_ADDONS[missing]
            note = ("\n\n(The command kokorog2p itself suggests, pip install \"kokorog2p[ja]\", compiles a C++ library and needs the "
                    "Visual C++ Build Tools on Windows. pyopenjtalk-plus is a prebuilt version of the same package.)") if language == "Japanese" else ""
            return (f"This voice needs the {language} speech add-on, which isn't installed. English voices don't need it.\n\n"
                    f"Fix: with EchoRead's virtual environment active, run\n{command}\nthen restart EchoRead.{note}")
        if isinstance(exc, ModuleNotFoundError) and exc.name:
            root = exc.name.split(".")[0]
            hints = {
                "paddleocr": "pip install paddlepaddle paddleocr",
                "paddle": "pip install paddlepaddle",
                "pykokoro": 'pip install "pykokoro[cpu]"',
                "sounddevice": "pip install sounddevice",
                "requests": "pip install requests readability-lxml",
                "readability": "pip install readability-lxml",
            }
            return f"A required package is missing: {root}\n\nInstall it with:\n{hints.get(root, 'pip install ' + root)}"
        if isinstance(exc, KeyError) and "not found. available voices" in str(exc).lower():
            import re

            m = re.search(r"Voice '([^']+)' not found", str(exc))
            return (f"The speech model doesn't have the voice '{m.group(1) if m else '?'}'. Pick another voice for this language "
                    "on the Voices page.")
        msg = str(exc) or exc.__class__.__name__
        low = msg.lower()
        if "convertpirattribute" in low or "onednn" in low:
            msg += ("\n\nThis is a PaddlePaddle CPU-engine problem on some processors. EchoRead already retried with oneDNN "
                    "and Paddle's new executor switched off. What still works: a GPU build of PaddlePaddle (it doesn't use "
                    "this code path), a different PaddlePaddle version, or opening a text-layer copy of the file.")
        if "espeak" in msg.lower():
            msg += "\n\nFix: run   pip install espeakng-loader   then restart EchoRead."
        return msg