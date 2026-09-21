"""Export: the two-step window (Text/Audio, then one file or one per chapter) and what it produces."""
import time

import numpy as np
import pytest
import soundfile as sf
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QToolButton

from core.models import picture_marker
from core.sections import Section
from tests.fakes import SR, make_services
from tests.test_ui_v2 import wait_for
from ui.main_window import MainWindow
from ui.theme import build_stylesheet
from ui.widgets import ExportDialog

CHAPTER_A = ["Alpha one two three.", "Alpha four five six seven."]
CHAPTER_B = ["Beta one two.", "Beta three four five six seven eight."]
BOOK = CHAPTER_A + CHAPTER_B


class Worker:
    def __init__(self, cancel_after=None):
        self.cancelled = False
        self.reports = []
        self._cancel_after = cancel_after

    def report(self, text, pct=-1):
        self.reports.append(text)
        if self._cancel_after is not None and len(self.reports) >= self._cancel_after:
            self.cancelled = True


def split_book(s, strings=BOOK, sections=True):
    secs = [Section("Chapter A", 0, 2), Section("Chapter B", 2, 4)] if sections else None
    s.model.set_document(strings, sections=secs)
    s.state.speed = 1.0


def seconds(path):
    info = sf.info(str(path))
    return info.frames / info.samplerate


def paragraph_seconds(text, fake=0.12):
    return len(text.split()) * fake


# ================================================================== the exporter
def test_whole_book_is_one_audio_file_with_every_chapter_in_order(root, qapp, tmp_path):
    s = make_services(root)
    split_book(s)
    assert s.model.section_count == 2
    out = tmp_path / "book.wav"
    assert s.exporter.export_book_audio(Worker(), out, 1.0) == str(out)
    expected = sum(paragraph_seconds(t) + s.exporter.GAP_SECONDS for t in BOOK) + s.exporter.CHAPTER_GAP_SECONDS
    assert seconds(out) == pytest.approx(expected, abs=0.05)
    only_open = tmp_path / "open.wav"  # "only the chapter I'm on" is still available and is shorter
    s.exporter.export_audio(Worker(), only_open, 1.0)
    assert seconds(only_open) < seconds(out) - 3


def test_the_book_file_really_contains_chapter_a_then_chapter_b(root, qapp, tmp_path):
    s = make_services(root)
    seen = []
    real = s.tts.synthesize
    s.tts.synthesize = lambda text, voice, speed: seen.append(text) or real(text, voice, speed)
    split_book(s)
    s.exporter.export_book_audio(Worker(), tmp_path / "b.wav", 1.0)
    assert seen == BOOK


def test_a_document_without_chapters_exports_as_one_file_the_same_way(root, qapp, tmp_path):
    s = make_services(root)
    split_book(s, sections=False)
    assert s.model.section_count == 1
    out = tmp_path / "one.wav"
    s.exporter.export_book_audio(Worker(), out, 1.0)
    assert seconds(out) == pytest.approx(sum(paragraph_seconds(t) + s.exporter.GAP_SECONDS for t in BOOK), abs=0.05)


def test_pictures_skipped_and_ignored_paragraphs_are_not_read_into_the_book(root, qapp, tmp_path):
    s = make_services(root)
    seen = []
    real = s.tts.synthesize
    s.tts.synthesize = lambda text, voice, speed: seen.append(text) or real(text, voice, speed)
    strings = ["Alpha one.", picture_marker("x.png"), "Skipped words here.", "Beta one."]
    s.model.set_document(strings, sections=[Section("A", 0, 3), Section("B", 3, 4)])
    s.model.toggle_skip(2)
    s.exporter.export_book_audio(Worker(), tmp_path / "b.wav", 1.0)
    assert seen == ["Alpha one.", "Beta one."]


def test_stopping_a_book_export_leaves_no_half_file(root, qapp, tmp_path):
    s = make_services(root)
    split_book(s)
    out = tmp_path / "b.wav"
    assert s.exporter.export_book_audio(Worker(cancel_after=2), out, 1.0) == ""
    assert not out.exists() and not list(tmp_path.glob("*.part"))


def test_progress_says_which_chapter_it_is_on(root, qapp, tmp_path):
    s = make_services(root)
    split_book(s)
    w = Worker()
    s.exporter.export_book_audio(w, tmp_path / "b.wav", 1.0)
    assert "chapter 1 / 2" in w.reports[0] and "chapter 2 / 2" in w.reports[-1] and "paragraph 4 / 4" in w.reports[-1]


def test_each_chapter_as_its_own_text_file(root, qapp, tmp_path):
    s = make_services(root)
    strings = ["Alpha one.", picture_marker("x.png"), "Alpha two.", "Beta one."]
    s.model.set_document(strings, sections=[Section("Chapter: A?", 0, 3), Section("Chapter B", 3, 4)])
    folder = tmp_path / "chapters"
    s.exporter.export_chapter_texts(folder, ".txt")
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["0001 - Chapter A.txt", "0002 - Chapter B.txt"]  # numbered, and no characters Windows dislikes
    assert (folder / names[0]).read_text("utf-8") == "Alpha one.\n\nAlpha two.\n"  # the picture is not text
    md = tmp_path / "md"
    s.exporter.export_chapter_texts(md, ".md")
    assert next(md.iterdir()).read_text("utf-8").startswith("# Chapter: A?\n\nAlpha one.")


# ================================================================== the window
def open_dialog(chapters=2, title="Chapter B", qapp=None):
    d = ExportDialog(chapters, title)
    d.show()
    QApplication.processEvents()
    return d


def test_step_one_needs_a_choice_before_next(qapp):
    d = open_dialog()
    assert d._step.text() == "Step 1 of 2"
    assert d.text_card.isVisible() and d.audio_card.isVisible()
    assert not d.next_button.isEnabled() and not d.export_button.isVisible() and not d.back_button.isVisible()
    QTest.mouseClick(d.next_button, Qt.MouseButton.LeftButton)  # nothing happens without a choice
    assert d._step.text() == "Step 1 of 2"
    QTest.mouseClick(d.audio_card, Qt.MouseButton.LeftButton)
    assert d.next_button.isEnabled()


def test_audio_step_two_offers_single_file_each_chapter_and_the_open_chapter(qapp):
    d = open_dialog()
    QTest.mouseClick(d.audio_card, Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.next_button, Qt.MouseButton.LeftButton)
    assert d._step.text() == "Step 2 of 2" and d.export_button.isVisible() and d.back_button.isVisible()
    o = d.options
    assert "single audio file" in o["book"].text() and "every chapter joined into one" in o["book"].text()
    assert "each chapter" in o["chapters"].text() and "Chapter B" in o["open"].text()
    assert all(r.isVisible() and r.isEnabled() for r in o.values()) and o["book"].isChecked()  # the whole book is the default
    assert [d.format_combo.itemData(i) for i in range(d.format_combo.count())] == [".mp3", ".wav"]
    QTest.mouseClick(o["chapters"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.export_button, Qt.MouseButton.LeftButton)
    assert (d.kind, d.scope, d.ext) == ("audio", "chapters", ".mp3") and d.result() == QDialog.DialogCode.Accepted


def test_text_step_two_has_two_options_and_the_file_types_follow_the_choice(qapp):
    d = open_dialog()
    QTest.mouseClick(d.text_card, Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.next_button, Qt.MouseButton.LeftButton)
    assert "single text file" in d.options["book"].text() and "each chapter" in d.options["chapters"].text()
    assert not d.options["open"].isVisible()
    types = lambda: [d.format_combo.itemData(i) for i in range(d.format_combo.count())]  # noqa: E731
    assert types() == [".txt", ".md", ".json"]
    QTest.mouseClick(d.options["chapters"], Qt.MouseButton.LeftButton)
    assert types() == [".txt", ".md"]  # JSON is for the whole document only
    QTest.mouseClick(d.options["book"], Qt.MouseButton.LeftButton)
    d.format_combo.setCurrentIndex(2)
    QTest.mouseClick(d.export_button, Qt.MouseButton.LeftButton)
    assert (d.kind, d.scope, d.ext) == ("text", "book", ".json")


def test_a_document_without_chapters_only_offers_the_single_file(qapp):
    d = open_dialog(chapters=1, title=None)
    QTest.mouseClick(d.audio_card, Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.next_button, Qt.MouseButton.LeftButton)
    assert d.options["book"].isEnabled() and not d.options["chapters"].isEnabled() and not d.options["open"].isEnabled()
    assert d._no_chapters.isVisible()
    QTest.mouseClick(d.export_button, Qt.MouseButton.LeftButton)
    assert (d.kind, d.scope) == ("audio", "book")


def test_back_and_cancel(qapp):
    d = open_dialog()
    QTest.mouseClick(d.text_card, Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.next_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(d.back_button, Qt.MouseButton.LeftButton)
    assert d._step.text() == "Step 1 of 2" and d.text_card.isChecked()
    QTest.mouseClick(d.cancel_button, Qt.MouseButton.LeftButton)
    assert d.result() == QDialog.DialogCode.Rejected and d.kind is None


# ================================================================== from the reader and the library
@pytest.fixture
def win(qapp, root, monkeypatch):
    s = make_services(root)
    qapp.setStyleSheet(build_stylesheet("dark"))
    w = MainWindow(s)
    w.errors_shown = []
    monkeypatch.setattr(w, "_error", lambda title, text: w.errors_shown.append((title, text)))
    w.show()
    split_book(s)
    from core.models import DocInfo

    s.state.doc = DocInfo("d", "My Book", "text", None)
    yield w
    w.close()


def choose(monkeypatch, kind, scope, ext):
    def fake_exec(self):
        self.kind, self.scope, self.ext = kind, scope, ext
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportDialog, "exec", fake_exec)


@pytest.mark.parametrize("kind,scope,method", [("text", "book", "export_text"), ("text", "chapters", "export_text_chapters"),
                                               ("audio", "book", "export_book_audio"), ("audio", "chapters", "export_chapters"),
                                               ("audio", "open", "export_audio")])
def test_each_choice_runs_the_right_export(win, monkeypatch, kind, scope, method):
    calls = []
    for name in ("export_text", "export_text_chapters", "export_book_audio", "export_chapters", "export_audio"):
        monkeypatch.setattr(win, name, lambda ext, n=name: calls.append((n, ext)))
    choose(monkeypatch, kind, scope, ".mp3" if kind == "audio" else ".txt")
    win.show_export_dialog()
    assert calls == [(method, ".mp3" if kind == "audio" else ".txt")]


def test_cancelling_the_window_exports_nothing(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win, "export_book_audio", lambda ext: calls.append(ext))
    monkeypatch.setattr(ExportDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    win.show_export_dialog()
    assert calls == []


def test_exporting_with_nothing_open_says_so(win):
    win.s.model.clear()
    win.show_export_dialog()
    assert "Open something first" in win._toast.last_text


def test_the_whole_book_audio_end_to_end(win, qapp, monkeypatch, tmp_path):
    out = tmp_path / "My Book.mp3"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    choose(monkeypatch, "audio", "book", ".wav")
    out = tmp_path / "My Book.wav"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    win.show_export_dialog()
    assert wait_for(qapp, lambda: out.exists() and win._job is None, 8)
    assert seconds(out) > sum(paragraph_seconds(t) for t in BOOK) and win.errors_shown[-1][0] == "Audio exported"


def test_each_chapter_as_text_end_to_end(win, monkeypatch, tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    choose(monkeypatch, "text", "chapters", ".txt")
    win.show_export_dialog()
    assert sorted(p.name for p in folder.iterdir()) == ["0001 - Chapter A.txt", "0002 - Chapter B.txt"]


def test_the_share_menu_has_one_export_entry(win, monkeypatch):
    opened = []
    monkeypatch.setattr(ExportDialog, "exec", lambda self: opened.append(1) or QDialog.DialogCode.Rejected)  # the window itself is modal
    share = next(b for b in win.reader.findChildren(QToolButton) if b.text().strip() == "Share")
    names = [a.text() for a in share.menu().actions() if a.text()]
    assert "Export…" in names and "Export per-paragraph audio folder…" in names
    assert not any(n.startswith(("Export text as", "Export audio as", "Export every chapter")) for n in names)
    seen = []
    win.reader.export_requested.connect(lambda: seen.append(1))
    next(a for a in share.menu().actions() if a.text() == "Export…").trigger()
    assert seen == [1] and opened == [1]  # and it reaches the main window, which shows the two-step window


def test_export_from_a_library_card_opens_the_document_then_asks(win, qapp, monkeypatch):
    win._open_text_doc("Saved Novel", "First paragraph here.\n\nSecond paragraph here.", "text", None)
    assert wait_for(qapp, lambda: len(win.s.model) == 2)
    doc_id = win.s.state.doc.doc_id
    win.s.model.clear()
    asked = []
    monkeypatch.setattr(ExportDialog, "exec", lambda self: asked.append(self._chapters) or QDialog.DialogCode.Rejected)
    win.library_view._list.setCurrentRow(0)
    win.library_view.export_requested.emit(doc_id)
    assert asked == [1] and len(win.s.model) == 2 and win.s.state.doc.title == "Saved Novel"
    win.library_view._list.setCurrentRow(0)
    from PyQt6.QtWidgets import QPushButton

    export_button = next(b for b in win.library_view.findChildren(QPushButton) if b.text() == "Export…")
    QTest.mouseClick(export_button, Qt.MouseButton.LeftButton)
    assert len(asked) == 2
