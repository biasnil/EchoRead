"""Who is using EchoRead: a name, an avatar colour and an optional picture, kept on this computer and never sent anywhere."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .paths import AppPaths
from .settings import SettingsManager


class ProfileManager(QObject):
    changed = pyqtSignal()  # name / colour / picture / onboarded changed

    AVATAR_COLORS = ("#6366F1", "#EC4899", "#F59E0B", "#10B981", "#0EA5E9", "#8B5CF6")
    MAX_NAME = 40
    IMAGE_SIZE = 256  # the stored picture: a square, big enough for the Settings preview on a sharp screen
    MAX_IMAGE_BYTES = 40 * 1024 * 1024
    NOT_A_PICTURE = "That file couldn't be read as a picture. Try a PNG, JPG, GIF or WebP."

    def __init__(self, settings: SettingsManager, paths: AppPaths | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._paths = paths
        settings.changed.connect(lambda key: key.startswith("profile.") and self.changed.emit())

    # -- reading
    @property
    def name(self) -> str:
        return self._settings.profile_name

    @property
    def avatar_color(self) -> str:
        return self._settings.avatar_color

    @property
    def has_profile(self) -> bool:
        return bool(self.name)

    @property
    def needs_onboarding(self) -> bool:
        """True on the very first launch (and after an old install that had a hard-coded name)."""
        return not self._settings.onboarded

    @property
    def initial(self) -> str:
        return self.name[:1].upper() if self.name else ""

    @property
    def avatar_image_path(self) -> Path | None:
        """The stored picture, or None (no picture, or the file has gone missing)."""
        name = self._settings.avatar_image
        if not name or self._paths is None:
            return None
        path = self._paths.profile_dir / name
        return path if path.is_file() else None

    # -- writing
    @classmethod
    def clean_name(cls, raw: str) -> str:
        return " ".join(str(raw).split())[: cls.MAX_NAME]

    def save(self, name: str, color: str | None = None) -> bool:
        """Store the profile and mark first-run setup as done. An empty name is refused (returns False)."""
        name = self.clean_name(name)
        if not name:
            return False
        if color:
            self._settings.avatar_color = color
        self._settings.profile_name = name
        self._settings.onboarded = True
        return True

    def skip(self) -> None:
        """'Skip for now': no profile, but don't show the welcome dialog again."""
        self._settings.onboarded = True

    def clear(self) -> None:
        """Forget the name and the picture."""
        self.clear_avatar_image()
        self._settings.profile_name = ""

    # -- the picture
    @classmethod
    def prepare_image(cls, path):
        """Open any picture Pillow understands and return it as a centred square RGBA image of IMAGE_SIZE px.
        Raises ValueError (with a message fit for the user) if it isn't a readable picture."""
        from PIL import Image, ImageOps

        path = Path(path)
        try:
            if not path.is_file() or path.stat().st_size > cls.MAX_IMAGE_BYTES:
                raise ValueError(cls.NOT_A_PICTURE)
            with Image.open(path) as im:
                im.load()  # first frame of a GIF; forces decoding now so a broken file fails here
                im = ImageOps.exif_transpose(im).convert("RGBA")
        except ValueError:
            raise
        except Exception as exc:  # unreadable, truncated, not an image, decompression bomb...
            raise ValueError(cls.NOT_A_PICTURE) from exc
        side = min(im.size)
        left, top = (im.width - side) // 2, (im.height - side) // 2
        im = im.crop((left, top, left + side, top + side))
        return im.resize((cls.IMAGE_SIZE, cls.IMAGE_SIZE), Image.Resampling.LANCZOS)

    def set_avatar_image(self, image) -> None:
        """Keep a prepared picture (from prepare_image) as the profile picture. Needs a real AppPaths."""
        if self._paths is None:
            raise RuntimeError("ProfileManager was built without paths")
        self._paths.profile_dir.mkdir(parents=True, exist_ok=True)
        name = f"avatar-{int(time.time() * 1000)}.png"  # a new name each time, so every view notices the change
        image.save(self._paths.profile_dir / name, format="PNG")
        old = self._settings.avatar_image
        self._settings.avatar_image = name
        self._delete(old)
        self.changed.emit()

    def set_avatar_from_path(self, path) -> None:
        self.set_avatar_image(self.prepare_image(path))

    def clear_avatar_image(self) -> None:
        old = self._settings.avatar_image
        if old:
            self._settings.avatar_image = ""
            self._delete(old)
            self.changed.emit()

    def _delete(self, name: str) -> None:
        if name and self._paths is not None and name != self._settings.avatar_image:
            try:
                (self._paths.profile_dir / name).unlink()
            except OSError:
                pass