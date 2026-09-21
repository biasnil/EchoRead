"""Stand-ins for the parts that need real hardware or models (speech engine, OCR, sound card)."""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.paths import AppPaths
from core.player import AudioPlayer
from core.services import Services

SR = 24000


class FakeTTS(QObject):
    """Returns a quiet sine wave whose length depends on the text. `timings` mimics pykokoro's word timings."""

    status = pyqtSignal(str)
    download_progress = pyqtSignal(str, int)

    def __init__(self, with_timings: bool = False, seconds_per_word: float = 0.12):
        super().__init__()
        self.with_timings = with_timings
        self.seconds_per_word = seconds_per_word
        self.calls: list[tuple] = []
        self.uses_gpu = False

    def _audio(self, text: str):
        n = max(1, len(text.split()))
        t = np.arange(int(SR * self.seconds_per_word * n)) / SR
        return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def synthesize(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        return self._audio(text), SR

    def synthesize_timed(self, text, voice, speed):
        audio, sr = self.synthesize(text, voice, speed)
        timings = None
        if self.with_timings:
            import re

            words = list(re.finditer(r"\S+", text))
            timings = [[m.start(), m.end(), i / len(words), (i + 1) / len(words)] for i, m in enumerate(words)]
        return audio, sr, timings


class FakeOCR(QObject):
    status = pyqtSignal(str)


class FakeStream:
    """A sound card you drive by hand: `pull(frames)` asks the player's callback for that many samples."""

    instances: list["FakeStream"] = []

    def __init__(self, samplerate, callback):
        self.rate, self.callback = samplerate, callback
        self.started = False
        self.closed = False
        self.out = []
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def abort(self):
        pass

    def close(self):
        self.closed = True

    def pull(self, frames: int):
        buf = np.zeros((frames, 1), dtype=np.float32)
        self.callback(buf, frames, None, None)
        self.out.append(buf[:, 0].copy())
        return buf[:, 0]


def fake_stream_factory(rate, callback):
    return FakeStream(rate, callback)


def make_services(root, with_timings: bool = False, **overrides) -> Services:
    FakeStream.instances.clear()
    overrides.setdefault("tts", FakeTTS(with_timings=with_timings))
    overrides.setdefault("ocr", FakeOCR())
    overrides.setdefault("player", AudioPlayer(stream_factory=fake_stream_factory))
    return Services.build(AppPaths(root), **overrides)


SAMPLE = (
    "Chapter one begins here. This is the first paragraph of the sample text, with a comma, and some words.\n\n"
    "The second paragraph is short.\n\n"
    "A third paragraph follows, and it is a little longer than the others so that it wraps over more than one line "
    "when the window is narrow, which lets us test clicking on a word inside it.\n\n"
    "Last paragraph."
)
