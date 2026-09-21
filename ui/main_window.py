"""Root window: sidebar, pages, status bar, and the open-a-document flows."""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                             QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                             QStackedWidget, QVBoxLayout, QWidget)

from core.errors import get_logger
from core.library import Library
from core.models import DocInfo
from core.paragraphs import ParagraphModel
from core.paths import AppPaths
from core.services import Services
from core.workers import TaskWorker
from .error_dialog import ErrorDialog
from .home_view import HomeView
from .library_view import LibraryView
from .onboarding import OnboardingDialog
from .reader_view import ReaderView
from .regions_dialog import RegionsDialog
from .settings_dialog import SettingsDialog
from .theme import FontLibrary, build_stylesheet
from .toast import Toast
from .voices_view import VoicesView
from .widgets import ShortcutHelpDialog, make_button, make_label, paint_avatar

PAGES = {"home": 0, "reader": 1, "library": 2, "voices": 3}


class MainWindow(QMainWindow):
    def __init__(self, services: Services):
        super().__init__()
        self.s = services
        self._job: TaskWorker | None = None
        self._preview: TaskWorker | None = None
        self._file_doc: DocInfo | None = None  # the PDF/image currently open in the loader
        self._loading = False  # True while a document is being put into the reader
        self._downloading = False  # the first-run speech-model download is in progress
        self._crash_open = False
        self.fonts = FontLibrary()
        self.fonts.load()  # the fonts EchoRead ships (Inter, Lora, ...) must be registered before any widget uses them
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)
        self._precache_timer = QTimer(self)
        self._precache_timer.setSingleShot(True)
        self._precache_timer.timeout.connect(lambda: self._precache_label.setText("") or self._update_status_bar())

        self.setWindowTitle("EchoRead")
        self.resize(1240, 820)
        icon = AppPaths.assets_dir() / "icons" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setAcceptDrops(True)
        self._build()
        self._wire()
        self._on_internet_toggled(self.s.settings.internet_usage)
        self._refresh_recent()
        self._show_page("home")
        self.s.errors.attach_ui(True)  # unhandled exceptions now become a friendly dialog

    # ------------------------------------------------------------------ construction
    def _build(self) -> None:
        s = self.s
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setSpacing(0)
        self._topbar = self._build_topbar()
        right.addWidget(self._topbar)
        self._stack = QStackedWidget()
        self.home = HomeView(s.settings)
        self.reader = ReaderView(s.settings, s.state, s.model, s.playback, s.catalog, s.highlights)
        self.library_view = LibraryView(s.library)
        self.voices_view = VoicesView(s.catalog, s.settings)
        for page in (self.home, self.reader, self.library_view, self.voices_view):
            self._stack.addWidget(page)
        right.addWidget(self._stack, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_statusbar())
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.pick_file)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.show_settings)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.show_settings("font"))
        self._toast = Toast(root)

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(250)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(6)
        add = make_button("＋  Add", "primary")
        menu = QMenu(add)
        menu.addAction("Upload file…").triggered.connect(lambda: self.pick_file())
        menu.addAction("Paste text…").triggered.connect(lambda: self.add_text())
        self._act_add_link = menu.addAction("Paste link…")
        self._act_add_link.triggered.connect(lambda: self.paste_link())
        add.setMenu(menu)
        lay.addWidget(add)
        lay.addSpacing(10)
        self._nav: dict[str, object] = {}
        for key, text in (("home", "✦   New Task"), ("library", "▤   Library"), ("voices", "♪   Voices")):
            b = make_button(text, "nav", lambda k=key: self._show_page(k))
            self._nav[key] = b
            lay.addWidget(b)
        lay.addWidget(make_button("⚙   Settings", "nav", self.show_settings))
        lay.addSpacing(12)
        tabs = QHBoxLayout()
        self._side_tabs: dict[str, object] = {}
        group = QButtonGroup(side)
        for key, text in (("task", "Tasks"), ("file", "Files")):
            b = make_button(text, "tab")
            b.setCheckable(True)
            b.setChecked(key == "task")
            group.addButton(b)
            b.clicked.connect(lambda checked=False: self._refresh_recent())
            self._side_tabs[key] = b
            tabs.addWidget(b)
        tabs.addStretch(1)
        lay.addLayout(tabs)
        self._recent_empty = make_label("Your recent tasks will appear here", "muted", wrap=True)
        lay.addWidget(self._recent_empty)
        self._recent = QListWidget()
        self._recent.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._recent.itemClicked.connect(lambda item: self.open_library_entry(item.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self._recent, 1)
        prof = QHBoxLayout()
        self._avatar = make_label("", "avatar")
        self._profile_name = make_label("", "cardtitle")
        self._setup_profile = make_button("Set up profile", "nav", self.run_profile_setup)
        prof.addWidget(self._avatar)
        prof.addWidget(self._profile_name)
        prof.addWidget(self._setup_profile, 1)
        prof.addStretch(1)
        lay.addLayout(prof)
        self._refresh_profile()
        return side

    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 20, 6)
        logo = QLabel()
        icon = AppPaths.assets_dir() / "icons" / "icon.png"
        if icon.exists():
            logo.setPixmap(QPixmap(str(icon)).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(logo)
        lay.addWidget(make_label("EchoRead", "logo"))
        lay.addStretch(1)
        lay.addWidget(make_button("⚙", "round", self.show_settings, "Settings (Ctrl+,)"))
        self._top_avatar = make_label("", "avatar")
        lay.addWidget(self._top_avatar)
        return bar

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("statusbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(14)
        self._status_label = make_label("", "muted")
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setMinimumWidth(240)
        self._cancel_btn = make_button("Cancel", callback=self.cancel_job)
        self._precache_label = make_label("", "muted")
        lay.addWidget(self._status_label, 1)
        lay.addWidget(self._progress)
        lay.addWidget(self._cancel_btn)
        lay.addWidget(self._precache_label)
        self._statusbar = bar
        self._update_status_bar()
        return bar

    def _wire(self) -> None:
        s, r, h = self.s, self.reader, self.home
        # theme / settings
        s.settings.theme_changed.connect(self._apply_theme)
        s.settings.internet_toggled.connect(self._on_internet_toggled)
        s.settings.changed.connect(self._on_setting_changed)
        s.profile.changed.connect(self._refresh_profile)
        # problems and warnings
        s.errors.error_occurred.connect(self._show_crash)
        s.player.device_unavailable.connect(lambda reason: self.toast(
            "No sound output was found, so EchoRead is playing silently. Plug in or enable a speaker and press Listen again."))
        s.cache.disk_full.connect(self.toast)
        s.tts.download_progress.connect(self._on_download_progress)
        r.message_requested.connect(self.toast)
        # home
        h.upload_requested.connect(lambda: self.pick_file())
        h.regions_first_requested.connect(lambda: self.pick_file(regions_first=True))
        h.paste_text_requested.connect(lambda: self.add_text())
        h.paste_link_requested.connect(lambda: self.paste_link())
        h.text_to_audio_requested.connect(lambda: self.add_text(export_after=True))
        h.import_voice_requested.connect(self.import_voice_pack)
        h.library_requested.connect(lambda: self._show_page("library"))
        h.input_submitted.connect(self.submit_input)
        # reader
        r.open_file_requested.connect(lambda: self.pick_file())
        r.add_text_requested.connect(lambda: self.add_text())
        r.paste_link_requested.connect(lambda: self.paste_link())
        r.home_requested.connect(lambda: self._show_page("home"))
        r.regions_requested.connect(self.edit_regions)
        r.rerun_requested.connect(self.rerun_extraction)
        r.clear_regions_requested.connect(self._clear_regions)
        r.settings_requested.connect(self.show_settings)
        r.help_requested.connect(self.show_help)
        r.manage_voices_requested.connect(lambda: self._show_page("voices"))
        r.export_text_requested.connect(self.export_text)
        r.export_audio_requested.connect(self.export_audio)
        r.export_folder_requested.connect(self.export_folder)
        r.export_chapters_requested.connect(self.export_chapters)
        r.voice_selected.connect(lambda vid: setattr(s.settings, "voice", vid))
        # library / voices
        self.library_view.open_requested.connect(self.open_library_entry)
        self.voices_view.use_requested.connect(lambda vid: setattr(s.settings, "voice", vid))
        self.voices_view.preview_requested.connect(self.preview_voice)
        self.voices_view.import_requested.connect(self.import_voice_pack)
        self.voices_view.delete_requested.connect(self._delete_pack)
        # persistence of reading state
        s.model.current_changed.connect(lambda new, _old: self._save_position())
        s.model.section_changed.connect(self._on_section_changed)
        s.state.speed_changed.connect(lambda _sp: self._save_position())
        s.model.flags_changed.connect(self._save_flags)
        s.library.changed.connect(self._refresh_recent)
        # background services
        s.cache.progress.connect(self._on_precache_progress)
        s.playback.error.connect(lambda msg: self._error("Playback problem", msg))
        s.player.error.connect(lambda msg: self._show_status(f"Audio problem: {msg}", 8000))
        s.tts.status.connect(lambda text: self._show_status(text, 9000))
        s.ocr.status.connect(lambda text: self._show_status(text, 9000))

    # ------------------------------------------------------------------ pages / theme / chrome
    def _show_page(self, name: str) -> None:
        self._stack.setCurrentIndex(PAGES[name])
        self._topbar.setVisible(name != "reader")
        for key, btn in self._nav.items():
            btn.setProperty("active", "true" if key == name else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if name == "reader":
            self.reader.focus_reader()
        elif name == "voices":
            self.voices_view.refresh()

    def _apply_theme(self, name: str) -> None:
        QApplication.instance().setStyleSheet(build_stylesheet(name))
        self.home.apply_theme(name)
        self.reader.apply_theme(name)

    def _refresh_profile(self) -> None:
        """Sidebar bottom: avatar + name once you have a profile, otherwise a 'Set up profile' button."""
        p = self.s.profile
        has = p.has_profile
        self._avatar.setVisible(has)
        self._profile_name.setVisible(has)
        self._setup_profile.setVisible(not has)
        if has:
            paint_avatar(self._avatar, p.name, p.avatar_color, 32, p.avatar_image_path)
            self._profile_name.setText(p.name)
        if hasattr(self, "_top_avatar"):
            self._top_avatar.setVisible(has)
            if has:
                paint_avatar(self._top_avatar, p.name, p.avatar_color, 32, p.avatar_image_path)

    def run_first_time_setup(self) -> None:
        """Show the welcome dialog on the very first launch (main.py calls this once the window is up)."""
        if self.s.profile.needs_onboarding:
            self.run_profile_setup()

    def run_profile_setup(self) -> None:
        OnboardingDialog(self.s.profile, self).exec()
        self._refresh_profile()

    def toast(self, text: str, ms: int = 4500) -> None:
        self._toast.show_message(text, ms)

    def _refresh_recent(self) -> None:
        if not hasattr(self, "_recent"):
            return
        kind = "file" if self._side_tabs["file"].isChecked() else "task"
        entries = self.s.library.entries(kind)[:14]
        self._recent.clear()
        self._recent_empty.setText("Your recent files will appear here" if kind == "file" else "Your recent tasks will appear here")
        self._recent_empty.setVisible(not entries)
        for e in entries:
            item = QListWidgetItem(e["title"])
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            self._recent.addItem(item)

    def _on_internet_toggled(self, enabled: bool) -> None:
        self.home.set_internet_enabled(enabled)
        self.reader.set_internet_enabled(enabled)
        self._act_add_link.setEnabled(enabled)

    def _on_setting_changed(self, key: str) -> None:
        if key in ("voice", "precache_speeds"):
            self._restart_cache()
            if key == "voice":
                self.s.playback.restart_current()

    # ------------------------------------------------------------------ status bar
    def _update_status_bar(self) -> None:
        running = self._job is not None
        self._progress.setVisible(running or self._downloading)
        self._cancel_btn.setVisible(running)
        has_text = bool(self._status_label.text()) or bool(self._precache_label.text())
        self._statusbar.setVisible(running or self._downloading or has_text)

    def _show_status(self, text: str, ms: int = 5000) -> None:
        self._status_label.setText(text)
        self._status_timer.start(ms)
        self._update_status_bar()

    def _clear_status(self) -> None:
        self._downloading = False  # progress events stopped arriving: don't leave a stuck progress bar
        if self._job is None:
            self._status_label.setText("")
        self._update_status_bar()

    def _on_precache_progress(self, done: int, total: int) -> None:
        if total and done < total:
            self._precache_label.setText(f"Precaching speeds — {done} / {total}")
            self._precache_timer.stop()
        elif total:
            self._precache_label.setText("All speeds cached ✓")
            self._precache_timer.start(3000)
        else:
            self._precache_label.setText("")
        self._update_status_bar()

    def _on_download_progress(self, name: str, percent: int) -> None:
        """First-run download of the speech model: a real progress bar in the status bar."""
        self._downloading = percent < 100
        self._progress.setRange(0, 100)
        self._progress.setValue(percent)
        self._show_status(f"Downloading the speech model ({name}) — {percent}%" if percent < 100 else "Speech model downloaded", 4000)

    def _show_crash(self, summary: str, details: str) -> None:
        """An unhandled exception: friendly message, expandable traceback, Copy traceback, Open log file."""
        if self._crash_open:
            return
        self._crash_open = True
        try:
            ErrorDialog(summary, details, self.s.paths.log_file, parent=self).exec()
        finally:
            self._crash_open = False

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _error(self, title: str, text: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec()

    # ------------------------------------------------------------------ background jobs
    def _start_job(self, worker: TaskWorker, on_success, label: str = "Working…", on_fail=None) -> bool:
        if self._job is not None:
            self._error("Please wait", "Another task is still running.")
            return False
        self._job = worker
        self.s.state.busy = True
        worker.progress.connect(self._on_job_progress)
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_fail or self._on_job_failed)
        worker.finished.connect(lambda w=worker: self._on_job_finished(w))
        self._on_job_progress(label, -1)
        worker.start()
        return True

    def _on_job_progress(self, text: str, pct: int) -> None:
        self._status_timer.stop()
        self._status_label.setText(text)
        self._progress.setRange(0, 0 if pct < 0 else 100)
        if pct >= 0:
            self._progress.setValue(pct)
        self._update_status_bar()

    def _on_job_failed(self, message: str) -> None:
        self._error("Something went wrong", message)

    def _on_job_finished(self, worker: TaskWorker) -> None:
        cancelled = worker.cancelled
        if self._job is worker:
            self._job = None
        self.s.state.busy = False
        self._status_label.setText("")
        if cancelled:
            self._show_status("Cancelled", 3000)
            if not len(self.s.model):
                self.reader.show_placeholder("Extraction cancelled. Use Regions → Run OCR to try again.")
        self._update_status_bar()
        worker.deleteLater()

    def cancel_job(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self._status_label.setText("Cancelling…")

    # ------------------------------------------------------------------ opening things
    def pick_file(self, regions_first: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open file", "",
            "Documents (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.txt *.md);;All files (*)")
        if path:
            self.open_path(path, regions_first)

    def submit_input(self, text: str) -> None:
        first = text.splitlines()[0].strip().strip('"')
        if re.match(r"^https?://\S+$", text.strip()):
            self.open_link(text.strip())
        elif first and Path(first).exists():
            self.open_path(first)
        else:
            self._open_text_doc("Pasted text", text, "text", None)

    def open_path(self, path, regions_first: bool = False) -> None:
        path = Path(path)
        ext = path.suffix.lower()
        if ext in (".txt", ".md"):
            try:
                text = path.read_text("utf-8", errors="replace")
            except OSError as exc:
                self._error("Could not open file", str(exc))
                return
            self._open_text_doc(path.stem, text, "file", str(path.resolve()))
        elif self.s.loader.supports(path):
            self._open_document_file(path, regions_first)
        else:
            self.toast("Unsupported file type. EchoRead opens PDF, image (PNG/JPG/TIFF/BMP/WebP) and text (.txt/.md) files.", 6000)

    def _open_text_doc(self, title: str, text: str, kind: str, source: str | None) -> None:
        self.s.loader.close()
        self.s.regions.clear()
        self._file_doc = None
        self.reader.set_regions_enabled(False)
        doc_id = Library.make_id(kind, source or text)
        self._show_document(DocInfo(doc_id, title, kind, source), text, export_after=self._export_after)
        self._export_after = False

    _export_after = False

    def _open_document_file(self, path: Path, regions_first: bool) -> None:
        if self._job is not None:
            self._error("Please wait", "Another task is still running.")
            return
        try:
            pages = self.s.loader.open(path)
        except Exception as exc:
            pages = self._recover_pdf(path, exc)
            if pages is None:
                return
        self.s.regions.clear()
        resolved = path.resolve()
        doc = DocInfo(Library.make_id("file", f"{resolved}|{path.stat().st_size}"), path.stem, "file", str(resolved), pages)
        self._file_doc = doc
        self.reader.set_regions_enabled(True)
        stored = self.s.library.read_text(doc.doc_id) if self.s.library.get(doc.doc_id) else None
        if stored and not regions_first:  # already extracted before: resume where you left off
            self._show_document(doc, stored)
            return
        self.s.playback.stop()
        self.s.model.clear()
        self.s.state.doc = doc
        self.reader.show_placeholder(f"Loaded {pages} page{'s' if pages != 1 else ''}.")
        self._show_page("reader")
        if regions_first and not self._run_regions_dialog():
            self.reader.show_placeholder("Open Regions → Edit regions to choose what to read, then press Run OCR.")
            return
        self._start_extraction()

    def _recover_pdf(self, path: Path, exc: Exception) -> int | None:
        """A PDF that won't open normally: offer to draw its pages with a more forgiving renderer and read them with OCR."""
        get_logger("main_window").warning("could not open %s: %s", path, exc)
        if path.suffix.lower() != ".pdf":
            self._error("Could not open file", TaskWorker.friendly(exc))
            return None
        if not self._confirm("This PDF looks damaged",
                             "EchoRead couldn't open this PDF normally.\n\nIt can try to recover it: the pages are drawn as pictures "
                             "and read with OCR. That is slower, and any text layer is ignored.\n\nTry the OCR fallback?"):
            return None
        try:
            return self.s.loader.open_recovered(path)
        except Exception as exc2:
            get_logger("main_window").error("OCR fallback failed for %s", path, exc_info=True)
            self._error("Could not recover this PDF", "The file is too damaged to read, even with the fallback.\n\n" + TaskWorker.friendly(exc2))
            return None

    def open_link(self, url: str) -> None:
        if not self.s.settings.internet_usage:
            self._error("Internet Usage is off", "Enable Internet Usage in Settings to fetch web pages.")
            return
        self._start_job(TaskWorker(lambda w: self.s.web.extract(url)), self._on_link_fetched, "Fetching page…", on_fail=self._on_link_failed)

    def paste_link(self) -> None:
        if not self.s.settings.internet_usage:
            self._error("Internet Usage is off", "Enable Internet Usage in Settings to fetch web pages.")
            return
        url, ok = QInputDialog.getText(self, "Paste Link", "Web page address:")
        if ok and url.strip():
            self.open_link(url.strip())

    def _on_link_failed(self, message: str) -> None:
        self._error("Couldn't read this page",
                    "Couldn't read this page. Check the URL or disable Internet Usage.\n\nDetails: " + message)

    def _on_link_fetched(self, article) -> None:
        self._open_text_doc(article.title, article.text, "link", article.url)

    def add_text(self, export_after: bool = False) -> None:
        from PyQt6.QtWidgets import QDialog, QLineEdit, QPlainTextEdit

        dlg = QDialog(self)
        dlg.setWindowTitle("Add Text")
        dlg.resize(620, 520)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.addWidget(make_label("Add Text", "h2"))
        title = QLineEdit()
        title.setPlaceholderText("Title")
        body = QPlainTextEdit()
        body.setPlaceholderText("Paste or type the text you want to hear…")
        lay.addWidget(title)
        lay.addWidget(body, 1)
        row = QHBoxLayout()
        dictate = make_button("🎤  Dictate", tooltip="Dictation isn't included in this offline app")
        dictate.setEnabled(False)
        row.addWidget(dictate)
        row.addStretch(1)
        row.addWidget(make_button("Cancel", callback=dlg.reject))

        def save():
            if body.toPlainText().strip():
                dlg.accept()

        row.addWidget(make_button("Save", "primary", save))
        lay.addLayout(row)
        if dlg.exec() and body.toPlainText().strip():
            self._export_after = export_after
            self._open_text_doc(title.text().strip() or "Pasted text", body.toPlainText(), "text", None)

    def open_library_entry(self, doc_id: str) -> None:
        entry = self.s.library.get(doc_id)
        text = self.s.library.read_text(doc_id)
        if entry is None or text is None:
            self._error("Not found", "That saved item's text is missing.")
            return
        doc = DocInfo(doc_id, entry["title"], entry["kind"], entry.get("source"), entry.get("page_count", 0), entry.get("text_hash", ""))
        self.s.loader.close()
        self.s.regions.clear()
        self._file_doc = None
        source = entry.get("source")
        if entry["kind"] == "file" and source and Path(source).exists() and self.s.loader.supports(source):
            try:
                self.s.loader.open(source)
                self._file_doc = doc
            except Exception:
                self.s.loader.close()
        self.reader.set_regions_enabled(self._file_doc is not None)
        self._show_document(doc, text)

    def _show_document(self, doc: DocInfo, text: str, export_after: bool = False) -> None:
        """Put `text` in the reader, restoring the saved position, speed and bookmarks. Long documents are split
        into chapters and only the open chapter is loaded into the reader."""
        s = self.s
        strings = ParagraphModel.split(text)
        if not strings:
            self._error("Nothing to read", "No readable text was found.")
            return
        s.playback.stop()
        doc.text_hash = s.model.hash_of(strings)
        entry = s.library.register(doc, "\n\n".join(strings), default_speed=s.settings.speed)
        s.highlights.open(doc.doc_id, doc.text_hash)
        legacy = list(entry.get("highlights") or [])  # v1 kept whole-paragraph highlights in library.json: carry them over once
        if legacy and not s.highlights.has_saved_file(doc.doc_id):
            s.highlights.import_paragraph_flags(legacy, lambda g: len(strings[g]) if 0 <= g < len(strings) else 0)
            s.library.set_flags(doc.doc_id, entry["bookmarks"], [])  # the old list has been carried over
        self._loading = True
        try:
            s.model.set_document(strings, bookmarks=entry["bookmarks"], highlights=(), position=entry["paragraph"])
            s.state.doc = doc
            s.state.speed = entry["speed"]
        finally:
            self._loading = False
        self._restart_cache()
        self.reader.scroll_to_current()
        self._show_page("reader")
        if export_after:
            self.export_audio(".wav")

    def _on_section_changed(self, _index: int) -> None:
        """A different chapter was opened: precache that chapter and remember where you are."""
        if self._loading:
            return
        self._restart_cache()
        self._save_position()

    def _restart_cache(self) -> None:
        s = self.s
        if len(s.model):
            s.cache.start(s.model.all_texts(), s.settings.voice, max(s.model.current, 0), s.state.speed)
        else:
            s.cache.stop()

    # ------------------------------------------------------------------ regions / extraction
    def _run_regions_dialog(self) -> bool:
        if not self.s.loader.is_open:
            self._error("Regions", "Regions apply to PDFs and images. Open one of those first.")
            return False
        if self._job is not None:
            self._error("Please wait", "Another task is still running.")
            return False
        dlg = RegionsDialog(self.s.loader, self.s.regions, self)
        dlg.exec()
        return dlg.run_ocr

    def edit_regions(self) -> None:
        if self._run_regions_dialog():
            self._start_extraction()

    def rerun_extraction(self) -> None:
        if not self.s.loader.is_open:
            self._error("Regions", "Re-running extraction applies to PDFs and images.")
            return
        self._start_extraction(fresh=True)  # explicit re-run: throw away saved page results and start over

    def _clear_regions(self) -> None:
        self.s.regions.clear()
        self._show_status("Regions cleared", 3000)

    def _start_extraction(self, fresh: bool = False) -> None:
        doc = self._file_doc
        if doc is None or not self.s.loader.is_open:
            return
        self.s.playback.stop()
        ex = self.s.extractor
        ex.checkpoint_dir = self.s.paths.ocr_dir / doc.doc_id  # finished pages are saved here as they complete
        ex.fresh = fresh

        def done(text: str) -> None:
            if not text.strip():
                self._error("No text found", "No text detected. Try adjusting Keep/Ignore regions.\n\nAlso check the OCR "
                            "language in Settings, or choose 'Always OCR' if the PDF's text layer is broken.")
                return
            if ex.resumed_pages:
                self._show_status(f"Resumed: {ex.resumed_pages} page(s) were already done from an earlier run", 8000)
            if ex.failed_pages:
                shown = ", ".join(map(str, ex.failed_pages[:15])) + ("…" if len(ex.failed_pages) > 15 else "")
                self._error("Some pages couldn't be read",
                            f"{len(ex.failed_pages)} page(s) were skipped: {shown}.\n\nEverything else was kept. "
                            "Regions → Edit regions → Run OCR tries just those pages again.")
            self._show_document(doc, text)

        self._start_job(TaskWorker(ex.extract), done, "Starting…")

    # ------------------------------------------------------------------ export
    def export_text(self, ext: str) -> None:
        if not len(self.s.model):
            return
        doc = self.s.state.doc
        default = f"{doc.title if doc else 'document'}{ext}"
        path, _ = QFileDialog.getSaveFileName(self, "Export text", default, f"{ext[1:].upper()} (*{ext})")
        if path:
            try:
                self.s.exporter.export_text(path, doc)
                self._show_status(f"Saved {path}", 6000)
            except OSError as exc:
                self._error("Could not save", str(exc))

    def export_audio(self, ext: str) -> None:
        if not len(self.s.model):
            return
        doc = self.s.state.doc
        m = self.s.model
        name = doc.title if doc else "document"
        if m.section_count > 1:  # this exports the open chapter; "Export every chapter" does the whole book
            name += f" - {m.sections[m.section_index].title}"
        path, _ = QFileDialog.getSaveFileName(self, "Export audio", f"{name}{ext}", f"{ext[1:].upper()} audio (*{ext})")
        if not path:
            return
        speed = self.s.state.speed
        self.s.playback.stop()
        self._start_job(TaskWorker(lambda w: self.s.exporter.export_audio(w, path, speed)),
                        lambda p: self._error("Audio exported", f"Saved to:\n{p}"), "Preparing audio…")

    def export_chapters(self, ext: str) -> None:
        if self.s.model.section_count < 2:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for one audio file per chapter")
        if not folder:
            return
        speed = self.s.state.speed
        self.s.playback.stop()
        self._start_job(TaskWorker(lambda w: self.s.exporter.export_chapters(w, folder, speed, ext)),
                        lambda p: self._error("Audio exported", f"Saved to:\n{p}\n\nChapters already exported are skipped, so if you stop "
                                              "this you can run it again and it carries on."), "Preparing audio…")

    def export_folder(self) -> None:
        if not len(self.s.model):
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the per-paragraph audio")
        if not folder:
            return
        speed = self.s.state.speed
        self.s.playback.stop()
        self._start_job(TaskWorker(lambda w: self.s.exporter.export_paragraph_folder(w, folder, speed)),
                        lambda p: self._error("Audio exported", f"Saved to:\n{p}"), "Preparing audio…")

    # ------------------------------------------------------------------ voices
    def preview_voice(self, voice_id: str) -> None:
        s = self.s
        s.playback.stop()
        if self._preview is not None and self._preview.isRunning():
            return
        text = s.catalog.sample_text(voice_id)
        self._show_status("Synthesizing speech…", 15000)
        worker = TaskWorker(lambda w: s.tts.synthesize(text, voice_id, 1.0))
        self._preview = worker

        def play(result) -> None:
            audio, sr = result
            try:
                s.player.play(audio, sr)
            except Exception as exc:
                self._error("Audio output problem", str(exc))

        worker.succeeded.connect(play)
        worker.failed.connect(lambda m: self._error("Could not preview this voice", m))
        worker.finished.connect(lambda: self._show_status("", 100))
        worker.start()

    def import_voice_pack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import voice pack", "", "Voice packs (*.npy *.npz *.bin *.json)")
        if not path:
            return
        lang = "en-us"
        if not path.lower().endswith(".json"):
            names = list(self.s.catalog.language_names.values())
            choice, ok = QInputDialog.getItem(self, "Voice language", "Which language does this voice speak?", names, 0, False)
            if not ok:
                return
            lang = next(code for code, name in self.s.catalog.language_names.items() if name == choice)
        name, ok = QInputDialog.getText(self, "Voice pack name", "Name for this voice:", text=Path(path).stem)
        if not ok:
            return
        try:
            vid = self.s.catalog.import_pack(path, lang, name)
        except Exception as exc:
            self._error("Could not import voice pack", str(exc))
            return
        self.voices_view.refresh()
        self._show_page("voices")
        self._show_status(f"Imported '{name}'. Select it and press Use this voice.", 7000)

    def _delete_pack(self, voice_id: str) -> None:
        self.s.catalog.delete_pack(voice_id)
        if self.s.settings.voice == voice_id:
            self.s.settings.voice = "af_heart"
        self.voices_view.refresh()

    # ------------------------------------------------------------------ dialogs
    def show_settings(self, focus: str | None = None) -> None:
        SettingsDialog(self.s.settings, self.s.cache, self.s.catalog, self.s.profile, self.fonts, self, focus=focus).exec()

    def show_help(self) -> None:
        ShortcutHelpDialog(self).exec()

    # ------------------------------------------------------------------ persistence of reading state
    def _save_position(self) -> None:
        doc = self.s.state.doc
        if doc is not None and len(self.s.model) and self.s.model.current >= 0:
            self.s.library.update_position(doc.doc_id, self.s.model.current_global, self.s.state.speed)

    def _save_flags(self) -> None:
        doc = self.s.state.doc
        if doc is not None:
            self.s.library.set_flags(doc.doc_id, self.s.model.bookmarks(), self.s.model.highlights())

    # ------------------------------------------------------------------ window events
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            event.acceptProposedAction()
            self.open_path(urls[0])

    def closeEvent(self, event) -> None:
        s = self.s
        s.playback.stop()
        if self._job is not None:
            self._job.cancel()
            self._job.wait(3000)
        s.cache.shutdown()
        s.library.flush()
        try:
            s.highlights.flush()
        except OSError:
            pass
        s.loader.close()
        super().closeEvent(event)