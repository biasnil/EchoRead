"""settings.json with typed getters/setters and change signals.

On disk the file is grouped (`profile`, `playback`, `reader`, plus a few top-level keys); in memory it is a flat dict with
dotted keys ("playback.volume"), and `changed(key)` carries the dotted key. Older flat files (speed, font_size, user_name)
are migrated on load and rewritten in the new layout.
"""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import QObject, pyqtSignal

from .models import RENAMED_VOICES, SPEEDS, nearest_speed
from .paths import AppPaths

GROUPS = ("profile", "playback", "reader")


class SettingsManager(QObject):
    changed = pyqtSignal(str)  # key that changed, e.g. "theme" or "reader.font_size"
    internet_toggled = pyqtSignal(bool)
    theme_changed = pyqtSignal(str)

    FONT_SIZE_RANGE = (12, 28)
    LINE_SPACING_RANGE = (1.2, 2.0)
    LEGACY_DEFAULT_FONT_SIZE = 17  # the old default; an untouched old file must not pin the reader to 17

    DEFAULTS = {
        "theme": "dark",
        "voice": "af_heart",
        "device": "CPU",
        "ocr_engine": "auto",  # "auto": use a PDF's own text when it has any; "ocr": always OCR
        "ocr_lang": "en",
        "internet_usage": False,
        "precache_speeds": [1.0],  # speeds prepared in the background besides the one being listened to (default: just 1.0x)
        "cache_limit_mb": 5120,  # oldest cached chapters are deleted beyond this; 0 = no limit
        "profile.name": "",
        "profile.avatar_color": "#6366F1",
        "profile.avatar_image": "",  # file name inside <root>/profile, or "" for none
        "profile.onboarded": False,
        "playback.volume": 100,
        "playback.speed": 1.0,
        "reader.font_family": "Georgia",
        "reader.font_size": 18,
        "reader.line_spacing": 1.65,
        "reader.highlight_sentence": True,
        "reader.highlight_word": True,
    }

    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._data = dict(self.DEFAULTS)
        self.load()

    # -- persistence
    @classmethod
    def _flatten(cls, stored: dict) -> tuple[dict, bool]:
        """Stored JSON (grouped, or the old flat layout) -> ({dotted key: value}, migrated?)."""
        flat: dict = {}
        migrated = False
        for key, value in stored.items():
            if key in GROUPS and isinstance(value, dict):
                for sub, v in value.items():
                    flat[f"{key}.{sub}"] = v
            else:
                flat[key] = value
        # old flat layout -> grouped
        if "speed" in flat:
            flat.setdefault("playback.speed", flat.pop("speed"))
            migrated = True
        if "font_size" in flat:
            old = flat.pop("font_size")
            if old != cls.LEGACY_DEFAULT_FONT_SIZE:
                flat.setdefault("reader.font_size", old)
            migrated = True
        if "precache_all_speeds" in flat:  # replaced by a list of chosen speeds; the new default (1.0x only) applies
            flat.pop("precache_all_speeds")
            migrated = True
        if "user_name" in flat:  # the old default was a hard-coded name: drop it, first-run setup asks instead
            flat.pop("user_name")
            migrated = True
        return flat, migrated

    def load(self) -> None:
        try:
            stored = json.loads(self._paths.settings_file.read_text("utf-8"))
        except Exception:
            return
        if not isinstance(stored, dict):
            return
        flat, migrated = self._flatten(stored)
        for key in self.DEFAULTS:
            if key in flat:
                self._data[key] = flat[key]
        self._data["playback.speed"] = nearest_speed(self._data["playback.speed"])
        if self._data["voice"] in RENAMED_VOICES:  # a saved choice of a voice that never existed
            self._data["voice"] = RENAMED_VOICES[self._data["voice"]]
            migrated = True
        if migrated:
            try:
                self.save()
            except OSError:
                pass

    def _grouped(self) -> dict:
        out: dict = {}
        for key, value in self._data.items():
            if "." in key:
                group, sub = key.split(".", 1)
                out.setdefault(group, {})[sub] = value
            else:
                out[key] = value
        return out

    def save(self) -> None:
        self._paths.root.mkdir(parents=True, exist_ok=True)
        tmp = self._paths.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._grouped(), indent=2), "utf-8")
        os.replace(tmp, self._paths.settings_file)

    def _set(self, key: str, value) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()
        self.changed.emit(key)
        if key == "internet_usage":
            self.internet_toggled.emit(bool(value))
        elif key == "theme":
            self.theme_changed.emit(str(value))

    def reset(self) -> None:
        """Back to defaults, except who you are (profile)."""
        for key, value in self.DEFAULTS.items():
            if not key.startswith("profile."):
                self._set(key, value)

    # -- typed access
    @property
    def theme(self) -> str:
        return "light" if self._data["theme"] == "light" else "dark"

    @theme.setter
    def theme(self, value: str) -> None:
        self._set("theme", "light" if value == "light" else "dark")

    @property
    def voice(self) -> str:
        return str(self._data["voice"])

    @voice.setter
    def voice(self, value: str) -> None:
        self._set("voice", str(value))

    @property
    def speed(self) -> float:
        return float(self._data["playback.speed"])

    @speed.setter
    def speed(self, value: float) -> None:
        self._set("playback.speed", nearest_speed(value))

    @property
    def volume(self) -> int:
        return max(0, min(100, int(self._data["playback.volume"])))

    @volume.setter
    def volume(self, value: int) -> None:
        self._set("playback.volume", max(0, min(100, int(round(value)))))

    @property
    def device(self) -> str:
        return "GPU" if self._data["device"] == "GPU" else "CPU"

    @device.setter
    def device(self, value: str) -> None:
        self._set("device", "GPU" if value == "GPU" else "CPU")

    @property
    def ocr_engine(self) -> str:
        return "ocr" if self._data["ocr_engine"] == "ocr" else "auto"

    @ocr_engine.setter
    def ocr_engine(self, value: str) -> None:
        self._set("ocr_engine", "ocr" if value == "ocr" else "auto")

    @property
    def ocr_lang(self) -> str:
        return str(self._data["ocr_lang"] or "en")

    @ocr_lang.setter
    def ocr_lang(self, value: str) -> None:
        self._set("ocr_lang", str(value).strip() or "en")

    @property
    def internet_usage(self) -> bool:
        return bool(self._data["internet_usage"])

    @internet_usage.setter
    def internet_usage(self, value: bool) -> None:
        self._set("internet_usage", bool(value))

    @staticmethod
    def _clean_speeds(raw) -> list[float]:
        """Only real speeds from SPEEDS, each once, in order. Anything else is ignored."""
        values = []
        try:
            items = list(raw)
        except TypeError:
            return []
        for v in items:  # one bad entry must not throw away the good ones
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                pass
        return [s for s in SPEEDS if any(abs(s - v) < 1e-9 for v in values)]

    @property
    def precache_speeds(self) -> tuple[float, ...]:
        """Speeds to prepare in the background in addition to the one being listened to (which is always prepared)."""
        raw = self._data["precache_speeds"]
        if not isinstance(raw, (list, tuple)):
            raw = self.DEFAULTS["precache_speeds"]
        return tuple(self._clean_speeds(raw))

    @precache_speeds.setter
    def precache_speeds(self, value) -> None:
        self._set("precache_speeds", self._clean_speeds(value))

    @property
    def cache_limit_mb(self) -> int:
        return max(0, int(self._data["cache_limit_mb"]))

    @cache_limit_mb.setter
    def cache_limit_mb(self, value: int) -> None:
        self._set("cache_limit_mb", max(0, int(value)))

    # -- reader appearance
    @property
    def font_family(self) -> str:
        return str(self._data["reader.font_family"] or self.DEFAULTS["reader.font_family"])

    @font_family.setter
    def font_family(self, value: str) -> None:
        self._set("reader.font_family", str(value).strip() or self.DEFAULTS["reader.font_family"])

    @property
    def font_size(self) -> int:
        lo, hi = self.FONT_SIZE_RANGE
        return max(lo, min(hi, int(self._data["reader.font_size"])))

    @font_size.setter
    def font_size(self, value: int) -> None:
        lo, hi = self.FONT_SIZE_RANGE
        self._set("reader.font_size", max(lo, min(hi, int(value))))

    @property
    def line_spacing(self) -> float:
        lo, hi = self.LINE_SPACING_RANGE
        return max(lo, min(hi, round(float(self._data["reader.line_spacing"]), 2)))

    @line_spacing.setter
    def line_spacing(self, value: float) -> None:
        lo, hi = self.LINE_SPACING_RANGE
        self._set("reader.line_spacing", max(lo, min(hi, round(float(value), 2))))

    @property
    def highlight_sentence(self) -> bool:
        return bool(self._data["reader.highlight_sentence"])

    @highlight_sentence.setter
    def highlight_sentence(self, value: bool) -> None:
        self._set("reader.highlight_sentence", bool(value))

    @property
    def highlight_word(self) -> bool:
        return bool(self._data["reader.highlight_word"])

    @highlight_word.setter
    def highlight_word(self, value: bool) -> None:
        self._set("reader.highlight_word", bool(value))

    # -- profile (used by core.profile.ProfileManager)
    @property
    def profile_name(self) -> str:
        return str(self._data["profile.name"] or "").strip()

    @profile_name.setter
    def profile_name(self, value: str) -> None:
        self._set("profile.name", str(value).strip())

    @property
    def avatar_color(self) -> str:
        return str(self._data["profile.avatar_color"] or self.DEFAULTS["profile.avatar_color"])

    @avatar_color.setter
    def avatar_color(self, value: str) -> None:
        self._set("profile.avatar_color", str(value))

    @property
    def avatar_image(self) -> str:
        return str(self._data["profile.avatar_image"] or "")

    @avatar_image.setter
    def avatar_image(self, value: str) -> None:
        self._set("profile.avatar_image", str(value or ""))

    @property
    def onboarded(self) -> bool:
        return bool(self._data["profile.onboarded"])

    @onboarded.setter
    def onboarded(self, value: bool) -> None:
        self._set("profile.onboarded", bool(value))