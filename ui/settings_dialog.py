"""Settings: general, reader font (live), highlighting, profile, cache, and the Internet Usage switch."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QScrollArea, QSlider, QVBoxLayout, QWidget)

from core.models import SPEEDS, speed_label
from core.profile import ProfileManager
from core.settings import SettingsManager
from core.tts import VoiceCatalog
from core.voice_cache import VoiceCache
from .onboarding import AvatarColorPicker, AvatarEditor
from .theme import FontLibrary, qcolor, tokens
from .widgets import ParagraphText, make_button, make_label


class SettingsDialog(QDialog):
    OCR_LANGS = ["en", "ch", "chinese_cht", "japan", "korean", "fr", "german"]
    PREVIEW = ("The quick brown fox jumps over the lazy dog. Comfortable reading depends on the typeface, its size "
               "and the space between the lines.")
    READER_KEYS = ("font_family", "font_size", "line_spacing", "highlight_sentence", "highlight_word")

    def __init__(self, settings: SettingsManager, cache: VoiceCache, catalog: VoiceCatalog, profile: ProfileManager,
                 fonts: FontLibrary | None = None, parent=None, focus: str | None = None):
        super().__init__(parent)
        self._settings = settings
        self._cache = cache
        self._profile = profile
        self._fonts = fonts or FontLibrary()
        self._initial = {k: getattr(settings, k) for k in self.READER_KEYS}  # Cancel puts these back (they change live)
        self.setWindowTitle("Settings")
        self.resize(640, 780)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(12)
        outer.addWidget(make_label("Settings", "h2"))
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("settingsbody")
        self._scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)
        outer.addWidget(self._scroll, 1)

        # ---------------------------------------------------------------- general
        form = QFormLayout()
        form.setSpacing(10)
        self._theme = QComboBox()
        self._theme.addItem("Dark", "dark")
        self._theme.addItem("Light", "light")
        self._theme.setCurrentIndex(0 if settings.theme == "dark" else 1)
        self._voice = QComboBox()
        for v in catalog.list():
            self._voice.addItem(f"{v['label']}  ({v['group']})", v["id"])
        self._voice.setCurrentIndex(max(self._voice.findData(settings.voice), 0))
        self._speed = QComboBox()
        for sp in SPEEDS:
            self._speed.addItem(speed_label(sp), sp)
        self._speed.setCurrentIndex(max(self._speed.findData(settings.speed), 0))
        self._device = QComboBox()
        self._device.addItems(["CPU", "GPU"])
        self._device.setCurrentText(settings.device)
        self._ocr_engine = QComboBox()
        self._ocr_engine.addItem("Auto — use a PDF's own text when it has any, otherwise OCR", "auto")
        self._ocr_engine.addItem("Always OCR with PaddleOCR", "ocr")
        self._ocr_engine.setCurrentIndex(0 if settings.ocr_engine == "auto" else 1)
        self._ocr_lang = QComboBox()
        self._ocr_lang.setEditable(True)
        self._ocr_lang.addItems(self.OCR_LANGS)
        self._ocr_lang.setCurrentText(settings.ocr_lang)
        form.addRow("Theme", self._theme)
        form.addRow("Default voice", self._voice)
        form.addRow("Default speed", self._speed)
        form.addRow("Device", self._device)
        form.addRow("Default OCR engine", self._ocr_engine)
        form.addRow("OCR language", self._ocr_lang)
        lay.addLayout(form)
        lay.addWidget(make_label("Device and OCR language take effect after restarting EchoRead.", "muted", wrap=True))

        # ---------------------------------------------------------------- reading font (applies live)
        self._font_box = QWidget()
        fb = QVBoxLayout(self._font_box)
        fb.setContentsMargins(0, 8, 0, 0)
        fb.setSpacing(8)
        fb.addWidget(make_label("Reading font", "cardtitle"))
        self._font_list = QListWidget()
        self._font_list.setObjectName("fontlist")
        self._font_list.setFixedHeight(190)
        for opt in self._fonts.options():
            item = QListWidgetItem(opt["label"] if opt["installed"] else f"{opt['label']}  (not installed on this PC)")
            item.setData(Qt.ItemDataRole.UserRole, opt["label"])
            item.setFont(FontLibrary.make_font(opt["label"], 17))
            self._font_list.addItem(item)
        self._select_font(settings.font_family)
        fb.addWidget(self._font_list)
        size_row = QHBoxLayout()
        lbl = make_label("Text size")
        lbl.setMinimumWidth(90)
        size_row.addWidget(lbl, 0)
        self._size = QSlider(Qt.Orientation.Horizontal)
        lo, hi = settings.FONT_SIZE_RANGE
        self._size.setRange(lo, hi)
        self._size.setValue(settings.font_size)
        self._size_label = make_label(f"{settings.font_size} px", "muted")
        self._size_label.setMinimumWidth(48)
        size_row.addWidget(self._size, 1)
        size_row.addWidget(self._size_label)
        fb.addLayout(size_row)
        space_row = QHBoxLayout()
        lbl2 = make_label("Line spacing")
        lbl2.setMinimumWidth(90)
        space_row.addWidget(lbl2, 0)
        self._spacing = QSlider(Qt.Orientation.Horizontal)
        self._spacing.setRange(24, 40)  # 1.20 .. 2.00 in steps of 0.05
        self._spacing.setValue(round(settings.line_spacing * 20))
        self._spacing_label = make_label(f"{settings.line_spacing:.2f}", "muted")
        self._spacing_label.setMinimumWidth(48)
        space_row.addWidget(self._spacing, 1)
        space_row.addWidget(self._spacing_label)
        fb.addLayout(space_row)
        self._preview = ParagraphText(self.PREVIEW)
        self._preview_frame = QFrame()
        self._preview_frame.setObjectName("card")
        pv = QVBoxLayout(self._preview_frame)
        pv.setContentsMargins(18, 14, 18, 14)
        pv.addWidget(self._preview)
        fb.addWidget(self._preview_frame)
        lay.addWidget(self._font_box)

        # ---------------------------------------------------------------- highlighting while reading
        self._hl_sentence = QCheckBox("Highlight Sentence — softly mark the sentence being read")
        self._hl_sentence.setChecked(settings.highlight_sentence)
        self._hl_word = QCheckBox("Highlight Word — mark the exact word being spoken")
        self._hl_word.setChecked(settings.highlight_word)
        lay.addWidget(self._hl_sentence)
        lay.addWidget(self._hl_word)

        # ---------------------------------------------------------------- profile
        lay.addSpacing(4)
        lay.addWidget(make_label("Edit Profile", "cardtitle"))
        prow = QHBoxLayout()
        self._editor = AvatarEditor(profile, 72)
        prow.addWidget(self._editor)
        pcol = QVBoxLayout()
        self._name = QLineEdit(profile.name)
        self._name.setPlaceholderText("Your name")
        self._name.setMaxLength(ProfileManager.MAX_NAME)
        self._picker = AvatarColorPicker(current=profile.avatar_color)
        pcol.addWidget(self._name)
        pcol.addWidget(self._picker)
        prow.addLayout(pcol, 1)
        lay.addLayout(prow)
        lay.addWidget(make_label("Your name is only used on this computer. It is never sent anywhere.", "muted", wrap=True))
        self._update_avatar()

        # ---------------------------------------------------------------- audio cache / internet
        lay.addSpacing(4)
        lay.addWidget(make_label("Precache speeds", "cardtitle"))
        lay.addWidget(make_label(
            "The speed you are listening at is always prepared in the background. Tick other speeds to have them ready too, "
            "so switching to them is instant. Speeds you leave unticked are made the first time you switch to them (a short wait).",
            "muted", wrap=True))
        speeds_row = QHBoxLayout()
        self._precache_boxes: dict[float, QCheckBox] = {}
        for sp in SPEEDS:
            box = QCheckBox(speed_label(sp))
            box.setChecked(sp in settings.precache_speeds)
            box.toggled.connect(lambda _on: self._update_precache_note())
            self._precache_boxes[sp] = box
            speeds_row.addWidget(box)
        speeds_row.addStretch(1)
        lay.addLayout(speeds_row)
        self._precache_note = make_label("", "muted", wrap=True)
        self._precache_warn = make_label("", "warn", wrap=True)
        lay.addWidget(self._precache_note)
        lay.addWidget(self._precache_warn)
        self._update_precache_note()
        limit_row = QHBoxLayout()
        limit_row.addWidget(make_label("Keep the audio cache under", "muted"))
        self._limit = QComboBox()
        for label, mb in (("1 GB", 1024), ("2 GB", 2048), ("5 GB", 5120), ("10 GB", 10240), ("20 GB", 20480), ("No limit", 0)):
            self._limit.addItem(label, mb)
        self._limit.setCurrentIndex(max(self._limit.findData(settings.cache_limit_mb), 2))
        self._limit.setToolTip("When the cache grows past this, the chapters you read longest ago are deleted first.")
        limit_row.addWidget(self._limit)
        limit_row.addStretch(1)
        lay.addLayout(limit_row)
        cache_row = QHBoxLayout()
        self._cache_label = make_label("", "muted")
        cache_row.addWidget(self._cache_label, 1)
        cache_row.addWidget(make_button("Clear cache", callback=self._clear_cache, tooltip="Deletes all cached audio. The open document keeps working; it is re-cached the next time you open it."))
        lay.addLayout(cache_row)
        self._update_cache_label()

        lay.addSpacing(6)
        self._internet = QCheckBox("Internet Usage")
        self._internet.setChecked(settings.internet_usage)
        lay.addWidget(self._internet)
        lay.addWidget(make_label(
            "Off by default. When on, Paste Link can fetch the page you paste (EchoRead also reads that site's robots.txt "
            "and skips pages it disallows). No trackers, no telemetry: the only requests are to the site you paste. "
            "Speech and OCR models download once on first use regardless of this switch.",
            "muted", wrap=True))
        lay.addStretch(1)

        row = QHBoxLayout()
        row.addWidget(make_button("Reset", callback=self._reset))
        row.addStretch(1)
        row.addWidget(make_button("Cancel", callback=self.reject))
        row.addWidget(make_button("Save", "primary", self._save))
        outer.addLayout(row)

        # live controls
        self._font_list.currentItemChanged.connect(self._font_chosen)
        self._size.valueChanged.connect(self._size_changed)
        self._spacing.valueChanged.connect(self._spacing_changed)
        self._hl_sentence.toggled.connect(lambda on: setattr(settings, "highlight_sentence", on))
        self._hl_word.toggled.connect(lambda on: setattr(settings, "highlight_word", on))
        self._name.textChanged.connect(lambda _t: self._update_avatar())
        self._picker.color_changed.connect(lambda _c: self._update_avatar())
        self._update_preview()
        if focus == "font":
            self.focus_font()

    # ------------------------------------------------------------------ reading font
    def focus_font(self) -> None:
        """Scroll to the reading-font section and put the cursor in its list (Ctrl+F)."""
        self._scroll.ensureWidgetVisible(self._font_box)
        self._font_list.setFocus()

    def _select_font(self, label: str) -> None:
        for i in range(self._font_list.count()):
            if self._font_list.item(i).data(Qt.ItemDataRole.UserRole) == label:
                self._font_list.setCurrentRow(i)
                return
        if self._font_list.count():
            self._font_list.setCurrentRow(0)

    def _font_chosen(self, item, _previous=None) -> None:
        if item is not None:
            self._settings.font_family = item.data(Qt.ItemDataRole.UserRole)
            self._update_preview()

    def _size_changed(self, value: int) -> None:
        self._settings.font_size = value
        self._size_label.setText(f"{value} px")
        self._update_preview()

    def _spacing_changed(self, value: int) -> None:
        self._settings.line_spacing = value / 20
        self._spacing_label.setText(f"{value / 20:.2f}")
        self._update_preview()

    def _update_preview(self) -> None:
        s = self._settings
        self._preview.set_font_spec(FontLibrary.make_font(s.font_family, s.font_size), s.line_spacing)
        self._preview.set_text_color(qcolor(tokens(s.theme)["body"]))

    # ------------------------------------------------------------------ profile
    def _update_avatar(self) -> None:
        self._editor.set_identity(ProfileManager.clean_name(self._name.text()), self._picker.color())

    # ------------------------------------------------------------------ precache speeds
    WARN_FROM = 4  # this many speeds ticked or more: warn

    def chosen_precache_speeds(self) -> list[float]:
        return [sp for sp, box in self._precache_boxes.items() if box.isChecked()]

    def _update_precache_note(self) -> None:
        n = len(self.chosen_precache_speeds())
        if n >= self.WARN_FROM:
            self._precache_note.setText("")
            self._precache_warn.setText(
                f"Warning: preparing {n} speeds means about {n}x the background work and disk space. It can slow EchoRead "
                "down while a long document is prepared and fills the audio cache much faster. One or two speeds is usually enough.")
        else:
            self._precache_warn.setText("")
            self._precache_note.setText(
                "Nothing extra is prepared. Only the speed you are listening at." if n == 0 else
                "Light on your PC and disk." if n == 1 else f"Moderate: about {n}x the background work and disk space.")
        self._precache_note.setVisible(bool(self._precache_note.text()))
        self._precache_warn.setVisible(bool(self._precache_warn.text()))

    # ------------------------------------------------------------------ cache
    def _update_cache_label(self) -> None:
        self._cache_label.setText(f"Audio cache: {self._cache.size_bytes() / (1024 * 1024):.1f} MB")

    def _clear_cache(self) -> None:
        self._cache.clear_all()
        self._update_cache_label()

    # ------------------------------------------------------------------ buttons
    def _save(self) -> None:
        s = self._settings
        s.theme = self._theme.currentData()
        s.voice = self._voice.currentData()
        s.speed = self._speed.currentData()
        s.device = self._device.currentText()
        s.ocr_engine = self._ocr_engine.currentData()
        s.ocr_lang = self._ocr_lang.currentText()
        s.precache_speeds = self.chosen_precache_speeds()
        s.cache_limit_mb = self._limit.currentData()
        s.internet_usage = self._internet.isChecked()
        name = ProfileManager.clean_name(self._name.text())
        if name:
            self._profile.save(name, self._picker.color())
            self._editor.apply()
        elif self._profile.has_profile:
            self._profile.clear()  # an emptied name goes back to "Set up profile" (and drops the picture)
        self.accept()

    def reject(self) -> None:
        for key, value in self._initial.items():  # font and highlight choices were applied live: undo them
            setattr(self._settings, key, value)
        super().reject()

    def _reset(self) -> None:
        self._settings.reset()  # everything except the profile
        self.accept()