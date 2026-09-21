"""Home: heading, universal input, workflow tabs and cards."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QPlainTextEdit, QScrollArea, QStackedWidget,
                             QVBoxLayout, QWidget)

from core.settings import SettingsManager
from .theme import SPACING, apply_card_shadow
from .widgets import make_button, make_label


class HomeView(QWidget):
    upload_requested = pyqtSignal()
    regions_first_requested = pyqtSignal()
    paste_text_requested = pyqtSignal()
    paste_link_requested = pyqtSignal()
    text_to_audio_requested = pyqtSignal()
    import_voice_requested = pyqtSignal()
    library_requested = pyqtSignal()
    input_submitted = pyqtSignal(str)

    LINK_DISABLED_TIP = "Enable Internet Usage in Settings."

    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._cards: list[QFrame] = []
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setObjectName("page")
        scroll.setWidget(inner)
        row = QHBoxLayout(inner)
        row.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(860)
        col.setMinimumWidth(520)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(32, 40, 32, 40)
        lay.setSpacing(SPACING["section_gap"])
        row.addWidget(col, 10)
        row.addStretch(1)

        heading = make_label("What do you want to listen to today?", "h1", wrap=True)
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(heading)

        self._input = QPlainTextEdit()
        self._input.setObjectName("universal")
        self._input.setPlaceholderText("Paste text, drop a file, or paste link…")
        self._input.setFixedHeight(120)
        self._input.setAcceptDrops(False)  # let file drops reach the window
        lay.addWidget(self._input)
        actions = QHBoxLayout()
        actions.addStretch(1)
        mic = make_button("🎤", "round", tooltip="Dictation isn't included in this offline app")
        mic.setEnabled(False)
        actions.addWidget(mic)
        actions.addWidget(make_button("＋", "round", self.upload_requested.emit, "Add a file"))
        actions.addWidget(make_button("↑", "primary", self._submit, "Read this"))
        lay.addLayout(actions)

        lay.addSpacing(8)
        lay.addWidget(make_label("Workflows", "h2"))
        tabs = QHBoxLayout()
        group = QButtonGroup(self)
        group.setExclusive(True)
        panels = QStackedWidget()
        for k, name in enumerate(("Listen to Text", "Create", "Study")):
            b = make_button(name, "tab")
            b.setCheckable(True)
            b.setChecked(k == 0)
            group.addButton(b, k)
            b.clicked.connect(lambda checked=False, k=k: panels.setCurrentIndex(k))
            tabs.addWidget(b)
        tabs.addStretch(1)
        lay.addLayout(tabs)

        # Listen to Text
        sources = QHBoxLayout()
        sources.setSpacing(16)
        drop = make_button("⬆   Drag && Drop File", "sourcecard", self.upload_requested.emit)
        paste = make_button("✎   Paste Text", "sourcecard", self.paste_text_requested.emit)
        self._link_card = make_button("🔗   Paste Link\n(optional)", "sourcecard", self.paste_link_requested.emit)
        for b in (drop, paste, self._link_card):
            b.setMinimumHeight(96)
            sources.addWidget(b)
        src = QWidget()
        src.setLayout(sources)
        sources.setContentsMargins(0, 0, 0, 0)
        panels.addWidget(self._panel([
            self._card("🎧  Read Aloud", "Upload any file to listen — PDF, image or text.", "Upload Files", self.upload_requested.emit),
            src,
        ]))
        # Create
        panels.addWidget(self._panel([
            self._card("Text to audio file", "Paste some text and save the narration as a WAV or MP3.", "Add text", self.text_to_audio_requested.emit),
            self._card("Import a voice pack", "Add extra Kokoro voices (.npy / .bin style arrays or .json blends).", "Import voice", self.import_voice_requested.emit),
        ]))
        # Study
        panels.addWidget(self._panel([
            self._card("Pick regions first", "Open a PDF or image and choose what to read — skip headers, footnotes and margins.", "Choose file", self.regions_first_requested.emit),
            self._card("Pick up where you left off", "Reopen anything you've read; EchoRead resumes at your last paragraph.", "Open Library", self.library_requested.emit),
        ]))
        lay.addWidget(panels)
        lay.addStretch(1)

        self.set_internet_enabled(settings.internet_usage)
        self.apply_theme(settings.theme)

    # -- building blocks
    def _card(self, title: str, subtitle: str, button: str, callback) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(*[SPACING["card_padding"]] * 4)
        text = QVBoxLayout()
        text.addWidget(make_label(title, "cardtitle"))
        text.addWidget(make_label(subtitle, "muted", wrap=True))
        lay.addLayout(text, 1)
        lay.addWidget(make_button(button, "primary", callback))
        self._cards.append(card)
        return card

    @staticmethod
    def _panel(widgets: list[QWidget]) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        for x in widgets:
            v.addWidget(x)
        v.addStretch(1)
        return w

    # -- behaviour
    def _submit(self) -> None:
        text = self._input.toPlainText().strip()
        if text:
            self._input.clear()
            self.input_submitted.emit(text)

    def set_internet_enabled(self, enabled: bool) -> None:
        self._link_card.setEnabled(enabled)
        self._link_card.setToolTip("" if enabled else self.LINK_DISABLED_TIP)

    def apply_theme(self, theme: str) -> None:
        for card in self._cards:
            apply_card_shadow(card, theme == "light")
