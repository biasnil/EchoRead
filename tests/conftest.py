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


@pytest.fixture(autouse=True)
def _stop_background_threads():
    """A voice-cache thread that is still writing while its Services is garbage-collected crashes Qt: stop and join them."""
    yield
    from tests import fakes

    for s in fakes.CREATED:
        try:
            s.playback.stop()
            s.cache.shutdown(wait=3)
            s.errors.close()
        except Exception:
            pass
    fakes.CREATED.clear()
    # windows left over from this test must be really gone before the next one starts: their timers and queued signals would
    # otherwise fire into half-torn-down objects
    from PyQt6.QtCore import QEvent

    app = QApplication.instance()
    if app is not None:
        for w in app.topLevelWidgets():
            try:
                w.close()
                w.deleteLater()
            except RuntimeError:
                pass
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "EchoRead"
