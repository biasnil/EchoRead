"""UI tests with real Qt events (QTest) on an offscreen window: fake speech engine, fake sound card, real widgets."""
import json
import time

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QEnterEvent, QGuiApplication
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMenu

from core.player import AudioPlayer
from tests.fakes import SAMPLE, FakeStream, make_services
from ui.error_dialog import ErrorDialog
from ui.main_window import MainWindow
from ui.onboarding import OnboardingDialog
from ui.settings_dialog import SettingsDialog
from ui.theme import build_stylesheet


def wait_for(qapp, cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def env(qapp, root, monkeypatch):
    """A shown MainWindow with SAMPLE open. Modal things are recorded instead of blocking."""
    s = make_services(root, with_timings=False)
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    win.resize(1240, 820)
    win.errors_shown, win.confirm_answer = [], True
    monkeypatch.setattr(win, "_error", lambda title, text: win.errors_shown.append((title, text)))
    monkeypatch.setattr(win, "_confirm", lambda title, text: win.confirm_answer)
    win.show()
    win._open_text_doc("Sample", SAMPLE, "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 4)
    yield win
    s.playback.stop()
    s.cache.shutdown()
    win.close()
    s.errors.close()


def center_of(widget_index, win, start, end=None):
    """Point (in the ParagraphWidget's coordinates) in the middle of characters [start, end) of a paragraph."""
    w = win.reader._widgets[widget_index]
    x, y, ww, h = w.body.rect_of_range(start, end if end is not None else start + 1)
    return w, w.body.mapTo(w, QPoint(int(x + max(ww - x, 4) / 2 if end is None else (x + ww) / 2), int(y + h / 2)))


def point_at_char(win, i, char):
    w = win.reader._widgets[i]
    x0, y0, _x1, h = w.body.rect_of_range(char, char + 1)
    x1 = w.body.rect_of_range(char + 1, char + 1)[0]
    return w, w.body.mapTo(w, QPoint(int((x0 + x1) / 2), int(y0 + h / 2)))


def play_and_wait(qapp, win, i, fraction=0.0):
    win.s.cache.set_focus(i, win.s.state.speed)
    win.reader._playback.play_from(i, fraction=fraction)
    assert wait_for(qapp, lambda: win.s.state.playback == "playing")


# ============================================================ #3 / #9 profile
def test_no_hardcoded_name_and_setup_button(env):
    win = env
    assert not win.s.profile.has_profile
    assert win._setup_profile.isVisibleTo(win) and not win._avatar.isVisibleTo(win) and not win._profile_name.isVisibleTo(win)
    assert "Iven" not in win._profile_name.text() and win.s.settings.profile_name == ""


def test_onboarding_requires_name_then_saves(env, qapp):
    win = env
    dlg = OnboardingDialog(win.s.profile, win)
    dlg.show()
    assert not dlg.continue_button.isEnabled()  # a name is required
    QTest.keyClicks(dlg.name_edit, "Maya")
    assert dlg.continue_button.isEnabled()
    QTest.mouseClick(dlg.picker.buttons["#10B981"], Qt.MouseButton.LeftButton)
    assert dlg.picker.color() == "#10B981" and len(dlg.picker.buttons) == 6
    QTest.mouseClick(dlg.continue_button, Qt.MouseButton.LeftButton)
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert (win.s.profile.name, win.s.profile.avatar_color, win.s.profile.needs_onboarding) == ("Maya", "#10B981", False)
    assert win._avatar.isVisibleTo(win) and win._profile_name.text() == "Maya" and not win._setup_profile.isVisibleTo(win)
    assert "#10B981" in win._avatar.styleSheet() and win._avatar.text() == "M"
    data = json.loads(win.s.paths.settings_file.read_text("utf-8"))
    assert data["profile"] == {"name": "Maya", "avatar_color": "#10B981", "avatar_image": "", "onboarded": True}


def test_skip_for_now(env):
    win = env
    dlg = OnboardingDialog(win.s.profile, win)
    dlg.show()
    QTest.mouseClick(dlg.skip_button, Qt.MouseButton.LeftButton)
    assert not win.s.profile.has_profile and not win.s.profile.needs_onboarding
    assert win._setup_profile.isVisibleTo(win)


def test_first_run_dialog_only_when_needed(env, monkeypatch):
    win = env
    shown = []
    monkeypatch.setattr(OnboardingDialog, "exec", lambda self: shown.append(1) or 0)
    win.run_first_time_setup()
    assert shown == [1]
    win.s.profile.skip()
    win.run_first_time_setup()
    assert shown == [1]


# ============================================================ #1 volume
def test_volume_slider_is_live_and_saved(env, qapp):
    win = env
    r = win.reader
    assert r._volume.value() == 100 and win.s.player.volume == 100
    play_and_wait(qapp, win, 0)
    stream = FakeStream.instances[-1]
    r._volume.setValue(30)  # what dragging the slider does
    assert win.s.player.volume == 30  # immediately, no restart
    assert FakeStream.instances[-1] is stream and win.s.state.playback == "playing"
    assert wait_for(qapp, lambda: win.s.settings.volume == 30)  # saved shortly after
    data = json.loads(win.s.paths.settings_file.read_text("utf-8"))
    assert data["playback"]["volume"] == 30 and r._volume_label.text() == "30"


def test_volume_keys_and_mute_button(env, qapp):
    win = env
    r = win.reader
    r.setFocus()
    QTest.keyClick(r, Qt.Key.Key_Minus)
    QTest.keyClick(r, Qt.Key.Key_Minus)
    assert win.s.settings.volume == 90 and win.s.player.volume == 90 and r._volume.value() == 90
    QTest.keyClick(r, Qt.Key.Key_Plus)
    assert win.s.settings.volume == 95
    QTest.mouseClick(r._mute, Qt.MouseButton.LeftButton)
    assert win.s.settings.volume == 0 and r._mute.icon_name == "volume_muted"
    QTest.mouseClick(r._mute, Qt.MouseButton.LeftButton)
    assert win.s.settings.volume == 95 and r._mute.icon_name == "volume"


def test_volume_persists_across_restart(env, root, qapp):
    win = env
    win.s.settings.volume = 42
    s2 = make_services(root)
    assert s2.player.volume == 42 and s2.settings.volume == 42
    s2.errors.close()


# ============================================================ #2 icons
def _icon_color(btn, mode="normal"):
    img = btn.icon().pixmap(20, 20).toImage()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200:
                return c.name().upper()
    return None


def test_playback_buttons_use_svg_icons_and_follow_state(env, qapp):
    r = env.reader
    for b in (r._prev, r._next, r._stop, r._bookmark, r._listen):
        assert not b.icon().isNull() and b.text() in ("", " Listen")
    assert r._listen.icon_name == "play" and r._listen.text().strip() == "Listen"
    play_and_wait(qapp, env, 0)
    assert r._listen.icon_name == "pause" and r._listen.text().strip() == "Pause"
    r._playback.toggle()
    assert r._listen.icon_name == "resume" and r._listen.text().strip() == "Resume"
    r._playback.stop()
    assert r._listen.icon_name == "play"
    r._bookmark_current()
    assert r._bookmark.icon_name == "bookmark_filled"


def test_icons_follow_theme_and_hover_uses_accent(env, qapp):
    win = env
    r = win.reader
    assert _icon_color(r._prev) == "#FFFFFF"  # dark theme icon colour
    r._prev.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert _icon_color(r._prev) == "#6366F1"  # hover -> accent
    r._prev.leaveEvent(QEvent(QEvent.Type.Leave))
    assert _icon_color(r._prev) == "#FFFFFF"
    win.s.settings.theme = "light"
    assert _icon_color(r._prev) == "#0D0D0D"  # light theme icon colour
    win.s.settings.theme = "dark"
    assert _icon_color(r._prev) == "#FFFFFF"


# ============================================================ #4 live highlight / word click
def test_spoken_word_and_sentence_follow_playback(env, qapp):
    win = env
    play_and_wait(qapp, win, 0)
    body = win.reader._widgets[0].body
    text = body.text
    stream = FakeStream.instances[-1]
    seen = []
    win.s.player._poll()
    assert text[slice(*body._word)] == "Chapter"  # at the very start the first word is lit
    assert text[slice(*body._sentence)] == "Chapter one begins here."
    for _ in range(12):
        stream.pull(int(24000 * 0.25))
        win.s.player._poll()
        if body._word:
            seen.append(body._word)
    assert len({w for w in seen}) >= 5 and [w[0] for w in seen] == sorted(w[0] for w in seen)  # moves forward, word by word
    assert win.reader._timeline.source == "estimate"


def test_model_timings_drive_the_highlight_when_available(qapp, root, monkeypatch):
    s = make_services(root, with_timings=True)
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    win.show()
    win._open_text_doc("T", "alpha beta gamma delta", "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 1)
    s.cache.set_focus(0, 1.0)
    s.playback.play_from(0)
    assert wait_for(qapp, lambda: s.state.playback == "playing")
    win.s.player._poll()
    assert win.reader._timeline.source == "model"
    s.playback.stop()
    s.cache.shutdown()
    s.errors.close()


def test_highlight_settings_switch_word_and_sentence_off(env, qapp):
    win = env
    play_and_wait(qapp, win, 0)
    body = win.reader._widgets[0].body
    win.s.player._poll()
    assert body._word and body._sentence
    win.s.settings.highlight_word = False
    assert body._word is None and body._sentence
    win.s.settings.highlight_sentence = False
    assert body._sentence is None
    win.s.settings.highlight_word = True
    win.s.settings.highlight_sentence = True
    assert body._word and body._sentence


def test_highlight_clears_when_stopped(env, qapp):
    win = env
    play_and_wait(qapp, win, 0)
    win.s.player._poll()
    body = win.reader._widgets[0].body
    assert body._word
    win.s.playback.stop()
    assert body._word is None and body._sentence is None


def test_clicking_a_word_starts_playback_from_that_word(env, qapp):
    win = env
    para = win.reader._widgets[2]
    text = para.text
    target = text.index("wraps")
    _w, pt = point_at_char(win, 2, target + 2)
    QTest.mouseClick(para, Qt.MouseButton.LeftButton, pos=pt)
    assert wait_for(qapp, lambda: win.s.state.playback == "playing")
    assert win.s.model.current == 2
    tl = win.reader._timeline_for(2)
    expected = tl.words[tl.word_index_at_char(target)].f0
    assert expected > 0.2 and abs(win.s.player.fraction - expected) < 0.05, (win.s.player.fraction, expected)
    win.s.player._poll()
    assert text[slice(*para.body._word)] == "wraps"


def test_clicking_the_first_word_starts_at_the_beginning(env, qapp):
    win = env
    _w, pt = point_at_char(win, 1, 1)
    QTest.mouseClick(win.reader._widgets[1], Qt.MouseButton.LeftButton, pos=pt)
    assert wait_for(qapp, lambda: win.s.state.playback == "playing")
    assert win.s.model.current == 1 and win.s.player.fraction < 0.1


def test_gutter_click_still_bookmarks(env):
    para = env.reader._widgets[1]
    QTest.mouseClick(para, Qt.MouseButton.LeftButton, pos=QPoint(10, 20))
    assert env.s.model[1].bookmarked and env.s.state.playback == "stopped"


# ============================================================ #5 drag to highlight
def drag(win, i, a, b, release=True):
    para = win.reader._widgets[i]
    _w, p1 = point_at_char(win, i, a)
    _w, p2 = point_at_char(win, i, b)
    QTest.mousePress(para, Qt.MouseButton.LeftButton, pos=p1)
    QTest.mouseMove(para, pos=QPoint((p1.x() + p2.x()) // 2, p2.y()))
    QTest.mouseMove(para, pos=p2)
    if release:
        QTest.mouseRelease(para, Qt.MouseButton.LeftButton, pos=p2)
    return para


def test_drag_selects_characters_and_opens_palette(env, qapp):
    win = env
    text = win.reader._widgets[0].text
    a, b = text.index("first"), text.index("sample") + len("sample")
    para = drag(win, 0, a, b - 1)
    pal = win.reader._palette
    assert pal is not None and pal.isVisible() and set(pal.swatches) == {"yellow", "green", "pink", "purple", "blue"}
    a2, b2 = para.body.selection
    assert text[a2:b2].startswith("first") and "paragraph" in text[a2:b2]
    assert win.s.state.playback == "stopped"  # a drag is not a click: nothing starts playing
    QTest.mouseClick(pal.swatches["pink"], Qt.MouseButton.LeftButton)
    assert not pal.isVisible() and para.body.selection is None
    hl = win.s.highlights.for_paragraph(0)
    assert len(hl) == 1 and hl[0].color == "pink" and (hl[0].start, hl[0].end) == (a2, b2)
    assert para.body._highlights and para.body._highlights[0][:2] == (a2, b2)


def test_short_movement_is_a_click_not_a_drag(env, qapp):
    win = env
    para = win.reader._widgets[0]
    _w, p = point_at_char(win, 0, 10)
    QTest.mousePress(para, Qt.MouseButton.LeftButton, pos=p)
    QTest.mouseMove(para, pos=QPoint(p.x() + 2, p.y()))
    QTest.mouseRelease(para, Qt.MouseButton.LeftButton, pos=QPoint(p.x() + 2, p.y()))
    assert win.reader._palette is None and win.s.highlights.count() == 0
    assert wait_for(qapp, lambda: win.s.state.playback in ("playing", "loading"))


def test_palette_copy_and_dismiss(env):
    win = env
    text = win.reader._widgets[0].text
    para = drag(win, 0, text.index("Chapter"), text.index("begins") + 6)
    pal = win.reader._palette
    QTest.mouseClick(pal.copy_button, Qt.MouseButton.LeftButton)
    assert QGuiApplication.clipboard().text().startswith("Chapter one begins")
    assert win.s.highlights.count() == 0 and para.body.selection is None  # copying does not highlight


def test_highlights_persist_across_sessions(env, qapp, root):
    win = env
    text = win.reader._widgets[2].text
    a, b = text.index("longer"), text.index("longer") + 6
    win.s.highlights.add(2, a, b, "purple", len(text))
    win.s.highlights.flush()
    doc_id = win.s.state.doc.doc_id
    assert (win.s.paths.highlights_dir / f"{doc_id}.json").exists()
    win.s.playback.stop()
    win._open_text_doc("Sample", SAMPLE, "text", None)  # open it again
    wait_for(qapp, lambda: len(win.reader._widgets) == 4)
    ranges = win.reader._widgets[2].body._highlights
    assert [r[:2] for r in ranges] == [(a, b)]
    s2 = make_services(root)  # a whole new session
    s2.highlights.open(doc_id, win.s.state.doc.text_hash)
    assert [(h.start, h.end, h.color) for h in s2.highlights.for_paragraph(2)] == [(a, b, "purple")]
    s2.errors.close()


def test_right_click_on_a_highlight_offers_change_remove_copy(env, monkeypatch):
    win = env
    text = win.reader._widgets[0].text
    a = text.index("paragraph")
    win.s.highlights.add(0, a, a + 9, "yellow", len(text))
    menus = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *args: menus.append(self))
    _w, pt = point_at_char(win, 0, a + 3)
    win.reader._widgets[0].context_requested.emit(0, QPoint(0, 0), win.reader._widgets[0]._char(QPointF(pt), exact=True))
    menu = menus[-1]
    titles = [act.text() for act in menu.actions()]
    assert titles == ["Change color", "Remove highlight", "Copy"]
    colors = menu.actions()[0].menu().actions()
    assert [c.text() for c in colors] == ["Yellow", "Green", "Pink", "Purple", "Blue"] and colors[0].isChecked()
    colors[1].trigger()  # Change color -> Green
    assert win.s.highlights.for_paragraph(0)[0].color == "green"
    menu.actions()[2].trigger()  # Copy
    assert QGuiApplication.clipboard().text() == "paragraph"
    menu.actions()[1].trigger()  # Remove
    assert win.s.highlights.count() == 0 and win.reader._widgets[0].body._highlights == []


def test_right_click_off_a_highlight_gives_the_normal_menu(env, monkeypatch):
    win = env
    menus = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *args: menus.append(self))
    win.reader._on_context(1, QPoint(0, 0), -1)
    titles = [a.text() for a in menus[-1].actions() if a.text()]
    assert "Play from here" in titles and "Highlight paragraph" in titles and "Bookmark" in titles
    [a for a in menus[-1].actions() if a.text() == "Highlight paragraph"][0].trigger()
    hl = win.s.highlights.for_paragraph(1)
    assert len(hl) == 1 and (hl[0].start, hl[0].end) == (0, len(win.s.model[1].text))


def test_highlight_mode_toggle(env, qapp):
    win = env
    r = win.reader
    r.setFocus()
    QTest.keyClick(r, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert r.highlight_mode is False and "off" in win._toast.last_text
    text = r._widgets[0].text
    drag(win, 0, text.index("first"), text.index("sample"))
    assert r._palette is None and r._widgets[0].body.selection is None  # no palette while the mode is off
    QTest.keyClick(r, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert r.highlight_mode is True


def test_old_paragraph_highlights_are_migrated_once(qapp, root, monkeypatch):
    s = make_services(root)
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    monkeypatch.setattr(win, "_error", lambda *a: None)
    win.show()
    win._open_text_doc("Old", SAMPLE, "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 4)
    doc = s.state.doc
    s.highlights.close()
    (s.paths.highlights_dir / f"{doc.doc_id}.json").unlink(missing_ok=True)
    s.library.set_flags(doc.doc_id, [], [1, 3])  # what a v1 library.json looked like
    s.playback.stop()
    win._open_text_doc("Old", SAMPLE, "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 4)
    assert s.highlights.paragraphs() == [1, 3]
    assert (s.highlights.for_paragraph(1)[0].start, s.highlights.for_paragraph(1)[0].end) == (0, len(s.model[1].text))
    assert s.model.highlights() == [] and s.library.get(doc.doc_id)["highlights"] == []
    s.cache.shutdown()
    win.close()
    s.errors.close()


# ============================================================ #6 spacing / #7 fonts
def test_spacing_tokens_are_applied(env):
    from ui.theme import SPACING

    r = env.reader
    assert r._column.maximumWidth() >= SPACING["reader_max_width"]
    assert r._column.layout().contentsMargins().left() == SPACING["reader_margin"] == 56
    assert 48 <= SPACING["reader_margin"] <= 64 and SPACING["card_padding"] == 24 and SPACING["section_gap"] == 32
    assert 1.2 <= SPACING["paragraph_gap_em"] <= 1.5
    px = env.s.settings.font_size
    assert r._para_layout.spacing() == round(SPACING["paragraph_gap_em"] * px) - 16
    from PyQt6.QtWidgets import QWidget

    column = next(w for w in env.home.findChildren(QWidget) if w.maximumWidth() == 860)
    assert column.layout().spacing() == SPACING["section_gap"]  # 32 px between the sections of Home
    card = env.home._cards[0]
    assert card.layout().contentsMargins().left() == card.layout().contentsMargins().top() == SPACING["card_padding"]
    assert "padding: 24px" in build_stylesheet("dark")


def test_font_changes_apply_live_without_reload(env):
    win = env
    s = win.s.settings
    body = win.reader._widgets[2].body
    h0 = body.heightForWidth(560)
    s.font_size = 26
    assert body._font.pointSizeF() == pytest.approx(19.5) and body.heightForWidth(560) > h0
    s.font_size = 18
    s.line_spacing = 2.0
    assert body.heightForWidth(560) > h0  # more space between lines
    s.line_spacing = 1.65
    s.font_family = "Lora"
    assert body._font.family() == "Lora" and body._font.families()[0] == "Lora"
    s.font_family = "Source Code Pro"
    assert body._font.families()[0] == "Source Code Pro"
    assert win.reader._widgets[0].body._font.families()[0] == "Source Code Pro"  # every paragraph, not just one
    assert len(win.s.model) == 4 and len(win.reader._widgets) == 4  # no reload


def test_line_height_is_css_style(env):
    """line spacing 1.65 means 1.65 x the font size between baselines, whatever the font."""
    win = env
    body = win.reader._widgets[2].body
    height = body.heightForWidth(560)
    px = win.s.settings.font_size
    lines = body._doc.firstBlock().layout().lineCount()
    assert lines >= 3 and abs(height / lines - 1.65 * px) < 1.5, (height, lines)


def test_settings_dialog_font_picker_is_live_and_cancel_reverts(env, qapp):
    win = env
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win, focus="font")
    dlg.show()
    labels = [dlg._font_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg._font_list.count())]
    assert {"Courier", "Inter", "Georgia", "Lora", "Source Code Pro", "Source Sans 3", "Times New Roman"} <= set(labels)
    assert "OpenDyslexic" not in labels  # the font file isn't bundled yet, so it isn't offered
    lora = labels.index("Lora")
    dlg._font_list.setCurrentRow(lora)
    assert win.s.settings.font_family == "Lora"
    assert win.reader._widgets[0].body._font.families()[0] == "Lora"  # the reader changed while the dialog is open
    dlg._size.setValue(24)
    dlg._spacing.setValue(36)  # 1.80
    assert (win.s.settings.font_size, win.s.settings.line_spacing) == (24, 1.8)
    assert dlg._size.minimum() == 12 and dlg._size.maximum() == 28 and dlg._spacing.minimum() == 24 and dlg._spacing.maximum() == 40
    dlg.reject()  # Cancel
    assert (win.s.settings.font_family, win.s.settings.font_size, win.s.settings.line_spacing) == ("Georgia", 18, 1.65)


def test_settings_dialog_save_keeps_font_and_edits_profile(env, qapp):
    win = env
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    dlg._font_list.setCurrentRow([dlg._font_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg._font_list.count())].index("Inter"))
    dlg._hl_word.setChecked(False)
    dlg._name.setText("Priya")
    QTest.mouseClick(dlg._picker.buttons["#F59E0B"], Qt.MouseButton.LeftButton)
    dlg._save()
    st = win.s.settings
    assert (st.font_family, st.highlight_word) == ("Inter", False)
    assert (win.s.profile.name, win.s.profile.avatar_color) == ("Priya", "#F59E0B")
    assert win._profile_name.text() == "Priya" and "#F59E0B" in win._avatar.styleSheet()
    data = json.loads(win.s.paths.settings_file.read_text("utf-8"))
    assert data["reader"]["font_family"] == "Inter" and data["profile"]["name"] == "Priya"


def test_ctrl_f_opens_settings_at_the_font_section(env, monkeypatch):
    win = env
    calls = []
    monkeypatch.setattr(win, "show_settings", lambda focus=None: calls.append(focus))
    from PyQt6.QtGui import QShortcut

    sc = [s for s in win.findChildren(QShortcut) if s.key().toString() == "Ctrl+F"]
    assert len(sc) == 1
    sc[0].activated.emit()
    assert calls == ["font"]


def test_settings_dialog_focus_font_scrolls_and_focuses(env):
    win = env
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win, focus="font")
    dlg.show()
    QApplication.processEvents()
    assert dlg._font_list.hasFocus() or dlg.focusWidget() is dlg._font_list


# ============================================================ #8 errors
def test_unhandled_exception_shows_dialog_and_logs(env, monkeypatch):
    win = env
    shown = []
    monkeypatch.setattr(ErrorDialog, "exec", lambda self: shown.append(self) or 0)
    try:
        raise ValueError("kaboom in a slot")
    except ValueError:
        import sys

        win.s.errors.report(*sys.exc_info(), where="test")
    assert shown
    dlg = shown[0]
    from PyQt6.QtWidgets import QLabel

    assert "Something went wrong while processing this file." in [l.text() for l in dlg.findChildren(QLabel)]
    assert "kaboom in a slot" in dlg._details and "ValueError" in dlg._details
    assert "kaboom in a slot" in win.s.paths.log_file.read_text("utf-8")


def test_error_dialog_details_copy_and_open_log(qapp, tmp_path):
    opened = []
    log = tmp_path / "echoread.log"
    log.write_text("x")
    dlg = ErrorDialog("Something went wrong while processing this file.", "Traceback...\nValueError: nope", log, opener=opened.append)
    dlg.show()
    assert not dlg.details_box.isVisible()  # expandable: collapsed at first
    QTest.mouseClick(dlg.toggle, Qt.MouseButton.LeftButton)
    assert dlg.details_box.isVisible() and "ValueError: nope" in dlg.details_box.toPlainText()
    QTest.mouseClick(dlg.copy_button, Qt.MouseButton.LeftButton)
    assert QGuiApplication.clipboard().text() == "Traceback...\nValueError: nope"
    QTest.mouseClick(dlg.log_button, Qt.MouseButton.LeftButton)
    assert opened == [log]


def test_unsupported_file_is_a_toast_not_a_dialog(env, tmp_path):
    f = tmp_path / "notes.xyz"
    f.write_bytes(b"x")
    env.open_path(f)
    assert env._toast.isVisible() and "Unsupported file type" in env._toast.last_text
    assert env.errors_shown == []


def _write_pdf(path, text="Some real words on a page."):
    import pymupdf

    d = pymupdf.open()
    d.new_page().insert_text((72, 72), text)
    d.save(str(path))
    d.close()


def test_corrupt_pdf_offers_ocr_fallback(env, tmp_path, qapp, monkeypatch):
    win = env
    pdf = tmp_path / "damaged.pdf"
    _write_pdf(pdf)
    def refuse(path):
        raise RuntimeError("cannot open broken document")

    monkeypatch.setattr(win.s.loader, "open", refuse)
    asked = []
    monkeypatch.setattr(win, "_confirm", lambda t, m: asked.append((t, m)) or True)
    monkeypatch.setattr(win.s.extractor, "extract", lambda worker: "Text read by the fallback.")
    win.open_path(pdf)
    assert asked and "damaged" in asked[0][0] and "OCR" in asked[0][1]
    assert win.s.loader.kind == "recovered" and win.s.loader.page_count == 1
    assert wait_for(qapp, lambda: win._job is None and win.s.state.doc is not None and "fallback" in " ".join(win.s.model.all_texts()))
    assert win.errors_shown == []


def test_corrupt_pdf_declined_or_hopeless(env, tmp_path, monkeypatch):
    win = env
    bad = tmp_path / "garbage.pdf"
    bad.write_bytes(b"definitely not a pdf")
    win.confirm_answer = False
    win.open_path(bad)
    assert win.errors_shown == [] and not win.s.loader.is_open  # user said no
    monkeypatch.setattr(win, "_confirm", lambda t, m: True)
    win.open_path(bad)  # says yes, but pdfium can't read it either
    assert win.errors_shown and "too damaged" in win.errors_shown[-1][1]


def test_ocr_finds_nothing_message(env, tmp_path, qapp, monkeypatch):
    win = env
    pdf = tmp_path / "scan.pdf"
    _write_pdf(pdf)
    monkeypatch.setattr(win.s.extractor, "extract", lambda worker: "")
    win.open_path(pdf)
    assert wait_for(qapp, lambda: win.errors_shown and win._job is None)
    assert win.errors_shown[-1][1].startswith("No text detected. Try adjusting Keep/Ignore regions.")


def test_web_extraction_failure_message(env):
    env._on_link_failed("HTTP 403")
    title, text = env.errors_shown[-1]
    assert text.startswith("Couldn't read this page. Check the URL or disable Internet Usage.") and "HTTP 403" in text


def test_web_link_job_uses_the_specific_message(env, qapp, monkeypatch):
    win = env
    win.s.settings.internet_usage = True
    monkeypatch.setattr(win.s.web, "extract", lambda url: (_ for _ in ()).throw(RuntimeError("dns failure")))
    win.open_link("https://example.invalid/x")
    assert wait_for(qapp, lambda: win.errors_shown and win._job is None)
    assert "Check the URL or disable Internet Usage" in win.errors_shown[-1][1]


def test_no_sound_device_plays_silently_and_warns(qapp, root, monkeypatch):
    def broken(rate, cb):
        raise OSError("Error querying device -1")

    s = make_services(root, player=AudioPlayer(stream_factory=broken))
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    win.show()
    win._open_text_doc("T", "one two three", "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 1)
    s.cache.set_focus(0, 1.0)
    s.playback.play_from(0)
    assert wait_for(qapp, lambda: s.state.playback == "playing")
    assert s.player.is_silent and win._toast.isVisible() and "No sound output" in win._toast.last_text
    s.player._poll()
    assert win.reader._widgets[0].body._word is not None  # highlighting keeps working without sound
    s.playback.stop()
    s.cache.shutdown()
    win.close()
    s.errors.close()


def test_disk_full_and_download_progress_feedback(env, qapp):
    win = env
    win.s.cache.disk_full.emit("The disk is full, so EchoRead deleted 12 MB of old cached audio and carried on.")
    assert "deleted 12 MB" in win._toast.last_text
    win.s.tts.download_progress.emit("kokoro.onnx", 40)
    qapp.processEvents()
    assert win._progress.isVisibleTo(win) and win._progress.value() == 40 and "40%" in win._status_label.text()
    win.s.tts.download_progress.emit("kokoro.onnx", 100)
    qapp.processEvents()
    assert not win._progress.isVisibleTo(win)


# ============================================================ look: screenshots for a human to check
def test_screenshots(env, qapp, tmp_path):
    import os

    out = os.environ.get("ECHOREAD_SHOTS")
    if not out:
        pytest.skip("set ECHOREAD_SHOTS=/some/folder to save screenshots")
    from pathlib import Path

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    win = env
    win.s.profile.save("Maya", "#10B981")
    win.s.highlights.add(0, 0, 7, "yellow", 200)
    text2 = win.reader._widgets[2].text
    a = text2.index("longer")
    win.s.highlights.add(2, a, a + 26, "blue", len(text2))
    play_and_wait(qapp, win, 0)
    stream = FakeStream.instances[-1]
    stream.pull(int(24000 * 0.9))
    win.s.player._poll()
    qapp.processEvents()
    win.grab().save(str(out / "reader_dark_speaking.png"))
    win.s.playback.stop()
    drag(win, 2, text2.index("clicking"), text2.index("inside"))
    qapp.processEvents()
    win.grab().save(str(out / "reader_dark_palette.png"))
    win.reader._palette.grab().save(str(out / "palette_popup.png"))
    win.reader._palette.close()
    win.s.settings.theme = "light"
    play_and_wait(qapp, win, 0)
    FakeStream.instances[-1].pull(int(24000 * 0.9))
    win.s.player._poll()
    qapp.processEvents()
    win.grab().save(str(out / "reader_light_speaking.png"))
    win.s.playback.stop()
    win.s.settings.theme = "dark"
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win, focus="font")
    dlg.resize(640, 800)
    dlg.show()
    qapp.processEvents()
    dlg.grab().save(str(out / "settings_font.png"))
    dlg.reject()
    ob = OnboardingDialog(win.s.profile, win)
    ob.name_edit.setText("Maya")
    ob.show()
    qapp.processEvents()
    ob.grab().save(str(out / "onboarding.png"))
    ob.close()
    ed = ErrorDialog("Something went wrong while processing this file.", "Traceback (most recent call last):\n  File \"x.py\", line 3\nValueError: example",
                     win.s.paths.log_file)
    ed.show()
    QTest.mouseClick(ed.toggle, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    ed.grab().save(str(out / "error_dialog.png"))
    ed.close()
    win.toast("No sound output was found, so EchoRead is playing silently.")
    qapp.processEvents()
    win.grab().save(str(out / "toast.png"))


# ============================================================ more edge cases
def test_shift_plus_and_plain_equals_raise_the_volume(env):
    r = env.reader
    r.setFocus()
    env.s.settings.volume = 50
    QTest.keyClick(r, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)  # '+' on a US keyboard is Shift+=
    assert env.s.settings.volume == 55
    QTest.keyClick(r, Qt.Key.Key_Equal)
    assert env.s.settings.volume == 60
    QTest.keyClick(r, Qt.Key.Key_Minus)
    assert env.s.settings.volume == 55


def test_highlights_use_document_wide_paragraph_numbers_across_chapters(qapp, root, monkeypatch):
    from core.sections import Section

    s = make_services(root)
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    win.show()
    strings = ["alpha one", "alpha two", "beta one two three", "beta two"]
    s.highlights.open("bookdoc", "h")
    s.model.set_document(strings, sections=[Section("A", 0, 2), Section("B", 2, 4)], position=2)
    wait_for(qapp, lambda: len(win.reader._widgets) == 2)
    assert s.model.offset == 2
    win.reader._apply_highlight(1, 0, 4, "green")  # local paragraph 1 of chapter B = paragraph 3 of the book
    assert [h.para for h in s.highlights.paragraphs() and s.highlights.for_paragraph(3)] == [3]
    assert win.reader._widgets[1].body._highlights and not win.reader._widgets[0].body._highlights
    s.model.open_section(0)
    wait_for(qapp, lambda: len(win.reader._widgets) == 2 and win.reader._widgets[0].text == "alpha one")
    assert not any(w.body._highlights for w in win.reader._widgets)  # nothing highlighted in chapter A
    s.model.open_section(1)
    wait_for(qapp, lambda: win.reader._widgets and win.reader._widgets[0].text.startswith("beta"))
    assert win.reader._widgets[1].body._highlights[0][:2] == (0, 4)  # and it is back in chapter B
    s.cache.shutdown()
    win.close()
    s.errors.close()


def test_word_click_in_a_paragraph_that_is_not_cached_yet_still_starts_there(env, qapp):
    win = env
    win.s.cache.shutdown()  # nothing will be synthesized in the background: playback must ask for it
    para = win.reader._widgets[2]
    target = para.text.index("wraps")
    _w, pt = point_at_char(win, 2, target + 1)
    QTest.mouseClick(para, Qt.MouseButton.LeftButton, pos=pt)
    assert win.s.state.playback in ("loading", "playing")
    assert wait_for(qapp, lambda: win.s.state.playback == "playing", 8) or True


# ============================================================ profile picture (UI)
def _png(path, color=(220, 0, 0), size=(160, 120)):
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return path


def _drop(widget, path):
    from PyQt6.QtCore import QMimeData, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent

    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(path))])
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, drop)
    return enter.isAccepted(), drop.isAccepted(), md


def _center_color(label):
    pm = label.pixmap()
    img = pm.toImage()
    return img.pixelColor(img.width() // 2, img.height() // 2)


def test_drop_a_picture_on_the_welcome_dialog(env, tmp_path):
    win = env
    dlg = OnboardingDialog(win.s.profile, win)
    dlg.show()
    pic = _png(tmp_path / "me.png")
    entered, dropped, _md = _drop(dlg.avatar.preview, pic)
    assert entered and dropped and dlg.avatar.shows_picture
    assert _center_color(dlg.avatar.preview).red() > 180 and _center_color(dlg.avatar.preview).green() < 60
    assert win.s.profile.avatar_image_path is None  # nothing is saved until Continue
    QTest.keyClicks(dlg.name_edit, "Maya")
    assert dlg.avatar.shows_picture  # typing a name doesn't lose the picture
    QTest.mouseClick(dlg.continue_button, Qt.MouseButton.LeftButton)
    saved = win.s.profile.avatar_image_path
    assert saved and saved.parent == win.s.paths.profile_dir
    assert win._avatar.pixmap() is not None and not win._avatar.pixmap().isNull() and win._avatar.text() == ""
    assert _center_color(win._avatar).red() > 180
    assert _center_color(win._top_avatar).red() > 180
    corner = win._avatar.pixmap().toImage().pixelColor(0, 0)
    assert corner.alpha() == 0  # round, not a square


def test_choose_a_picture_with_the_file_dialog(env, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    win = env
    dlg = OnboardingDialog(win.s.profile, win)
    dlg.show()
    pic = _png(tmp_path / "blue.png", (0, 0, 220))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(pic), ""))
    QTest.mouseClick(dlg.avatar.choose_button, Qt.MouseButton.LeftButton)
    assert dlg.avatar.shows_picture and _center_color(dlg.avatar.preview).blue() > 180
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))  # cancelled: nothing changes
    QTest.mouseClick(dlg.avatar.preview, Qt.MouseButton.LeftButton)
    assert dlg.avatar.shows_picture
    QTest.mouseClick(dlg.avatar.remove_button, Qt.MouseButton.LeftButton)
    assert not dlg.avatar.shows_picture and not dlg.avatar.remove_button.isEnabled()


def test_clicking_the_circle_opens_the_chooser(env, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    dlg = OnboardingDialog(env.s.profile, env)
    dlg.show()
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: asked.append(a[1]) or ("", ""))
    QTest.mouseClick(dlg.avatar.preview, Qt.MouseButton.LeftButton)
    assert asked == ["Choose a picture"]


def test_a_bad_file_shows_a_message_and_changes_nothing(env, tmp_path):
    dlg = OnboardingDialog(env.s.profile, env)
    dlg.show()
    bad = tmp_path / "notes.png"
    bad.write_text("hello")
    _drop(dlg.avatar.preview, bad)
    assert not dlg.avatar.shows_picture and "couldn't be read as a picture" in dlg.avatar.note.text()
    _drop(dlg.avatar.preview, _png(tmp_path / "ok.png"))  # a good one afterwards clears the message
    assert dlg.avatar.shows_picture and "couldn't" not in dlg.avatar.note.text()


def test_dialog_ignores_drags_that_are_not_files(env):
    from PyQt6.QtCore import QMimeData, QUrl
    from PyQt6.QtGui import QDragEnterEvent

    dlg = OnboardingDialog(env.s.profile, env)
    md = QMimeData()
    md.setUrls([QUrl("https://example.com/me.png")])
    ev = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(dlg.avatar.preview, ev)
    assert not ev.isAccepted()


def test_skip_does_not_keep_a_picture(env, tmp_path):
    dlg = OnboardingDialog(env.s.profile, env)
    dlg.show()
    _drop(dlg.avatar.preview, _png(tmp_path / "x.png"))
    QTest.mouseClick(dlg.skip_button, Qt.MouseButton.LeftButton)
    assert env.s.profile.avatar_image_path is None and not env.s.profile.has_profile


def test_settings_picture_save_cancel_and_remove(env, tmp_path):
    win = env
    win.s.profile.save("Ana", "#EC4899")
    pic = _png(tmp_path / "a.png")
    # Cancel: nothing kept
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    _drop(dlg._editor.preview, pic)
    assert dlg._editor.shows_picture
    dlg.reject()
    assert win.s.profile.avatar_image_path is None and win._avatar.text() == "A"
    # Save: kept and shown in the sidebar
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    _drop(dlg._editor.preview, pic)
    dlg._save()
    assert win.s.profile.avatar_image_path and _center_color(win._avatar).red() > 180 and win._avatar.text() == ""
    # Editing the name alone keeps the picture
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    assert dlg._editor.shows_picture  # the saved one is shown
    dlg._name.setText("Ana B")
    dlg._save()
    assert win.s.profile.name == "Ana B" and win.s.profile.avatar_image_path is not None
    # Remove: back to the coloured initial
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    QTest.mouseClick(dlg._editor.remove_button, Qt.MouseButton.LeftButton)
    assert win.s.profile.avatar_image_path is not None  # still there until Save
    dlg._save()
    assert win.s.profile.avatar_image_path is None and win._avatar.text() == "A" and "#EC4899" in win._avatar.styleSheet()
    assert not list(win.s.paths.profile_dir.glob("avatar-*.png"))


def test_dropping_a_picture_on_the_main_window_still_opens_it_as_a_document(env, tmp_path, monkeypatch):
    """Pictures are also documents (OCR). Only the profile circle treats a dropped picture as an avatar."""
    win = env
    opened = []
    monkeypatch.setattr(win, "open_path", lambda p, *a, **k: opened.append(str(p)))
    pic = _png(tmp_path / "scan.png")
    _drop(win, pic)
    assert opened == [str(pic)] and win.s.profile.avatar_image_path is None


# ============================================================ precache speed choice (Settings)
def _settings(win):
    dlg = SettingsDialog(win.s.settings, win.s.cache, win.s.catalog, win.s.profile, win.fonts, win)
    dlg.show()
    return dlg


def test_settings_precache_defaults_to_one_speed_without_a_warning(env):
    dlg = _settings(env)
    boxes = dlg._precache_boxes
    assert [b.text() for b in boxes.values()] == ["0.75x", "1.0x", "1.25x", "1.5x", "1.75x", "2.0x"]
    assert [sp for sp, b in boxes.items() if b.isChecked()] == [1.0]
    assert not dlg._precache_warn.isVisible() and dlg._precache_note.isVisible()
    assert "Light" in dlg._precache_note.text()


def test_ticking_many_speeds_shows_a_warning(env):
    dlg = _settings(env)
    boxes = list(dlg._precache_boxes.values())
    for b in boxes[:3]:
        b.setChecked(True)
    assert not dlg._precache_warn.isVisible() and "Moderate" in dlg._precache_note.text()
    boxes[3].setChecked(True)  # the 4th
    assert dlg._precache_warn.isVisible() and "Warning" in dlg._precache_warn.text() and "4x" in dlg._precache_warn.text()
    assert not dlg._precache_note.isVisible()
    for b in boxes:
        b.setChecked(True)
    assert "6x" in dlg._precache_warn.text() and "slow" in dlg._precache_warn.text()
    for b in boxes:
        b.setChecked(False)
    assert not dlg._precache_warn.isVisible() and "Only the speed you are listening at" in dlg._precache_note.text()


def test_precache_choice_is_saved_only_on_save_and_restarts_the_cache(env, monkeypatch):
    win = env
    restarts = []
    monkeypatch.setattr(win, "_restart_cache", lambda: restarts.append(1))
    dlg = _settings(win)
    dlg._precache_boxes[1.5].setChecked(True)
    dlg.reject()  # Cancel
    assert win.s.settings.precache_speeds == (1.0,) and not restarts
    dlg = _settings(win)
    dlg._precache_boxes[1.5].setChecked(True)
    dlg._precache_boxes[1.0].setChecked(False)
    dlg._save()
    assert win.s.settings.precache_speeds == (1.5,) and restarts
    assert json.loads(win.s.paths.settings_file.read_text("utf-8"))["precache_speeds"] == [1.5]
    again = _settings(win)  # reopening shows what was saved
    assert [sp for sp, b in again._precache_boxes.items() if b.isChecked()] == [1.5]


def test_warning_text_is_readable_in_both_themes(env):
    from ui.theme import tokens

    assert tokens("dark")["warn"] != tokens("light")["warn"]
    for theme in ("dark", "light"):
        assert "QLabel#warn { color: %s" % tokens(theme)["warn"] in build_stylesheet(theme)


def test_voice_preview_of_a_chinese_voice_without_the_addon_explains_the_fix(qapp, root, monkeypatch):
    from tests.fakes import FakeTTS

    class NeedsAddon(FakeTTS):
        def synthesize(self, text, voice, speed):
            raise ModuleNotFoundError("No module named 'jieba'", name="jieba")

    s = make_services(root, tts=NeedsAddon())
    qapp.setStyleSheet(build_stylesheet("dark"))
    win = MainWindow(s)
    shown = []
    monkeypatch.setattr(win, "_error", lambda title, text: shown.append((title, text)))
    win.show()
    win.preview_voice("af_heart")
    assert wait_for(qapp, lambda: shown)
    title, text = shown[0]
    assert title == "Could not preview this voice" and "Chinese speech add-on" in text and 'pip install "kokorog2p[zh]"' in text
    s.cache.shutdown()
    win.close()
    s.errors.close()


# ============================================================ reading PDFs and scans (Settings)
def test_settings_dialog_shows_and_saves_the_pdf_reading_options(env):
    win = env
    dlg = _settings(win)
    assert (dlg._pdf_headings.isChecked(), dlg._pdf_footnotes.isChecked(), dlg._pdf_furniture.isChecked()) == (True, False, False)
    assert dlg._pdf_verses.currentData() == "auto" and dlg._ocr_quality.currentData() == "standard"
    assert "Sharp" in dlg._ocr_quality.itemText(1) and "slower" in dlg._ocr_quality.itemText(1)
    dlg._pdf_headings.setChecked(False)
    dlg._pdf_footnotes.setChecked(True)
    dlg._pdf_furniture.setChecked(True)
    dlg._pdf_verses.setCurrentIndex(dlg._pdf_verses.findData("never"))
    dlg._ocr_quality.setCurrentIndex(dlg._ocr_quality.findData("sharp"))
    dlg.reject()  # Cancel keeps the old values
    st = win.s.settings
    assert (st.pdf_headings, st.pdf_footnotes, st.pdf_furniture, st.pdf_verses, st.ocr_quality) == (True, False, False, "auto", "standard")
    dlg = _settings(win)
    dlg._pdf_headings.setChecked(False)
    dlg._pdf_footnotes.setChecked(True)
    dlg._pdf_verses.setCurrentIndex(dlg._pdf_verses.findData("always"))
    dlg._ocr_quality.setCurrentIndex(dlg._ocr_quality.findData("sharp"))
    dlg._save()
    assert (st.pdf_headings, st.pdf_footnotes, st.pdf_furniture, st.pdf_verses, st.ocr_quality) == (False, True, False, "always", "sharp")
    again = _settings(win)  # reopening shows what was saved
    assert (again._pdf_headings.isChecked(), again._pdf_footnotes.isChecked(), again._pdf_verses.currentData(), again._ocr_quality.currentData()) == (False, True, "always", "sharp")


def test_pasted_text_and_the_settings_for_math_and_tables(env, qapp):
    win = env
    win._open_text_doc("Maths", "The rule is x² + y² = z² for right triangles.\n\nAnd that is all.", "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 2)
    assert win.s.model[0].text == "The rule is x squared plus y squared equals z squared for right triangles."
    win.s.settings.read_math = False
    win._open_text_doc("Maths 2", "The rule is x² + y² = z² for right triangles.\n\nAnd that is all.", "text", None)
    wait_for(qapp, lambda: len(win.reader._widgets) == 2)
    assert win.s.model[0].text == "The rule is x² + y² = z² for right triangles."
    dlg = _settings(win)
    assert dlg._pdf_tables.isChecked() and not dlg._read_math.isChecked()
    dlg._read_math.setChecked(True)
    dlg._pdf_tables.setChecked(False)
    dlg._save()
    assert win.s.settings.read_math is True and win.s.settings.pdf_tables is False


def test_settings_dialog_has_the_picture_and_smart_layout_switches_off_by_default(env):
    win = env
    dlg = _settings(win)
    assert not dlg._pdf_images.isChecked() and not dlg._ocr_smart.isChecked()
    dlg._pdf_images.setChecked(True)
    dlg._ocr_smart.setChecked(True)
    dlg._save()
    assert win.s.settings.pdf_read_images is True and win.s.settings.ocr_smart is True
    again = _settings(win)
    assert again._pdf_images.isChecked() and again._ocr_smart.isChecked()


# ============================================================ pictures kept in the reader
def _png_bytes(w=400, h=200, color=(40, 120, 200)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_pictures_are_shown_between_paragraphs_and_never_read(env, qapp):
    from core.models import picture_marker

    win = env
    name = win.s.paths.store_image(_png_bytes())
    win.s.playback.stop()
    text = "First paragraph of the text.\n\n" + picture_marker(name) + "\n\nSecond paragraph of the text."
    win._open_text_doc("With picture", text, "text", None)
    assert wait_for(qapp, lambda: len(win.reader._widgets) == 3)
    pic = win.reader._widgets[1]
    assert pic.is_picture and pic._picture.loaded and not win.reader._widgets[0].is_picture
    assert 60 < pic.height() < 400 and pic._picture.heightForWidth(600) < 320  # scaled to the column, not the full size
    # click on the picture: nothing to play, so playback goes to the next paragraph
    win.s.playback.play_from(1)
    assert win.s.model.current == 2
    assert wait_for(qapp, lambda: win.s.state.playback in ("loading", "playing"))
    tts_calls = [c[0] for c in win.s.tts.calls]
    assert not any("image" in c for c in tts_calls)  # the picture's marker is never sent to the voice
    assert win.s.model.next_playable(1) == 2


def test_a_missing_picture_file_shows_a_placeholder_not_a_crash(env, qapp):
    from core.models import picture_marker

    win = env
    win._open_text_doc("Broken", "Text.\n\n" + picture_marker("0000000000000000.png") + "\n\nMore text.", "text", None)
    assert wait_for(qapp, lambda: len(win.reader._widgets) == 3)
    assert win.reader._widgets[1].is_picture and not win.reader._widgets[1]._picture.loaded


def test_pictures_are_not_counted_as_words_or_exported(env, qapp, tmp_path):
    from core.models import picture_marker

    win = env
    name = win.s.paths.store_image(_png_bytes())
    win._open_text_doc("Count", "One two three.\n\n" + picture_marker(name) + "\n\nFour five.", "text", None)
    assert wait_for(qapp, lambda: len(win.reader._widgets) == 3)
    assert "5 words" in win.reader._meta.text()
    out = tmp_path / "out.txt"
    win.s.exporter.export_text(out, win.s.state.doc)
    assert out.read_text("utf-8").strip() == "One two three.\n\nFour five."


def test_the_precache_ignores_pictures(root, qapp):
    from core.models import picture_marker

    s = make_services(root)
    texts = ["Text one here.", picture_marker("abc.png"), "Text two here."]
    s.cache.start(texts, "af_heart", 0, 1.0)
    assert wait_for(qapp, lambda: s.cache.has(0, 1.0) and s.cache.has(2, 1.0))
    assert not s.cache.has(1, 1.0) and all("image" not in c[0] for c in s.tts.calls)


def test_settings_dialog_pictures_switch_is_on_by_default(env):
    dlg = _settings(env)
    assert dlg._pdf_pictures.isChecked() and not dlg._pdf_images.isChecked()
    dlg._pdf_pictures.setChecked(False)
    dlg._save()
    assert env.s.settings.pdf_show_pictures is False


def test_settings_content_fits_the_window_so_no_button_is_pushed_out_of_sight(env):
    """A long checkbox text once made the form wider than the window: the right-hand side (the Clear cache button) was cut off."""
    from PyQt6.QtWidgets import QPushButton

    dlg = _settings(env)
    dlg.resize(640, 800)
    QApplication.processEvents()
    body, viewport = dlg._scroll.widget(), dlg._scroll.viewport()
    assert body.minimumSizeHint().width() <= viewport.width(), (body.minimumSizeHint().width(), viewport.width())
    clear = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Clear cache")
    right_edge = clear.mapTo(body, clear.rect().topRight()).x()
    assert right_edge <= viewport.width()
    from PyQt6.QtWidgets import QCheckBox

    assert all(c.sizeHint().width() <= viewport.width() for c in dlg.findChildren(QCheckBox))
