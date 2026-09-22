"""Small reusable widgets and factories."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QAbstractTextDocumentLayout, QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPixmap,
                         QTextBlockFormat, QTextCursor, QTextDocument, QTextOption)
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QRadioButton, QStackedWidget, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core.models import picture_name
from .theme import qcolor, svg_pixmap, themed_icon, tokens


def make_button(text: str, name: str | None = None, callback=None, tooltip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if callback:
        b.clicked.connect(lambda checked=False, f=callback: f())
    if tooltip:
        b.setToolTip(tooltip)
    return b


def make_label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class IconButton(QPushButton):
    """A button drawn from an SVG in assets/icons. The icon takes the theme's icon colour and turns accent-coloured on hover.
    Call apply_theme() when the theme changes."""

    def __init__(self, icon_name: str, text: str = "", name: str | None = None, callback=None, tooltip: str | None = None,
                 normal: str = "icon", active: str = "accent", size: int = 20, theme: str = "dark", parent=None):
        super().__init__(text, parent)
        if name:
            self.setObjectName(name)
        if callback:
            self.clicked.connect(lambda checked=False, f=callback: f())
        if tooltip:
            self.setToolTip(tooltip)
        self._icon_name = icon_name
        self._normal, self._active = normal, active
        self._theme = theme
        self._hover = False
        self.setIconSize(QSize(size, size))
        self._refresh()

    @property
    def icon_name(self) -> str:
        return self._icon_name

    def set_icon_name(self, icon_name: str) -> None:
        if icon_name != self._icon_name:
            self._icon_name = icon_name
            self._refresh()

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self._refresh()

    def _refresh(self) -> None:
        colour = self._active if self._hover else self._normal
        self.setIcon(themed_icon(self._icon_name, self._theme, colour, colour, self.iconSize().width()))

    def enterEvent(self, event) -> None:
        self._hover = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._refresh()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh()


class ParagraphText(QWidget):
    """Paragraph text drawn with a QTextDocument, so it can paint backgrounds behind character ranges (the word being spoken,
    the sentence, coloured highlights, a drag selection) and answer 'which character is under this point?'."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._doc = QTextDocument(self)
        self._doc.setDocumentMargin(0)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        self._doc.setDefaultTextOption(opt)
        self._text = ""
        self._font = QFont()
        self._line_spacing = 1.65
        self._color = QColor("#B0B0B8")
        self._sentence: tuple[int, int] | None = None
        self._word: tuple[int, int] | None = None
        self._highlights: list[tuple[int, int, QColor]] = []
        self._selection: tuple[int, int] | None = None
        self._sentence_color = QColor(99, 102, 241, 56)
        self._word_color = QColor(99, 102, 241, 184)
        self._select_color = QColor(3, 61, 98)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # the ParagraphWidget frame handles the mouse
        self.set_text(text)

    # ------------------------------------------------------------------ content and look
    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._doc.setPlainText(text)
        self._apply_line_height()
        self.updateGeometry()
        self.update()

    def set_font_spec(self, font: QFont, line_spacing: float) -> None:
        self._font = QFont(font)
        self._line_spacing = line_spacing
        self._doc.setDefaultFont(self._font)
        self._apply_line_height()
        self.updateGeometry()
        self.update()

    def _apply_line_height(self) -> None:
        """CSS-style line height: `line_spacing` x the font size, however tall the font's natural line is."""
        fm = QFontMetricsF(self._font)
        px = self._font.pixelSize() if self._font.pixelSize() > 0 else self._font.pointSizeF() * 96.0 / 72.0
        natural = fm.lineSpacing()
        pct = max(100.0, 100.0 * self._line_spacing * px / natural) if natural > 0 else 100.0
        cursor = QTextCursor(self._doc)
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(pct, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(fmt)

    def set_text_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = QColor(color)
            self.update()

    def set_decoration_colors(self, sentence: QColor, word: QColor, selection: QColor) -> None:
        self._sentence_color, self._word_color, self._select_color = QColor(sentence), QColor(word), QColor(selection)
        self.update()

    def set_spoken(self, sentence: tuple[int, int] | None, word: tuple[int, int] | None) -> None:
        if sentence != self._sentence or word != self._word:
            self._sentence, self._word = sentence, word
            self.update()

    def set_highlights(self, ranges: list[tuple[int, int, QColor]]) -> None:
        self._highlights = list(ranges)
        self.update()

    def set_selection(self, span: tuple[int, int] | None) -> None:
        if span != self._selection:
            self._selection = span
            self.update()

    @property
    def selection(self) -> tuple[int, int] | None:
        return self._selection

    # ------------------------------------------------------------------ geometry
    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        self._doc.setTextWidth(max(20, width))
        return int(self._doc.size().height()) + 1

    def sizeHint(self) -> QSize:
        w = max(200, self.width())
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(120, self.heightForWidth(max(120, self.width())))

    def resizeEvent(self, event) -> None:
        self._doc.setTextWidth(max(20, self.width()))
        super().resizeEvent(event)

    # ------------------------------------------------------------------ hit testing
    def char_at(self, point: QPointF, exact: bool = False) -> int:
        """Character position (between characters) nearest `point`; with exact=True, -1 unless the point is on text."""
        self._doc.setTextWidth(max(20, self.width()))
        acc = Qt.HitTestAccuracy.ExactHit if exact else Qt.HitTestAccuracy.FuzzyHit
        return int(self._doc.documentLayout().hitTest(point, acc))

    def rect_of_range(self, start: int, end: int):
        """Bounding box of characters [start, end) in widget coordinates (first line only; used to place popups)."""
        cursor = QTextCursor(self._doc)
        cursor.setPosition(max(0, min(start, len(self._text))))
        self._doc.setTextWidth(max(20, self.width()))
        block = cursor.block()
        tl = block.layout()
        line = tl.lineForTextPosition(cursor.positionInBlock())
        if not line.isValid():
            return None
        x0 = line.cursorToX(cursor.positionInBlock())[0]
        y = line.y() + tl.position().y()
        cursor.setPosition(max(start, min(end, len(self._text))))
        line2 = cursor.block().layout().lineForTextPosition(cursor.positionInBlock())
        x1 = line2.cursorToX(cursor.positionInBlock())[0] if line2.isValid() else x0
        return (x0, y, max(x1, x0 + 2), line.height())

    # ------------------------------------------------------------------ painting
    def _selection_for(self, span: tuple[int, int], color: QColor):
        s = QAbstractTextDocumentLayout.Selection()
        cursor = QTextCursor(self._doc)
        cursor.setPosition(max(0, span[0]))
        cursor.setPosition(min(len(self._text), span[1]), QTextCursor.MoveMode.KeepAnchor)
        s.cursor = cursor
        s.format.setBackground(QBrush(color))
        return s

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._doc.setTextWidth(max(20, self.width()))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(ctx.palette.ColorRole.Text, self._color)
        sels = []
        if self._sentence:
            sels.append(self._selection_for(self._sentence, self._sentence_color))
        for s, e, color in self._highlights:
            sels.append(self._selection_for((s, e), color))
        if self._word:
            sels.append(self._selection_for(self._word, self._word_color))
        if self._selection:
            sels.append(self._selection_for(self._selection, self._select_color))
        ctx.selections = sels
        self._doc.documentLayout().draw(painter, ctx)
        painter.end()


class PictureView(QWidget):
    """A picture kept from a PDF or scan, shown at the width of the column (never bigger than it really is)."""

    MAX_HEIGHT = 560

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._pm = QPixmap(str(path)) if path else QPixmap()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._border = QColor(128, 128, 140, 90)

    @property
    def loaded(self) -> bool:
        return not self._pm.isNull()

    def _target(self, width: int) -> tuple[int, int]:
        if self._pm.isNull():
            return max(width, 40), 48
        w, h = self._pm.width() / self._pm.devicePixelRatio(), self._pm.height() / self._pm.devicePixelRatio()
        scale = min(width / w, self.MAX_HEIGHT / h, 1.0)
        return max(1, int(w * scale)), max(1, int(h * scale))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._target(max(width, 40))[1] + 8

    def sizeHint(self) -> QSize:
        w = max(200, self.width())
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(120, 60)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        if self._pm.isNull():
            p.setPen(self._border)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "(picture not found)")
            return
        w, h = self._target(self.width())
        x = 0
        path = QPainterPath()
        path.addRoundedRect(x, 4, w, h, 6, 6)
        p.setClipPath(path)
        p.drawPixmap(x, 4, w, h, self._pm)
        p.setClipping(False)
        p.setPen(self._border)
        p.drawRoundedRect(x, 4, w - 1, h - 1, 6, 6)
        p.end()


class ParagraphWidget(QFrame):
    """One paragraph. Click a word to play from it; drag across text to select it (a colour palette follows);
    click the left gutter to bookmark."""

    GUTTER = 28
    DRAG_DISTANCE = 6  # px of movement before a press becomes a selection
    clicked = pyqtSignal(int, int)  # paragraph, character clicked (-1 if none)
    bookmark_toggled = pyqtSignal(int)
    selection_finished = pyqtSignal(int, int, int, QPoint)  # paragraph, start, end, where the mouse was released (global)
    context_requested = pyqtSignal(int, QPoint, int)  # paragraph, global position, character under the mouse (-1 if none)

    def __init__(self, index: int, text: str, images_dir=None, parent=None):
        super().__init__(parent)
        self.index = index
        self._picture_name = picture_name(text)
        self._theme = "dark"
        self._state = ""
        self._bookmarked = False
        self._selectable = True
        self._press: QPointF | None = None
        self._press_char = -1
        self._dragging = False
        self.setObjectName("para")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click a word to play from it · drag to highlight · click the left edge to bookmark")
        self.setProperty("state", "")
        self.setProperty("highlighted", "false")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 8, 14, 8)
        row.setSpacing(4)
        self._gutter = QLabel("")
        self._gutter.setObjectName("gutter")
        self._gutter.setFixedWidth(self.GUTTER - 6)
        self._gutter.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._gutter.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._body = ParagraphText("" if self._picture_name else text)
        row.addWidget(self._gutter)
        if self._picture_name:  # a picture: shown, never read; the text widget stays (hidden) so the reader's calls still work
            self._picture = PictureView(Path(images_dir) / self._picture_name if images_dir else None)
            self._body.hide()
            self._selectable = False
            row.addWidget(self._picture, 1)
        else:
            self._picture = None
            row.addWidget(self._body, 1)
        self.apply_theme("dark")

    # ------------------------------------------------------------------ pieces the reader configures
    @property
    def body(self) -> ParagraphText:
        return self._body

    @property
    def text(self) -> str:
        return self._body.text

    @property
    def is_picture(self) -> bool:
        return self._picture is not None

    def set_selectable(self, on: bool) -> None:
        if self._picture is not None:
            return
        self._selectable = bool(on)
        if not on:
            self._press, self._dragging = None, False
            self._body.set_selection(None)

    def apply_theme(self, theme: str) -> None:
        self._theme = "light" if theme == "light" else "dark"
        tk = tokens(self._theme)
        self._body.set_decoration_colors(qcolor(tk["sentence_bg"]), qcolor(tk["word_bg"]), qcolor(tk["select_bg"]))
        self._apply_text_color()
        self._update_gutter()

    def _apply_text_color(self) -> None:
        tk = tokens(self._theme)
        token = {"playing": "heading", "skipped": "para_dim", "ignored": "para_dim"}.get(self._state, "body")
        self._body.set_text_color(qcolor(tk[token]))

    def _update_gutter(self) -> None:
        if self._bookmarked:
            self._gutter.setPixmap(svg_pixmap("bookmark_filled", tokens(self._theme)["accent"], 16))
        else:
            self._gutter.clear()

    def set_state(self, state: str) -> None:
        if self.property("state") != state:
            self.setProperty("state", state)
            repolish(self)
        if state != self._state:
            self._state = state
            self._apply_text_color()

    def set_flags(self, bookmarked: bool, highlighted: bool) -> None:
        if bookmarked != self._bookmarked:
            self._bookmarked = bookmarked
            self._update_gutter()
        flag = "true" if highlighted else "false"
        if self.property("highlighted") != flag:
            self.setProperty("highlighted", flag)
            repolish(self)

    # ------------------------------------------------------------------ mouse
    def _char(self, pos: QPointF, exact: bool = False) -> int:
        return self._body.char_at(self._body.mapFromParent(pos), exact)

    def _trimmed(self, a: int, b: int) -> tuple[int, int]:
        text = self._body.text
        a, b = max(0, min(a, b)), min(len(text), max(a, b))
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        return a, b

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.position().x() < self.GUTTER:
            self.bookmark_toggled.emit(self.index)
            return
        self._press = event.position()
        self._press_char = self._char(event.position())
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._dragging:
            if not self._selectable or (event.position() - self._press).manhattanLength() < self.DRAG_DISTANCE:
                return
            self._dragging = True
        a, b = self._trimmed(self._press_char, self._char(event.position()))
        self._body.set_selection((a, b) if b > a else None)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        was_dragging, press_char = self._dragging, self._press_char
        self._press, self._dragging = None, False
        if was_dragging:
            sel = self._body.selection
            if sel and sel[1] > sel[0]:
                self.selection_finished.emit(self.index, sel[0], sel[1], event.globalPosition().toPoint())
            else:
                self._body.set_selection(None)
        else:
            self.clicked.emit(self.index, press_char)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.index, event.globalPos(), self._char(QPointF(event.pos()), exact=True))


class ShortcutHelpDialog(QDialog):
    """The '?' overlay."""

    SHORTCUTS = [
        ("Space", "Play / pause"),
        ("← / →", "Previous / next paragraph"),
        ("↑ / ↓", "Previous / next paragraph (alternate)"),
        ("[  /  ]", "Speed down / up"),
        ("+  /  -", "Volume up / down"),
        ("B", "Bookmark the current paragraph"),
        ("Ctrl+Shift+H", "Turn drag-to-highlight on / off"),
        ("Ctrl+F", "Change the reading font"),
        ("PgUp / PgDn", "Previous / next chapter"),
        ("Enter", "Play from the current paragraph"),
        ("Ctrl+O", "Open a file"),
        ("Ctrl+,", "Settings"),
        ("?", "Show this help"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(make_label("Keyboard shortcuts", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(10)
        for row, (keys, what) in enumerate(self.SHORTCUTS):
            k = make_label(keys, "cardtitle")
            grid.addWidget(k, row, 0)
            grid.addWidget(make_label(what), row, 1)
        lay.addLayout(grid)
        lay.addWidget(make_label("Click a word to play from it. Drag across text to highlight it. Click a paragraph's left edge to bookmark it.", "muted", wrap=True))
        close = make_button("Close", "primary", self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


def circular_pixmap(source, size: int, dpr: float = 2.0) -> QPixmap:
    """A picture (file path, QImage or QPixmap) centre-cropped to a square and clipped to a circle, `size` px wide."""
    if isinstance(source, QImage):
        img = source
    elif isinstance(source, QPixmap):
        img = source.toImage()
    else:
        img = QImage(str(source))
    px = max(1, int(round(size * dpr)))
    out = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    if not img.isNull():
        scaled = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(out)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addEllipse(0, 0, px, px)
        painter.setClipPath(path)
        painter.drawImage((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        painter.end()
    pm = QPixmap.fromImage(out)
    pm.setDevicePixelRatio(dpr)
    return pm


def paint_avatar(label: QLabel, name: str, color: str, size: int = 32, image=None) -> None:
    """Round avatar: the profile picture when there is one (`image` = file path / QImage), otherwise a coloured circle with the
    first letter of `name`. Used in the sidebar, top bar, Settings and the welcome dialog."""
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if image is not None:
        pm = circular_pixmap(image, size)
        if not pm.isNull():
            label.setText("")
            label.setStyleSheet("QLabel { background: transparent; }")
            label.setPixmap(pm)
            return
    label.setPixmap(QPixmap())
    label.setText(name[:1].upper() if name else "")
    label.setStyleSheet(f"QLabel {{ background: {color}; color: #FFFFFF; border-radius: {size // 2}px; font-weight: 700; "
                        f"font-size: {max(9, int(size * 0.34))}pt; }}")


class ExportDialog(QDialog):
    """Export in two steps: 1) Text or Audio, 2) what shape: one file for the whole book, one per chapter, or (audio) just the open chapter.
    Afterwards `kind` is "text"/"audio", `scope` is "book"/"chapters"/"open" and `ext` the file type (".txt", ".mp3"...)."""

    def __init__(self, chapters: int, open_chapter: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._chapters = chapters
        self.kind: str | None = None
        self.scope: str | None = None
        self.ext: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(14)
        self._step = make_label("", "muted")
        lay.addWidget(self._step)
        self._stack = QStackedWidget()
        lay.addWidget(self._stack)

        # ---------------------------------------------------------------- step 1: text or audio
        first = QWidget()
        fl = QVBoxLayout(first)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(12)
        fl.addWidget(make_label("What do you want to export?", "h2"))
        self.text_card = QPushButton("Text\nThe words of the document")
        self.audio_card = QPushButton("Audio\nThe document read aloud, as sound files")
        self._cards = QButtonGroup(self)
        self._cards.setExclusive(True)
        for card in (self.text_card, self.audio_card):
            card.setObjectName("sourcecard")
            card.setCheckable(True)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setMinimumHeight(74)
            self._cards.addButton(card)
            fl.addWidget(card)
            card.clicked.connect(self._choose_kind)
        self._stack.addWidget(first)

        # ---------------------------------------------------------------- step 2: the shape
        second = QWidget()
        sl = QVBoxLayout(second)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self._title2 = make_label("", "h2")
        sl.addWidget(self._title2)
        self._group = QButtonGroup(self)
        self.options: dict[str, QRadioButton] = {}
        for scope in ("book", "chapters", "open"):
            radio = QRadioButton()
            self._group.addButton(radio)
            self.options[scope] = radio
            sl.addWidget(radio)
            radio.toggled.connect(lambda _on, self=self: self._fill_formats())
        self._no_chapters = make_label("This document isn't split into chapters, so the whole document is exported as one.", "muted", wrap=True)
        sl.addWidget(self._no_chapters)
        row = QHBoxLayout()
        row.addWidget(make_label("File type"))
        self.format_combo = QComboBox()
        self.format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        row.addWidget(self.format_combo)
        row.addStretch(1)
        sl.addLayout(row)
        self._stack.addWidget(second)
        self._open_chapter = open_chapter

        # ---------------------------------------------------------------- buttons
        buttons = QHBoxLayout()
        self.cancel_button = make_button("Cancel", callback=self.reject)
        self.back_button = make_button("Back", callback=lambda: self._show_step(0))
        self.next_button = make_button("Next", "primary", lambda: self._show_step(1))
        self.export_button = make_button("Export", "primary", self._finish)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        for b in (self.back_button, self.next_button, self.export_button):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self._show_step(0)

    # ------------------------------------------------------------------ steps
    def _choose_kind(self) -> None:
        self.next_button.setEnabled(True)

    def _show_step(self, step: int) -> None:
        if step == 1 and not (self.text_card.isChecked() or self.audio_card.isChecked()):
            return
        self._stack.setCurrentIndex(step)
        self._step.setText(f"Step {step + 1} of 2")
        self.back_button.setVisible(step == 1)
        self.next_button.setVisible(step == 0)
        self.export_button.setVisible(step == 1)
        self.next_button.setEnabled(self.text_card.isChecked() or self.audio_card.isChecked())
        if step == 1:
            self._build_options("audio" if self.audio_card.isChecked() else "text")

    def _build_options(self, kind: str) -> None:
        many = self._chapters > 1
        labels = {
            "text": {"book": "Export as a single text file", "chapters": "Export each chapter as its own text file"},
            "audio": {"book": "Export as a single audio file — every chapter joined into one",
                      "chapters": "Export each chapter as its own audio file",
                      "open": f"Export only the chapter I'm on{f' ({self._open_chapter})' if self._open_chapter else ''}"},
        }[kind]
        self._title2.setText("How do you want the text?" if kind == "text" else "How do you want the audio?")
        for scope, radio in self.options.items():
            radio.setVisible(scope in labels)
            if scope in labels:
                radio.setText(labels[scope])
                radio.setEnabled(scope == "book" or many)
        self._no_chapters.setVisible(not many)
        self._group.setExclusive(False)
        for radio in self.options.values():
            radio.setChecked(False)
        self._group.setExclusive(True)
        self.options["book"].setChecked(True)
        self._fill_formats()

    def _fill_formats(self) -> None:
        if self._stack.currentIndex() != 1:
            return
        kind = "audio" if self.audio_card.isChecked() else "text"
        scope = self.selected_scope()
        types = ((".mp3", "MP3 (smaller)"), (".wav", "WAV (best quality, large)")) if kind == "audio" else \
            (((".txt", "Plain text (.txt)"), (".md", "Markdown (.md)")) + (((".json", "JSON (.json)"),) if scope == "book" else ()))
        current = self.format_combo.currentData()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for ext, label in types:
            self.format_combo.addItem(label, ext)
        if current in [e for e, _l in types]:
            self.format_combo.setCurrentIndex([e for e, _l in types].index(current))
        self.format_combo.blockSignals(False)

    def selected_scope(self) -> str:
        return next((s for s, r in self.options.items() if r.isChecked()), "book")

    def _finish(self) -> None:
        self.kind = "audio" if self.audio_card.isChecked() else "text"
        self.scope = self.selected_scope()
        self.ext = self.format_combo.currentData()
        self.accept()
