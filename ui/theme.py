"""Design tokens (dark + light) and the stylesheet built from them."""
from __future__ import annotations

import re
from pathlib import Path
from string import Template

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from core.paths import AppPaths

THEMES = {
    "dark": {
        "bg": "#0D0D0D", "card": "#1A1A1F", "sidebar": "#08080A",
        "accent": "#6366F1", "accent_hover": "#7477F5", "on_accent": "#FFFFFF",
        "heading": "#FFFFFF", "body": "#B0B0B8", "muted": "#6B6B76", "strong": "#E6E6EC",
        "border": "#2A2A32", "input_bg": "#101014", "hover": "#15151A", "hover_strong": "#24242B",
        "nav_active": "#1A1A2E", "menu_sel": "#2A2A44", "scroll": "#2A2A32", "disabled": "#55555E",
        "para_sel_bg": "rgba(255,255,255,0.05)", "para_sel_bar": "#3A3A48",
        "para_play_bg": "rgba(99,102,241,0.16)", "para_dim": "#4D4D56",
        "hl_bg": "rgba(250,204,21,0.13)", "hl_bar": "#CA8A04",
        "warn": "#F59E0B", "icon": "#FFFFFF", "sentence_bg": "rgba(99,102,241,0.22)", "word_bg": "rgba(99,102,241,0.72)",
        "select_bg": "rgba(3,61,98,1.0)", "palette_bg": "#282828", "toast_bg": "#26262E",
        "shadow": "0",
    },
    "light": {
        "bg": "#F7F7FA", "card": "#FFFFFF", "sidebar": "#EFEFF3",
        "accent": "#4F46E5", "accent_hover": "#6058F0", "on_accent": "#FFFFFF",
        "heading": "#0D0D0D", "body": "#4A4A52", "muted": "#8A8A94", "strong": "#1A1A1F",
        "border": "#E2E2EA", "input_bg": "#FFFFFF", "hover": "#E6E6EC", "hover_strong": "#DCDCE4",
        "nav_active": "#E3E2FB", "menu_sel": "#E3E2FB", "scroll": "#CFCFD8", "disabled": "#B4B4BE",
        "para_sel_bg": "rgba(0,0,0,0.04)", "para_sel_bar": "#C4C4CE",
        "para_play_bg": "rgba(79,70,229,0.12)", "para_dim": "#B4B4BE",
        "hl_bg": "rgba(250,204,21,0.32)", "hl_bar": "#CA8A04",
        "warn": "#B45309", "icon": "#0D0D0D", "sentence_bg": "rgba(79,70,229,0.13)", "word_bg": "rgba(79,70,229,0.30)",
        "select_bg": "rgba(147,197,253,0.65)", "palette_bg": "#FFFFFF", "toast_bg": "#1A1A1F",
        "shadow": "1",
    },
}

# Vertical rhythm and layout, in px (paragraph_gap_em is in multiples of the reader's font size).
SPACING = {
    "unit": 8,
    "line_height": 1.65,  # default; the Settings slider (1.2 - 2.0) overrides it
    "paragraph_gap_em": 1.3,  # space between paragraphs: 1.2 - 1.5 em
    "reader_margin": 56,  # left/right margin of the reader column: 48 - 64
    "reader_max_width": 720,  # text column width
    "card_padding": 24,
    "section_gap": 32,  # between sections of the Home view
}

# Highlight colours (name -> colour), matching the Speechify-style palette. `hex` is the swatch; the text background uses it
# with a transparency that suits each theme.
HIGHLIGHT_COLORS = {
    "yellow": {"hex": "#FED25B", "label": "Yellow"},
    "green": {"hex": "#80F0A4", "label": "Green"},
    "pink": {"hex": "#FF77C9", "label": "Pink"},
    "purple": {"hex": "#D298FF", "label": "Purple"},
    "blue": {"hex": "#70D1FF", "label": "Blue"},
}
HIGHLIGHT_ALPHA = {"dark": 0.55, "light": 0.65}

# Reader fonts: label shown in Settings, the real family name, whether EchoRead ships the file (assets/fonts), generic fallback.
FONTS = (
    {"label": "Courier", "family": "Courier New", "bundled": False, "generic": "mono"},
    {"label": "Inter", "family": "Inter 18pt", "bundled": True, "generic": "sans"},
    {"label": "Georgia", "family": "Georgia", "bundled": False, "generic": "serif"},
    {"label": "Lora", "family": "Lora", "bundled": True, "generic": "serif"},
    {"label": "OpenDyslexic", "family": "OpenDyslexic", "bundled": True, "generic": "sans"},
    {"label": "Source Code Pro", "family": "Source Code Pro", "bundled": True, "generic": "mono"},
    {"label": "Source Sans 3", "family": "Source Sans 3", "bundled": True, "generic": "sans"},
    {"label": "Times New Roman", "family": "Times New Roman", "bundled": False, "generic": "serif"},
)
_GENERIC = {"serif": ("Georgia", "Times New Roman", "serif"), "sans": ("Segoe UI", "Arial", "sans-serif"),
            "mono": ("Consolas", "Courier New", "monospace")}

_QSS = Template("""
* { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 10pt; color: $body; }
QMainWindow, QWidget#root, QWidget#page { background: $bg; }
QDialog { background: $card; }
QFrame#sidebar { background: $sidebar; }
QFrame#topbar { background: $bg; }
QFrame#card { background: $card; border-radius: 16px; border: 1px solid $border; }
QFrame#pill { background: $card; border: 1px solid $border; border-radius: 26px; }
QFrame#statusbar { background: $sidebar; border-top: 1px solid $border; }
QFrame#sidepanel { background: $sidebar; border-left: 1px solid $border; }
QFrame#divider { background: $border; max-height: 1px; min-height: 1px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget { background: transparent; }
QLabel { background: transparent; }
QLabel#logo { color: $heading; font-size: 12pt; font-weight: 700; }
QLabel#h1 { color: $heading; font-size: 22pt; font-weight: 700; }
QLabel#h2 { color: $heading; font-size: 13pt; font-weight: 600; }
QLabel#doctitle { color: $heading; font-size: 21pt; font-weight: 700; }
QLabel#muted { color: $muted; font-size: 10pt; }
QLabel#warn { color: $warn; font-size: 10pt; }
QLabel#cardtitle { color: $heading; font-size: 12pt; font-weight: 600; }
QLabel#avatar { background: $accent; color: $on_accent; border-radius: 16px; font-weight: 700; }
QLabel#gutter { color: $accent; font-size: 11pt; }
QFrame#para { border-left: 3px solid transparent; border-radius: 6px; background: transparent; }
QFrame#para QLabel#paratext { color: $body; padding: 0px; }
QFrame#para[highlighted="true"] { background: $hl_bg; border-left: 3px solid $hl_bar; }
QFrame#para[state="selected"] { background: $para_sel_bg; border-left: 3px solid $para_sel_bar; }
QFrame#para[state="playing"] { background: $para_play_bg; border-left: 3px solid $accent; }
QFrame#para[state="playing"] QLabel#paratext { color: $heading; }
QFrame#para[state="skipped"] QLabel#paratext, QFrame#para[state="ignored"] QLabel#paratext { color: $para_dim; }
QPushButton { background: $card; color: $strong; border: 1px solid $border; border-radius: 10px; padding: 8px 16px; }
QPushButton:hover { background: $hover_strong; }
QPushButton:disabled { color: $disabled; }
QPushButton#primary { background: $accent; color: $on_accent; border: none; font-weight: 600; }
QPushButton#primary:hover { background: $accent_hover; }
QPushButton#primary:disabled { background: $hover_strong; color: $disabled; }
QPushButton#nav { background: transparent; border: none; text-align: left; padding: 10px 14px; color: $body; }
QPushButton#nav:hover { background: $hover; }
QPushButton#nav[active="true"] { background: $nav_active; color: $accent; font-weight: 600; }
QPushButton#tab { background: transparent; border: none; border-radius: 16px; padding: 7px 16px; color: $body; }
QPushButton#tab:checked { background: $card; color: $heading; border: 1px solid $border; }
QPushButton#sourcecard { background: $card; border: 1px solid $border; border-radius: 16px; padding: ${card_pad}px; text-align: left; color: $strong; font-size: 11pt; }
QPushButton#sourcecard:checked { border: 2px solid $accent; }
QPushButton#sourcecard:hover { border: 1px solid $accent; }
QPushButton#sourcecard:disabled { color: $disabled; background: $hover; }
QPushButton#listen { background: $accent; color: $on_accent; border: none; border-radius: 18px; padding: 11px 28px; font-weight: 700; font-size: 11pt; }
QPushButton#listen:hover { background: $accent_hover; }
QPushButton#round { background: transparent; border: none; border-radius: 18px; min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; padding: 0; color: $strong; font-size: 11pt; }
QPushButton#round:hover { background: $hover_strong; }
QPushButton#round:disabled { color: $disabled; }
QPushButton#speed { background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 4px 10px; color: $body; font-size: 9pt; }
QPushButton#speed:hover { background: $hover_strong; }
QPushButton#speed:checked { background: $accent; color: $on_accent; font-weight: 600; }
QToolButton#menubtn { background: transparent; border: none; padding: 8px 12px; border-radius: 8px; color: $strong; }
QToolButton#menubtn:hover { background: $hover_strong; }
QToolButton#menubtn:checked { background: $nav_active; color: $accent; }
QToolButton#menubtn::menu-indicator { image: none; }
QMenu { background: $card; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 22px; border-radius: 6px; color: $strong; }
QMenu::item:selected { background: $menu_sel; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox { background: $input_bg; color: $strong; border: 1px solid $border; border-radius: 8px; padding: 7px 10px; selection-background-color: $accent; selection-color: $on_accent; }
QPlainTextEdit#universal { background: $card; border: 1px solid $border; border-radius: 16px; padding: 16px; font-size: 11pt; }
QComboBox QAbstractItemView { background: $card; color: $strong; selection-background-color: $menu_sel; border: 1px solid $border; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url($chevron); width: 12px; height: 8px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 9px 12px; border-radius: 8px; color: $strong; }
QListWidget::item:hover { background: $hover; }
QListWidget::item:selected { background: $nav_active; color: $heading; }
QListWidget::item:disabled { color: $muted; font-size: 9pt; font-weight: 600; }
QProgressBar { background: $hover_strong; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QCheckBox, QRadioButton { color: $strong; spacing: 8px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll; border-radius: 4px; min-width: 30px; }
QToolTip { background: $card; color: $strong; border: 1px solid $border; padding: 4px; }
QSlider#volume::groove:horizontal { background: $hover_strong; height: 4px; border-radius: 2px; }
QSlider#volume::sub-page:horizontal { background: $accent; height: 4px; border-radius: 2px; }
QSlider#volume::handle:horizontal { background: $heading; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QSlider#volume::handle:horizontal:hover { background: $accent; }
QSlider::groove:horizontal { background: $hover_strong; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: $heading; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QLabel#volumelabel { color: $muted; font-size: 8pt; min-width: 26px; }
QFrame#palette { background: $palette_bg; border: 1px solid $border; border-radius: 16px; }
QPushButton#swatch { border: 2px solid transparent; border-radius: 13px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; padding: 0; }
QPushButton#swatch:hover { border: 2px solid $heading; }
QPushButton#swatch:checked { border: 2px solid $heading; }
QPushButton#palrow { background: transparent; border: none; border-radius: 8px; text-align: left; padding: 8px 12px; color: $strong; }
QPushButton#palrow:hover { background: $hover_strong; }
QLabel#toast { background: $toast_bg; color: #F4F4F8; border-radius: 14px; padding: 10px 18px; font-size: 10pt; }
QLabel#avatarbig { color: #FFFFFF; font-size: 20pt; font-weight: 700; border-radius: 32px; }
QLabel#preview { background: $input_bg; border: 1px solid $border; border-radius: 10px; padding: 14px; }
QFrame#sidepanel QLabel#hint { color: $muted; }
QPushButton#linkbtn { background: transparent; border: none; color: $accent; text-align: left; padding: 4px 0; }
QPushButton#linkbtn:hover { color: $accent_hover; }
QPlainTextEdit#details { background: $input_bg; border: 1px solid $border; border-radius: 8px; font-family: Consolas, "Courier New", monospace; font-size: 9pt; }
QListWidget#fontlist { background: $input_bg; border: 1px solid $border; border-radius: 10px; padding: 4px; }
QSlider { min-height: 22px; }
QWidget#settingsbody { background: transparent; }
""")


def tokens(name: str) -> dict:
    return THEMES["light" if name == "light" else "dark"]


def build_stylesheet(name: str) -> str:
    name = "light" if name == "light" else "dark"
    chevron = (icons_dir() / f"chevron_{name}.png").as_posix()
    return _QSS.substitute(tokens(name), chevron=chevron, card_pad=SPACING["card_padding"])


def apply_card_shadow(widget: QWidget, enabled: bool) -> None:
    """Light theme cards get a soft shadow; dark cards are flat."""
    if enabled:
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(24)
        effect.setOffset(0, 4)
        effect.setColor(QColor(20, 20, 40, 38))
        widget.setGraphicsEffect(effect)
    else:
        widget.setGraphicsEffect(None)


# ---------------------------------------------------------------------- colours
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)")


def qcolor(value: str) -> QColor:
    """'#RRGGBB' or 'rgba(r,g,b,a)' (a = 0..1, as used in the tokens) -> QColor."""
    m = _RGBA.fullmatch(value.strip())
    if m:
        alpha = float(m.group(4)) if m.group(4) is not None else 1.0
        return QColor(int(m.group(1)), int(m.group(2)), int(m.group(3)), round(alpha * 255))
    return QColor(value)


def highlight_fill(color_name: str, theme: str) -> QColor:
    """Background for highlighted text: the palette colour, see-through enough to keep the text readable."""
    base = QColor(HIGHLIGHT_COLORS.get(color_name, HIGHLIGHT_COLORS["yellow"])["hex"])
    base.setAlphaF(HIGHLIGHT_ALPHA["light" if theme == "light" else "dark"])
    return base


# ---------------------------------------------------------------------- icons (assets/icons/*.svg, line style)
def icons_dir() -> Path:
    return AppPaths.assets_dir() / "icons"


def svg_pixmap(name: str, color: str, size: int = 24, dpr: float = 2.0) -> QPixmap:
    """Render assets/icons/<name>.svg with its stroke/fill set to `color`. The files use `currentColor`."""
    svg = (icons_dir() / f"{name}.svg").read_text("utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    px = max(1, int(round(size * dpr)))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    return pm


def themed_icon(name: str, theme: str, normal: str = "icon", active: str = "accent", size: int = 24) -> QIcon:
    """Icon in the theme's icon colour (#FFFFFF dark / #0D0D0D light); `active` colour is used for hover/press.
    `normal`/`active` are token names (or '#RRGGBB'). The disabled look uses the theme's disabled colour."""
    tk = tokens(theme)
    icon = QIcon()
    icon.addPixmap(svg_pixmap(name, tk.get(normal, normal), size), QIcon.Mode.Normal)
    icon.addPixmap(svg_pixmap(name, tk.get(active, active), size), QIcon.Mode.Active)
    icon.addPixmap(svg_pixmap(name, tk["disabled"], size), QIcon.Mode.Disabled)
    return icon


# ---------------------------------------------------------------------- fonts
class FontLibrary:
    """Registers the fonts EchoRead ships (assets/fonts) and answers which reader fonts can be offered."""

    def __init__(self):
        self.loaded: list[str] = []
        self._done = False

    def load(self) -> list[str]:
        if self._done:
            return self.loaded
        self._done = True
        folder = AppPaths.assets_dir() / "fonts"
        families: set[str] = set()
        if folder.is_dir():
            for f in sorted(folder.rglob("*")):
                if f.suffix.lower() in (".ttf", ".otf"):
                    fid = QFontDatabase.addApplicationFont(str(f))
                    if fid >= 0:
                        families.update(QFontDatabase.applicationFontFamilies(fid))
        self.loaded = sorted(families)
        if "Inter 18pt" in families:
            QFont.insertSubstitution("Inter", "Inter 18pt")  # the stylesheet asks for "Inter"
        return self.loaded

    def options(self) -> list[dict]:
        """The fonts to list in Settings. A bundled font whose file isn't in assets/fonts is left out;
        system fonts (Georgia, Times New Roman, Courier New) are always listed, with `installed` telling if this PC has them."""
        installed = set(QFontDatabase.families())
        out = []
        for f in FONTS:
            have = f["family"] in installed or f["family"] in self.loaded
            if f["bundled"] and not have:
                continue
            out.append({**f, "installed": have})
        return out

    @staticmethod
    def entry(label: str) -> dict:
        for f in FONTS:
            if f["label"] == label:
                return f
        return next(f for f in FONTS if f["label"] == "Georgia")

    @classmethod
    def make_font(cls, label: str, pixel_size: float) -> QFont:
        """QFont for a picker label at `pixel_size` (the reader's 'px'; Qt gets points = px x 0.75)."""
        f = cls.entry(label)
        font = QFont()
        font.setFamilies([f["family"], *_GENERIC[f["generic"]]])
        font.setPointSizeF(round(pixel_size * 0.75, 1))
        return font
