"""Logging and the global exception hook.

Every problem goes to `%APPDATA%\\EchoRead\\logs\\echoread.log` (rotating: 5 MB x 3 files) with time, level, module and traceback.
Unhandled exceptions (in the GUI thread or a Python thread) are logged and reported through `error_occurred`, which the main
window turns into a friendly dialog. Before a window is attached, or for SystemExit/KeyboardInterrupt, the previous hook runs.
"""
from __future__ import annotations

import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from PyQt6.QtCore import QObject, pyqtSignal

from .paths import AppPaths

LOGGER_NAME = "echoread"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
SUMMARY = "Something went wrong while processing this file."


def get_logger(module: str) -> logging.Logger:
    """A logger whose name shows up in the log line as the module, e.g. get_logger('voice_cache')."""
    return logging.getLogger(f"{LOGGER_NAME}.{module}")


class ErrorReporter(QObject):
    error_occurred = pyqtSignal(str, str)  # short message, full details (traceback)

    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._handler: logging.Handler | None = None
        self._previous_hook = None
        self._previous_thread_hook = None
        self._ui_attached = False
        self._busy = False
        self.setup_logging()

    # ------------------------------------------------------------------ logging
    @property
    def log_path(self):
        return self._paths.log_file

    def setup_logging(self) -> None:
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for old in [h for h in logger.handlers if getattr(h, "_echoread", False)]:
            logger.removeHandler(old)
            old.close()
        try:
            self._paths.log_dir.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = RotatingFileHandler(self.log_path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                           encoding="utf-8", delay=True)
        except OSError:
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        handler._echoread = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        self._handler = handler

    def close(self) -> None:
        """Detach from the global logger and the exception hooks (tests, or a clean exit)."""
        self.uninstall()
        logger = logging.getLogger(LOGGER_NAME)
        if self._handler is not None:
            logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    # ------------------------------------------------------------------ hooks
    def install(self) -> None:
        if self._previous_hook is None:
            self._previous_hook = sys.excepthook
            sys.excepthook = self._on_exception
        if self._previous_thread_hook is None:
            self._previous_thread_hook = threading.excepthook
            threading.excepthook = self._on_thread_exception

    def uninstall(self) -> None:
        if self._previous_hook is not None and sys.excepthook == self._on_exception:
            sys.excepthook = self._previous_hook
        if self._previous_thread_hook is not None and threading.excepthook == self._on_thread_exception:
            threading.excepthook = self._previous_thread_hook
        self._previous_hook = self._previous_thread_hook = None

    def attach_ui(self, attached: bool = True) -> None:
        """Once a window listens to `error_occurred`, errors become dialogs instead of going to the old hook."""
        self._ui_attached = attached

    @staticmethod
    def format_exception(exc_type, exc, tb) -> str:
        return "".join(traceback.format_exception(exc_type, exc, tb))

    def _on_exception(self, exc_type, exc, tb) -> None:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            if self._previous_hook is not None:
                self._previous_hook(exc_type, exc, tb)
            return
        self.report(exc_type, exc, tb, where="unhandled exception")

    def _on_thread_exception(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        thread = getattr(args.thread, "name", "thread")
        self.report(args.exc_type, args.exc_value, args.exc_traceback, where=f"thread {thread}")

    def report(self, exc_type, exc, tb, where: str = "") -> None:
        """Log an exception; show it to the user when a window is listening (never raises)."""
        if self._busy:
            return
        self._busy = True
        try:
            details = self.format_exception(exc_type, exc, tb)
            get_logger("errors").error("%s\n%s", where or "error", details)
            if self._ui_attached:
                try:
                    self.error_occurred.emit(SUMMARY, details)
                except RuntimeError:  # the Qt object is already being destroyed
                    pass
            elif self._previous_hook is not None:
                self._previous_hook(exc_type, exc, tb)
            else:
                sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
        finally:
            self._busy = False

    # ------------------------------------------------------------------ explicit logging helpers
    @staticmethod
    def warn(module: str, message: str) -> None:
        get_logger(module).warning(message)

    @staticmethod
    def error(module: str, message: str, exc: BaseException | None = None) -> None:
        get_logger(module).error(message, exc_info=exc)
