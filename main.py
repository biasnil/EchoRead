"""EchoRead entry point. Wires the objects together and hands control to MainWindow."""
from __future__ import annotations

import os
import sys

from core.frozen import prepare_runtime

prepare_runtime()  # log file + crash box + DLL folders for a frozen build; does nothing when run from source

from PyQt6.QtCore import QLoggingCategory, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.services import Services  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import build_stylesheet  # noqa: E402


def quiet_font_warnings() -> None:
    """Windows lists old raster fonts (8514oem, Fixedsys, Modern) that DirectWrite can't open, and Qt warns about scripts a font
    lacks OpenType tables for. Both are harmless but flood the console. Set QT_LOGGING_RULES yourself to see them again."""
    if not os.environ.get("QT_LOGGING_RULES"):
        QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false\nqt.text.font.db.warning=false")


def main() -> int:
    if "--selftest" in sys.argv:  # checks a build for missing metadata/DLLs/data; see core/selftest.py
        from core.selftest import run

        return run()
    quiet_font_warnings()
    app = QApplication(sys.argv)
    app.setApplicationName("EchoRead")
    services = Services.build()
    services.errors.install()  # log file + friendly dialog for unhandled exceptions
    app.setStyleSheet(build_stylesheet(services.settings.theme))
    window = MainWindow(services)
    window.show()
    QTimer.singleShot(0, window.run_first_time_setup)  # the welcome dialog, first launch only
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())