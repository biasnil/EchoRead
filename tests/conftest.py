import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    from ui.theme import FontLibrary, build_stylesheet

    FontLibrary().load()
    app.setStyleSheet(build_stylesheet("dark"))
    return app


@pytest.fixture
def root(tmp_path):
    return tmp_path / "EchoRead"
