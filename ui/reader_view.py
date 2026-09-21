"""Reader: paragraph list with click-to-jump, bookmark gutter, speed row, Listen pill, bookmarks panel."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QMenu,
                             QScrollArea, QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from core.highlights import HighlightStore
from core.models import SPEEDS, DocInfo, speed_label
from core.paragraphs import ParagraphModel
from core.playback import PlaybackController
from core.settings import SettingsManager
from core.state import AppState
from core.tts import VoiceCatalog
from core.wordtiming import WordTimeline
from .highlight_palette import HighlightPalette, build_highlight_menu
from .theme import SPACING, FontLibrary, highlight_fill, themed_icon
from .widgets import IconButton, ParagraphWidget, make_button, make_label


class ReaderView(QWidget):
    open_file_requested = pyqtSignal()
    add_text_requested = pyqtSignal()
    paste_link_requested = pyqtSignal()
    home_requested = pyqtSignal()
    regions_requested = pyqtSignal()
    rerun_requested = pyqtSignal()
    clear_regions_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    help_requested = pyqtSignal()
    manage_voices_requested = pyqtSignal()
    export_text_requested = pyqtSignal(str)  # ".txt" | ".md" | ".json"
    export_audio_requested = pyqtSignal(str)  # ".wav" | ".mp3"
    export_folder_requested = pyqtSignal()
    export_chapters_requested = pyqtSignal(str)  # ".mp3" | ".wav"
    voice_selected = pyqtSignal(str)
    message_requested = pyqtSignal(str)  # something short to tell the user (the main window shows it as a toast)

    def __init__(self, settings: SettingsManager, state: AppState, model: ParagraphModel,
                 playback: PlaybackController, catalog: VoiceCatalog, highlights: HighlightStore, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._state = state
        self._model = model
        self._playback = playback
        self._catalog = catalog
        self._highlights = highlights
        self._widgets: list[ParagraphWidget] = []
        self._show_ignored = False
        self._theme = settings.theme
        self._highlight_mode = True  # drag across text -> colour palette (Ctrl+Shift+H turns it off/on)
        self._palette: HighlightPalette | None = None
        self._timeline: WordTimeline | None = None
        self._timeline_key: tuple | None = None
        self._last_word_y: float | None = None
        self._premute = 100
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(250)
        self._volume_timer.timeout.connect(self._commit_volume)
        self.setObjectName("page")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build()
        self._wire()
        self.apply_reader_style()
        self._update_playback_ui()
        self._update_speed_buttons()  # reflect the starting speed, not just later changes
        self._sync_volume()
        self.apply_theme(self._theme)

    # ------------------------------------------------------------------ construction
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 8)
        file_menu = self._menu_button("File", bar)
        self._add(file_menu, "Open file…\tCtrl+O", self.open_file_requested.emit)
        self._add(file_menu, "Add text…", self.add_text_requested.emit)
        self._act_link = self._add(file_menu, "Paste link…", self.paste_link_requested.emit)
        file_menu.addSeparator()
        self._add(file_menu, "Back to Home", self.home_requested.emit)

        view_menu = self._menu_button("View", bar)
        self._add(view_menu, "Larger text", lambda: setattr(self._settings, "font_size", self._settings.font_size + 2))
        self._add(view_menu, "Smaller text", lambda: setattr(self._settings, "font_size", self._settings.font_size - 2))
        view_menu.addSeparator()
        self._act_panel = self._add(view_menu, "Bookmarks panel", lambda: self._panel_btn.toggle(), checkable=True)
        self._act_ignored = self._add(view_menu, "Show ignored paragraphs", None, checkable=True)
        self._act_ignored.toggled.connect(self._toggle_show_ignored)
        view_menu.addSeparator()
        self._act_theme = self._add(view_menu, "Switch theme", self._toggle_theme)
        view_menu.aboutToShow.connect(lambda: self._act_theme.setText(
            "Switch to light theme" if self._settings.theme == "dark" else "Switch to dark theme"))

        regions_menu = self._menu_button("Regions", bar)
        self._region_actions = [
            self._add(regions_menu, "Edit regions…", self.regions_requested.emit),
            self._add(regions_menu, "Re-run text extraction", self.rerun_requested.emit),
            self._add(regions_menu, "Clear all regions", self.clear_regions_requested.emit),
        ]

        self._voices_menu = self._menu_button("Voices", bar)
        self._voices_menu.aboutToShow.connect(self._fill_voices_menu)
        self._fill_voices_menu()

        settings_menu = self._menu_button("Settings", bar)
        self._add(settings_menu, "Settings…\tCtrl+,", self.settings_requested.emit)
        help_menu = self._menu_button("Help", bar)
        self._add(help_menu, "Keyboard shortcuts\t?", self.help_requested.emit)

        bar.addStretch(1)
        self._chapters_btn = QToolButton()
        self._chapters_btn.setObjectName("menubtn")
        self._chapters_btn.setText("☰ Chapters")
        self._chapters_btn.setCheckable(True)
        self._chapters_btn.setVisible(False)  # only for documents that were split into chapters
        bar.addWidget(self._chapters_btn)
        self._panel_btn = QToolButton()
        self._panel_btn.setObjectName("menubtn")
        self._panel_btn.setText(" Bookmarks")
        self._panel_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._panel_btn.setCheckable(True)
        bar.addWidget(self._panel_btn)
        share = self._menu_button("Share", bar)
        text_menu = share.addMenu("Export text as")
        for ext in (".txt", ".md", ".json"):
            self._add(text_menu, ext, lambda e=ext: self.export_text_requested.emit(e))
        audio_menu = share.addMenu("Export audio as")
        for ext in (".wav", ".mp3"):
            self._add(audio_menu, ext, lambda e=ext: self.export_audio_requested.emit(e))
        self._add(share, "Export per-paragraph audio folder…", self.export_folder_requested.emit)
        chap_menu = share.addMenu("Export every chapter as")
        for ext in (".mp3", ".wav"):
            self._add(chap_menu, ext, lambda e=ext: self.export_chapters_requested.emit(e))
        self._chapters_export = chap_menu.menuAction()
        self._chapters_export.setVisible(False)
        share.addSeparator()
        self._add(share, "Copy all text", self._copy_all)
        outer.addLayout(bar)
        div = QFrame()
        div.setObjectName("divider")
        outer.addWidget(div)

        body = QHBoxLayout()
        body.setSpacing(0)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("page")
        self._scroll.setWidget(inner)
        row = QHBoxLayout(inner)
        row.addStretch(1)
        self._column = QWidget()
        margin = SPACING["reader_margin"]
        self._column.setMaximumWidth(SPACING["reader_max_width"] + 2 * margin + 40)  # + the bookmark gutter
        self._column.setMinimumWidth(420)
        col = QVBoxLayout(self._column)
        col.setContentsMargins(margin, 36, margin, 24)
        col.setSpacing(6)
        self._title = make_label("", "doctitle", wrap=True)
        self._meta = make_label("", "muted")
        self._section_label = make_label("", "h2", wrap=True)
        self._title.setContentsMargins(37, 0, 17, 0)  # line up with paragraph text
        self._meta.setContentsMargins(37, 0, 17, 0)
        self._section_label.setContentsMargins(37, 4, 17, 0)
        col.addWidget(self._title)
        col.addWidget(self._section_label)
        col.addWidget(self._meta)
        col.addSpacing(14)
        self._para_layout = QVBoxLayout()
        self._para_layout.setSpacing(6)
        col.addLayout(self._para_layout, 1)
        row.addWidget(self._column, 10)
        row.addStretch(1)
        grid.addWidget(self._scroll, 0, 0)

        pill = QFrame()
        pill.setObjectName("pill")
        pv = QVBoxLayout(pill)
        pv.setContentsMargins(16, 10, 16, 10)
        pv.setSpacing(6)
        speeds = QHBoxLayout()
        speeds.setSpacing(2)
        speeds.addStretch(1)
        self._speed_group = QButtonGroup(self)
        self._speed_group.setExclusive(True)
        self._speed_buttons: dict[float, object] = {}
        for sp in SPEEDS:
            b = make_button(speed_label(sp), "speed", lambda s=sp: self._playback.set_speed(s), "Playback speed ([ and ] to change)")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._speed_group.addButton(b)
            self._speed_buttons[sp] = b
            speeds.addWidget(b)
        speeds.addStretch(1)
        pv.addLayout(speeds)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addStretch(1)
        self._prev = IconButton("previous", "", "round", lambda: self._playback.step(-1), "Previous paragraph")
        self._listen = IconButton("play", " Listen", "listen", self._playback.toggle, normal="on_accent", active="on_accent")
        self._listen.setMinimumWidth(150)  # the label changes (Listen / Pause / Resume): keep the pill from jumping
        self._next = IconButton("next", "", "round", lambda: self._playback.step(1), "Next paragraph")
        self._stop = IconButton("stop", "", "round", self._playback.stop, "Stop")
        self._bookmark = IconButton("bookmark", "", "round", self._bookmark_current, "Bookmark current paragraph (B)")
        for w in (self._prev, self._listen, self._next, self._stop, self._bookmark):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            controls.addWidget(w)
        controls.addSpacing(10)
        self._mute = IconButton("volume", "", "round", self._toggle_mute, "Mute / unmute (+ and - change the volume)")
        self._mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setObjectName("volume")
        self._volume.setRange(0, 100)
        self._volume.setFixedWidth(84)
        self._volume.setToolTip("Volume")
        self._volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume_label = make_label("100", "volumelabel")
        controls.addWidget(self._mute)
        controls.addWidget(self._volume)
        controls.addWidget(self._volume_label)
        controls.addStretch(1)
        pv.addLayout(controls)
        grid.addWidget(pill, 0, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        grid.setRowMinimumHeight(0, 240)
        body.addLayout(grid, 1)

        self._side = QFrame()
        self._side.setObjectName("sidepanel")
        self._side.setFixedWidth(290)
        sl = QVBoxLayout(self._side)
        sl.setContentsMargins(14, 16, 14, 14)
        self._side_title = make_label("Bookmarks", "h2")
        sl.addWidget(self._side_title)
        self._side_stack = QStackedWidget()
        sl.addWidget(self._side_stack, 1)
        self._chapter_list = QListWidget()
        self._chapter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._side_stack.addWidget(self._chapter_list)  # page 0
        bm_page = QWidget()
        bl = QVBoxLayout(bm_page)
        bl.setContentsMargins(0, 0, 0, 0)
        self._side_empty = make_label("Click the left edge of a paragraph, or press B, to bookmark it.", "muted", wrap=True)
        bl.addWidget(self._side_empty)
        self._bm_list = QListWidget()
        self._bm_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bl.addWidget(self._bm_list, 1)
        self._side_stack.addWidget(bm_page)  # page 1
        self._side_stack.setCurrentIndex(1)
        self._side.setVisible(False)
        body.addWidget(self._side)
        outer.addLayout(body, 1)

        # keyboard shortcuts work while focus is anywhere inside the reader
        for seq, fn in (
            ("Space", self._playback.toggle),
            ("Left", lambda: self._playback.step(-1)), ("Up", lambda: self._playback.step(-1)),
            ("Right", lambda: self._playback.step(1)), ("Down", lambda: self._playback.step(1)),
            ("[", self._playback.speed_down), ("]", self._playback.speed_up),
            ("B", self._bookmark_current),
            ("?", self.help_requested.emit), ("Shift+/", self.help_requested.emit),
            ("Return", self._play_current), ("Enter", self._play_current),
            ("PgUp", lambda: self._playback.step_section(-1)), ("PgDown", lambda: self._playback.step_section(1)),
            ("Ctrl+Shift+H", self.toggle_highlight_mode),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _menu_button(self, text: str, bar: QHBoxLayout) -> QMenu:
        btn = QToolButton()
        btn.setObjectName("menubtn")
        btn.setText(text)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(btn)
        btn.setMenu(menu)
        bar.addWidget(btn)
        return menu

    @staticmethod
    def _add(menu: QMenu, text: str, fn, checkable: bool = False):
        act = menu.addAction(text)
        act.setCheckable(checkable)
        if fn is not None:
            act.triggered.connect(lambda checked=False, f=fn: f())
        return act

    def _wire(self) -> None:
        self._panel_btn.toggled.connect(lambda on: self._on_panel_toggled("bookmarks", on))
        self._chapters_btn.toggled.connect(lambda on: self._on_panel_toggled("chapters", on))
        self._bm_list.itemClicked.connect(lambda item: self._on_bookmark_clicked(item))
        self._chapter_list.itemClicked.connect(lambda item: self._playback.goto_section(item.data(Qt.ItemDataRole.UserRole)))
        m, s = self._model, self._state
        m.reset.connect(self._rebuild)
        m.document_changed.connect(self._populate_chapters)
        m.section_changed.connect(self._on_section_changed)
        m.flags_changed.connect(self._refresh_bookmarks)
        m.current_changed.connect(self._on_current_changed)
        m.paragraph_changed.connect(self._on_paragraph_changed)
        s.playback_changed.connect(lambda _pb: self._update_playback_ui())
        s.speed_changed.connect(lambda _sp: self._update_speed_buttons())
        s.doc_changed.connect(lambda _doc: self._update_header())
        self._settings.changed.connect(self._on_setting_changed)
        self._playback.player.position_changed.connect(self._on_position)
        self._volume.valueChanged.connect(self._on_volume_slider)
        self._highlights.changed.connect(self._on_highlights_changed)

    def keyPressEvent(self, event) -> None:
        """+ / - change the volume. Handled here rather than as shortcuts because '+' is Shift+= on many keyboards and
        shortcut matching of shifted keys differs between layouts."""
        blocked = event.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        if not blocked:
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._nudge_volume(+5)
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self._nudge_volume(-5)
                event.accept()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ public
    def focus_reader(self) -> None:
        self.setFocus()

    def set_internet_enabled(self, enabled: bool) -> None:
        self._act_link.setEnabled(enabled)
        self._act_link.setToolTip("" if enabled else "Enable Internet Usage in Settings.")

    def set_regions_enabled(self, enabled: bool) -> None:
        for act in self._region_actions:
            act.setEnabled(enabled)

    def show_placeholder(self, text: str) -> None:
        self._clear_widgets()
        self._para_layout.addWidget(make_label(text, "muted", wrap=True))
        self._update_header()

    def scroll_to_current(self) -> None:
        QTimer.singleShot(60, lambda: self._scroll_to(self._model.current))

    def apply_reader_style(self) -> None:
        """Font family, size and line spacing from Settings, applied to what is on screen (no reload)."""
        px = self._settings.font_size
        self._font = FontLibrary.make_font(self._settings.font_family, px)
        self._line_spacing = self._settings.line_spacing
        # distance between the text of two paragraphs = paragraph_gap_em x font size (each paragraph has 8 px padding above and below)
        self._para_layout.setSpacing(max(0, round(SPACING["paragraph_gap_em"] * px) - 16))
        for w in self._widgets:
            w.body.set_font_spec(self._font, self._line_spacing)

    apply_font_size = apply_reader_style  # the old name

    def apply_theme(self, name: str) -> None:
        self._theme = "light" if name == "light" else "dark"
        for b in (self._prev, self._listen, self._next, self._stop, self._bookmark, self._mute):
            b.apply_theme(self._theme)
        self._panel_btn.setIcon(themed_icon("bookmark", self._theme, "strong", "accent", 20))
        self._panel_btn.setIconSize(QSize(16, 16))
        for i, w in enumerate(self._widgets):
            w.apply_theme(self._theme)
            self._apply_highlights(i)

    @property
    def highlight_mode(self) -> bool:
        return self._highlight_mode

    def toggle_highlight_mode(self) -> None:
        """Ctrl+Shift+H: drag-to-highlight on/off. When off, dragging across text does nothing special."""
        self._highlight_mode = not self._highlight_mode
        for w in self._widgets:
            w.set_selectable(self._highlight_mode)
        self.message_requested.emit("Highlight mode on: drag across text to pick a color" if self._highlight_mode
                                    else "Highlight mode off")

    # ------------------------------------------------------------------ volume
    def _sync_volume(self) -> None:
        v = self._settings.volume
        self._volume.blockSignals(True)
        self._volume.setValue(v)
        self._volume.blockSignals(False)
        self._volume_label.setText(str(v))
        self._mute.set_icon_name("volume_muted" if v == 0 else "volume")

    def _on_volume_slider(self, value: int) -> None:
        """Live: the player follows the slider at once; the setting is saved a moment after you stop dragging."""
        self._playback.player.set_volume(value)
        self._volume_label.setText(str(value))
        self._mute.set_icon_name("volume_muted" if value == 0 else "volume")
        self._volume_timer.start()

    def _commit_volume(self) -> None:
        self._settings.volume = self._volume.value()

    def _nudge_volume(self, delta: int) -> None:
        self._volume_timer.stop()
        self._settings.volume = self._settings.volume + delta

    def _toggle_mute(self) -> None:
        self._volume_timer.stop()
        if self._settings.volume > 0:
            self._premute = self._settings.volume
            self._settings.volume = 0
        else:
            self._settings.volume = self._premute or 100

    def _on_setting_changed(self, key: str) -> None:
        if key in ("reader.font_family", "reader.font_size", "reader.line_spacing"):
            self.apply_reader_style()
        elif key in ("reader.highlight_sentence", "reader.highlight_word"):
            self._show_spoken()
        elif key == "playback.volume":
            self._sync_volume()

    # ------------------------------------------------------------------ building the list
    def _clear_widgets(self) -> None:
        while self._para_layout.count():
            item = self._para_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)  # deleteLater() alone leaves it painted until the event loop runs
                w.deleteLater()
        self._widgets = []

    def _rebuild(self) -> None:
        self._clear_widgets()
        self._timeline = self._timeline_key = None
        for p in self._model:
            w = ParagraphWidget(p.index, p.text)
            w.apply_theme(self._theme)
            w.body.set_font_spec(self._font, self._line_spacing)
            w.set_selectable(self._highlight_mode)
            w.clicked.connect(self._on_clicked)
            w.bookmark_toggled.connect(self._model.toggle_bookmark)
            w.selection_finished.connect(self._on_selection)
            w.context_requested.connect(self._on_context)
            self._widgets.append(w)
            self._para_layout.addWidget(w)
        if self._model.section_count > 1:
            nav = QWidget()
            row = QHBoxLayout(nav)
            row.setContentsMargins(37, 18, 17, 0)
            i = self._model.section_index
            prev = make_button("◀  Previous chapter", callback=lambda: self._playback.step_section(-1))
            nxt = make_button("Next chapter  ▶", callback=lambda: self._playback.step_section(1))
            prev.setEnabled(i > 0)
            nxt.setEnabled(i < self._model.section_count - 1)
            for b in (prev, nxt):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(prev)
            row.addStretch(1)
            row.addWidget(nxt)
            self._para_layout.addWidget(nav)
        self._para_layout.addStretch(1)
        self._para_layout.addSpacing(180)  # so the pill never covers the last paragraph
        for i in range(len(self._widgets)):
            self._refresh(i)
        self._update_header()
        self._refresh_bookmarks()

    def _style_for(self, i: int) -> str:
        p = self._model[i]
        if p.ignored:
            return "ignored"
        if p.skip:
            return "skipped"
        if i == self._model.current:
            return "playing" if self._state.is_active else "selected"
        return ""

    def _refresh(self, i: int) -> None:
        if 0 <= i < len(self._widgets):
            w, p = self._widgets[i], self._model[i]
            w.set_state(self._style_for(i))
            w.set_flags(p.bookmarked, p.highlighted)
            w.setVisible(not p.ignored or self._show_ignored)
            self._apply_highlights(i)
            if i == self._model.current:
                self._bookmark.set_icon_name("bookmark_filled" if p.bookmarked else "bookmark")

    def _apply_highlights(self, i: int) -> None:
        if not 0 <= i < len(self._widgets):
            return
        g = self._model.offset + i
        ranges = [(h.start, h.end, highlight_fill(h.color, self._theme)) for h in self._highlights.for_paragraph(g)]
        self._widgets[i].body.set_highlights(ranges)

    def _on_highlights_changed(self, g: int) -> None:
        if g < 0:
            for i in range(len(self._widgets)):
                self._apply_highlights(i)
        else:
            self._apply_highlights(g - self._model.offset)

    def _scroll_to(self, i: int) -> None:
        if 0 <= i < len(self._widgets):
            self._scroll.ensureWidgetVisible(self._widgets[i], 0, 170)

    def _update_header(self) -> None:
        doc: DocInfo | None = self._state.doc
        self._title.setText(doc.title if doc else "Untitled")
        m = self._model
        multi = m.section_count > 1
        self._section_label.setVisible(multi)
        if multi:
            self._section_label.setText(f"{m.sections[m.section_index].title}   ·   {m.section_index + 1} / {m.section_count}")
        n = len(m)
        if n:
            words = sum(len(t.split()) for t in m.all_texts())
            minutes = max(1, round(words / (160 * self._state.speed)))
            pages = f"{doc.page_count} page{'s' if doc.page_count != 1 else ''} · " if doc and doc.page_count and not multi else ""
            scope = " in this chapter" if multi else ""
            self._meta.setText(f"{pages}{n} paragraphs{scope} · {words:,} words · about {minutes} min at {speed_label(self._state.speed)}")
        else:
            self._meta.setText("")

    # ------------------------------------------------------------------ chapters
    def _populate_chapters(self) -> None:
        m = self._model
        self._chapter_list.clear()
        multi = m.section_count > 1
        for i, sec in enumerate(m.sections):
            item = QListWidgetItem(f"{i + 1}.  {sec.title}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._chapter_list.addItem(item)
        self._chapters_btn.setVisible(multi)
        self._chapters_export.setVisible(multi)
        if not multi and self._chapters_btn.isChecked():
            self._chapters_btn.setChecked(False)

    def _on_section_changed(self, index: int) -> None:
        if 0 <= index < self._chapter_list.count():
            self._chapter_list.setCurrentRow(index)
            self._chapter_list.scrollToItem(self._chapter_list.item(index))
        self._update_header()
        self._scroll.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------ reacting to the model / state
    def _on_current_changed(self, new: int, old: int) -> None:
        if 0 <= old < len(self._widgets):
            self._widgets[old].body.set_spoken(None, None)
        self._last_word_y = None
        self._refresh(old)
        self._refresh(new)
        self._scroll_to(new)
        self._show_spoken()

    def _on_paragraph_changed(self, i: int) -> None:
        self._refresh(i)
        self._refresh_bookmarks()

    def _update_playback_ui(self) -> None:
        pb = self._state.playback
        icon, text = {"playing": ("pause", " Pause"), "paused": ("resume", " Resume"), "loading": ("play", " Loading…")}.get(pb, ("play", " Listen"))
        self._listen.set_icon_name(icon)
        self._listen.setText(text)
        self._stop.setEnabled(pb != "stopped")
        self._refresh(self._model.current)
        if pb in ("playing", "paused"):
            self._show_spoken()
        else:
            for w in self._widgets:
                w.body.set_spoken(None, None)
            self._last_word_y = None

    # ------------------------------------------------------------------ live word / sentence highlight
    def _timeline_for(self, index: int) -> WordTimeline | None:
        if not 0 <= index < len(self._model):
            return None
        text = self._model[index].text
        speed = self._state.speed
        key = (index, self._model.offset, speed, text)
        if key != self._timeline_key:
            timings = None
            getter = getattr(self._playback.cache, "get_word_timings", None)
            if getter is not None:
                timings = getter(index, speed)
            self._timeline, self._timeline_key = WordTimeline(text, timings), key
        return self._timeline

    def _on_position(self, fraction: float) -> None:
        self._show_spoken(fraction)

    def _show_spoken(self, fraction: float | None = None) -> None:
        """Paint the sentence and word being spoken (each can be switched off in Settings)."""
        cur = self._model.current
        if self._state.playback not in ("playing", "paused") or not 0 <= cur < len(self._widgets):
            return
        tl = self._timeline_for(cur)
        if tl is None:
            return
        if fraction is None:
            fraction = self._playback.player.fraction
        wi, si = tl.word_at(fraction), tl.sentence_at(fraction)
        word = tl.word_span(wi) if self._settings.highlight_word else None
        sentence = tl.sentence_span(si) if self._settings.highlight_sentence else None
        w = self._widgets[cur]
        w.body.set_spoken(sentence, word)
        if word:
            self._keep_visible(w, word)

    def _keep_visible(self, w: ParagraphWidget, span: tuple[int, int]) -> None:
        """In a long paragraph, scroll so the word being spoken doesn't leave the screen (only when it changes line)."""
        rect = w.body.rect_of_range(*span)
        if rect is None:
            return
        x, y, _w, h = rect
        if self._last_word_y is not None and abs(y - self._last_word_y) < 1:
            return
        self._last_word_y = y
        top_left = w.body.mapTo(self._scroll.widget(), QPoint(int(x), int(y)))
        self._scroll.ensureVisible(top_left.x(), top_left.y() + int(h), 0, 190)

    def _update_speed_buttons(self) -> None:
        b = self._speed_buttons.get(self._state.speed)
        if b is not None:
            b.setChecked(True)
        self._update_header()

    # ------------------------------------------------------------------ user actions
    def _on_clicked(self, i: int, char: int = -1) -> None:
        """Click a paragraph to play it; click a word to play from that word."""
        self.setFocus()
        fraction = 0.0
        if char >= 0:
            tl = self._timeline_for(i)
            if tl is not None:
                fraction = tl.fraction_at_char(char)
        self._playback.play_from(i, fraction=fraction)

    def _on_selection(self, i: int, start: int, end: int, global_pos: QPoint) -> None:
        """A drag across text finished: offer the colour palette."""
        if not self._highlight_mode:
            self._widgets[i].body.set_selection(None)
            return
        if self._palette is not None:
            self._palette.close()
        pal = HighlightPalette(self)
        self._palette = pal
        text = self._model[i].text
        pal.color_chosen.connect(lambda name, i=i, s=start, e=end: self._apply_highlight(i, s, e, name))
        pal.copy_requested.connect(lambda: QGuiApplication.clipboard().setText(text[start:end]))
        pal.closed.connect(lambda i=i: self._selection_closed(i))
        pal.show_above(global_pos)

    def _selection_closed(self, i: int) -> None:
        if 0 <= i < len(self._widgets):
            self._widgets[i].body.set_selection(None)
        self._palette = None

    def _apply_highlight(self, i: int, start: int, end: int, color: str) -> None:
        self._highlights.add(self._model.offset + i, start, end, color, len(self._model[i].text))

    def _play_current(self) -> None:
        if self._model.current >= 0:
            self._playback.play_from(self._model.current)

    def _bookmark_current(self) -> None:
        if self._model.current >= 0:
            self._model.toggle_bookmark(self._model.current)

    def _on_context(self, i: int, pos: QPoint, char: int = -1) -> None:
        p = self._model[i]
        g = self._model.offset + i
        hit = self._highlights.at(g, char) if char >= 0 else None
        if hit is not None:  # right-click on a highlight: Change color / Remove / Copy
            menu = build_highlight_menu(
                self, hit.color,
                on_color=lambda name, hid=hit.id: self._highlights.set_color(hid, name),
                on_remove=lambda hid=hit.id: self._highlights.remove(hid),
                on_copy=lambda h=hit: QGuiApplication.clipboard().setText(p.text[h.start:h.end]))
            menu.exec(pos)
            return
        menu = QMenu(self)
        self._add(menu, "Play from here", lambda: self._on_clicked(i, char))
        self._add(menu, "Copy", lambda: QGuiApplication.clipboard().setText(p.text))
        menu.addSeparator()
        self._add(menu, "Remove bookmark" if p.bookmarked else "Bookmark", lambda: self._model.toggle_bookmark(i))
        if self._highlights.for_paragraph(g):
            self._add(menu, "Remove highlights", lambda: self._highlights.clear_paragraph(g))
        else:
            self._add(menu, "Highlight paragraph", lambda: self._highlights.add(g, 0, len(p.text), None, len(p.text)))
        menu.addSeparator()
        self._add(menu, "Don't skip" if p.skip else "Skip", lambda: self._model.toggle_skip(i))
        self._add(menu, "Ignore this paragraph", lambda: self._ignore(i))
        menu.exec(pos)

    def _ignore(self, i: int) -> None:
        self._model.ignore(i)
        self._playback.handle_ignored(i)

    def _toggle_show_ignored(self, checked: bool) -> None:
        self._show_ignored = bool(checked)
        for i in range(len(self._widgets)):
            self._refresh(i)

    def _toggle_theme(self) -> None:
        self._settings.theme = "light" if self._settings.theme == "dark" else "dark"

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText("\n\n".join(self._model.active_texts()))

    # ------------------------------------------------------------------ bookmarks panel
    def _on_panel_toggled(self, which: str, on: bool) -> None:
        other = self._chapters_btn if which == "bookmarks" else self._panel_btn
        if on:
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
            self._side_stack.setCurrentIndex(1 if which == "bookmarks" else 0)
            self._side_title.setText("Bookmarks" if which == "bookmarks" else "Chapters")
        self._side.setVisible(self._panel_btn.isChecked() or self._chapters_btn.isChecked())
        if self._act_panel.isChecked() != self._panel_btn.isChecked():
            self._act_panel.setChecked(self._panel_btn.isChecked())

    def _refresh_bookmarks(self) -> None:
        self._bm_list.clear()
        marks = self._model.bookmarks()
        self._side_empty.setVisible(not marks)
        for g in marks:
            title, number, text = self._model.describe(g)
            snippet = text if len(text) <= 60 else text[:57] + "…"
            where = f"{title} · " if title else ""
            item = QListWidgetItem(f"{where}¶ {number}   {snippet}")
            item.setData(Qt.ItemDataRole.UserRole, g)
            self._bm_list.addItem(item)

    def _on_bookmark_clicked(self, item: QListWidgetItem) -> None:
        self.setFocus()
        self._playback.jump_to_global(item.data(Qt.ItemDataRole.UserRole))

    # ------------------------------------------------------------------ voices menu
    def _fill_voices_menu(self) -> None:
        menu = self._voices_menu
        menu.clear()
        current = self._settings.voice
        last_group = None
        for v in self._catalog.list():
            if v["group"] != last_group:
                menu.addSection(v["group"])
                last_group = v["group"]
            act = self._add(menu, v["label"], lambda vid=v["id"]: self.voice_selected.emit(vid), checkable=True)
            act.setChecked(v["id"] == current)
        menu.addSeparator()
        self._add(menu, "Manage voices…", self.manage_voices_requested.emit)
