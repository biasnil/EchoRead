"""First-run welcome: ask for a name and an avatar colour. Also holds the colour picker Settings reuses."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                             QWidget)

from core.profile import ProfileManager
from .theme import tokens
from .widgets import make_button, make_label, paint_avatar


class AvatarColorPicker(QWidget):
    """A row of round colour swatches (exactly one is selected)."""

    color_changed = pyqtSignal(str)

    def __init__(self, colors=ProfileManager.AVATAR_COLORS, current: str | None = None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for c in colors:
            b = QPushButton()
            b.setObjectName("swatch")
            b.setCheckable(True)
            b.setToolTip(c)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton#swatch {{ background: {c}; }}")
            b.clicked.connect(lambda checked=False, col=c: self.color_changed.emit(col))
            self._group.addButton(b)
            self.buttons[c] = b
            row.addWidget(b)
        row.addStretch(1)
        self.set_color(current or colors[0])

    def color(self) -> str:
        return next((c for c, b in self.buttons.items() if b.isChecked()), next(iter(self.buttons)))

    def set_color(self, color: str) -> None:
        if color in self.buttons:
            self.buttons[color].setChecked(True)


class DropAvatar(QLabel):
    """The round avatar preview. Drop a picture file on it, or click it to browse."""

    file_dropped = pyqtSignal(str)
    clicked = pyqtSignal()

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Drop a picture here, or click to choose one")
        self._size = size
        self._over = False
        self.setFixedSize(size, size)

    @staticmethod
    def _first_file(event) -> str | None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).is_file():
                return url.toLocalFile()
        return None

    def _set_over(self, on: bool) -> None:
        if on != self._over:
            self._over = on
            self.update()

    def dragEnterEvent(self, event) -> None:
        if self._first_file(event):
            event.acceptProposedAction()
            self._set_over(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._first_file(event):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_over(False)

    def dropEvent(self, event) -> None:
        self._set_over(False)
        path = self._first_file(event)
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._over:  # a dashed accent ring while a file is held over it
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(tokens("dark")["accent"]), 2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawEllipse(self.rect().adjusted(1, 1, -2, -2))
            p.end()


class AvatarEditor(QWidget):
    """Profile picture + colour preview with 'Choose picture…' / 'Remove picture'. Changes are kept aside until `apply()`
    (so Cancel in a dialog really cancels). Any picture Pillow can read works; it is cropped to a square and copied into
    EchoRead's own folder, the original file is never touched."""

    changed = pyqtSignal()

    HINT = "Drop a picture on the circle, or click it. It stays on this computer."
    FILTER = "Pictures (*.png *.jpg *.jpeg *.jfif *.bmp *.gif *.webp *.tif *.tiff *.ico);;All files (*)"

    def __init__(self, profile: ProfileManager, size: int = 72, parent=None):
        super().__init__(parent)
        self._profile = profile
        self._size = size
        self._name = profile.name
        self._color = profile.avatar_color
        self._pending = None  # PIL image chosen but not saved yet
        self._pending_q: QImage | None = None
        self._remove = False  # the saved picture is to be removed on apply()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.preview = DropAvatar(size)
        lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.choose_button = make_button("Choose picture…", callback=self.choose)
        self.remove_button = make_button("Remove", callback=self.remove)
        row.addStretch(1)
        row.addWidget(self.choose_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.note = make_label(self.HINT, "muted", wrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.note)
        self.preview.file_dropped.connect(self.load_path)
        self.preview.clicked.connect(self.choose)
        self._refresh()

    # ------------------------------------------------------------------ state
    @property
    def has_pending(self) -> bool:
        return self._pending is not None or self._remove

    @property
    def shows_picture(self) -> bool:
        return self._source() is not None

    def _source(self):
        if self._pending_q is not None:
            return self._pending_q
        if self._remove:
            return None
        return self._profile.avatar_image_path

    def set_identity(self, name: str, color: str) -> None:
        self._name, self._color = name, color
        self._refresh()

    def _refresh(self) -> None:
        paint_avatar(self.preview, self._name, self._color, self._size, self._source())
        self.remove_button.setEnabled(self.shows_picture)

    # ------------------------------------------------------------------ choosing
    def choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", "", self.FILTER)
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> bool:
        try:
            image = ProfileManager.prepare_image(path)
        except ValueError as exc:
            self.note.setText(str(exc))
            return False
        data = image.tobytes("raw", "RGBA")
        self._pending = image
        self._pending_q = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
        self._remove = False
        self.note.setText(self.HINT)
        self._refresh()
        self.changed.emit()
        return True

    def remove(self) -> None:
        self._remove = self._profile.avatar_image_path is not None
        self._pending, self._pending_q = None, None
        self.note.setText(self.HINT)
        self._refresh()
        self.changed.emit()

    def apply(self) -> None:
        """Make the choice permanent (call after the name has been saved)."""
        if self._pending is not None:
            self._profile.set_avatar_image(self._pending)
        elif self._remove:
            self._profile.clear_avatar_image()
        self._pending, self._pending_q, self._remove = None, None, False
        self._refresh()


class OnboardingDialog(QDialog):
    """'Welcome to EchoRead' - Continue saves the profile, Skip for now just remembers not to ask again."""

    def __init__(self, profile: ProfileManager, parent=None):
        super().__init__(parent)
        self._profile = profile
        self.setWindowTitle("Welcome to EchoRead")
        self.setModal(True)
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(14)
        self.avatar = AvatarEditor(profile, 80)
        lay.addWidget(self.avatar)
        title = make_label("Welcome to EchoRead", "h1")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)
        sub = make_label("What should we call you? Your name is only used on this computer, to label your profile. "
                         "It is never sent anywhere.", "muted", wrap=True)
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(sub)
        lay.addWidget(make_label("Name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your name")
        self.name_edit.setMaxLength(ProfileManager.MAX_NAME)
        lay.addWidget(self.name_edit)
        lay.addWidget(make_label("Avatar color"))
        self.picker = AvatarColorPicker(current=profile.avatar_color)
        lay.addWidget(self.picker)
        lay.addSpacing(6)
        row = QHBoxLayout()
        self.skip_button = make_button("Skip for now", callback=self._skip)
        self.continue_button = make_button("Continue", "primary", self._continue)
        self.continue_button.setDefault(True)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.continue_button)
        lay.addLayout(row)
        self.name_edit.textChanged.connect(self._changed)
        self.picker.color_changed.connect(lambda _c: self._changed())
        self._changed()

    def _changed(self) -> None:
        name = ProfileManager.clean_name(self.name_edit.text())
        self.avatar.set_identity(name, self.picker.color())
        self.continue_button.setEnabled(bool(name))  # a name is required to continue

    def _continue(self) -> None:
        if self._profile.save(self.name_edit.text(), self.picker.color()):
            self.avatar.apply()
            self.accept()

    def _skip(self) -> None:
        self._profile.skip()
        self.reject()