"""Where EchoRead keeps its files (%APPDATA%/EchoRead on Windows)."""
from __future__ import annotations

import os
from pathlib import Path


class AppPaths:
    """Resolves every on-disk location. Pass a `root` to relocate everything (used by tests)."""

    def __init__(self, root: str | Path | None = None):
        if root is None:
            base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
            root = Path(base) / "EchoRead"
        self.root = Path(root)
        self.settings_file = self.root / "settings.json"
        self.library_file = self.root / "library.json"
        self.docs_dir = self.root / "docs"
        self.cache_dir = self.root / "cache"
        self.voices_dir = self.root / "voices"
        self.ocr_dir = self.root / "ocr"  # per-page OCR progress, so a stopped scan can resume
        self.highlights_dir = self.root / "highlights"  # one <doc id>.json per document: coloured text highlights
        self.profile_dir = self.root / "profile"  # the profile picture (a small square PNG copy; the original is never touched)
        self.log_dir = self.root / "logs"  # echoread.log (rotating) and console.log (library output of a windowed exe)
        self.log_file = self.log_dir / "echoread.log"

    def ensure(self) -> None:
        for d in (self.root, self.docs_dir, self.cache_dir, self.voices_dir, self.ocr_dir, self.highlights_dir, self.profile_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_root() -> Path:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return Path(base) / "EchoRead"

    @staticmethod
    def assets_dir() -> Path:
        from .frozen import resource_root  # the PyInstaller folder when frozen, the project folder otherwise

        return resource_root() / "assets"