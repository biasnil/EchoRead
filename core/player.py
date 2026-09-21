"""Audio output with instant pause/resume (own sounddevice stream; playback position is a sample counter)."""
from __future__ import annotations

import threading
import time

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .errors import get_logger


class NullOutputStream:
    """Stands in for a sound card that isn't there: it 'plays' in real time and throws the sound away,
    so the position, highlighting and auto-advance all keep working."""

    BLOCK = 1024

    def __init__(self, samplerate: int, callback):
        self._rate = max(1, int(samplerate))
        self._callback = callback
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="null-audio", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        block = np.zeros((self.BLOCK, 1), dtype=np.float32)
        while self._running:
            block.fill(0)
            self._callback(block, self.BLOCK, None, None)
            time.sleep(self.BLOCK / self._rate)

    def abort(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False


class AudioPlayer(QObject):
    POLL_MS = 33  # ~30 updates a second: drives the live word highlight

    finished = pyqtSignal()  # the audio played to its end (not emitted by stop())
    position_changed = pyqtSignal(float)  # 0..1 through the current audio
    state_changed = pyqtSignal(str)  # "playing" | "paused" | "stopped"
    error = pyqtSignal(str)
    volume_changed = pyqtSignal(int)
    device_unavailable = pyqtSignal(str)  # no usable sound output: playing silently instead (warn the user)

    def __init__(self, stream_factory=None, parent=None):
        """`stream_factory(samplerate, callback)` returns an object with start()/abort()/close()."""
        super().__init__(parent)
        self._factory = stream_factory or self._sounddevice_stream
        self._stream = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._paused = False
        self._ended = False
        self._volume = 100
        self._gain = 1.0
        self._silent = False  # True while running on the null output
        self._warned = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    @staticmethod
    def _sounddevice_stream(samplerate: int, callback):
        import sounddevice as sd

        return sd.OutputStream(samplerate=int(samplerate), channels=1, dtype="float32", callback=callback)

    # -- volume
    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, percent: int) -> None:
        """0..100. Takes effect on the very next audio block (no restart). The curve is squared so the slider feels even."""
        percent = max(0, min(100, int(round(percent))))
        self._volume = percent
        self._gain = (percent / 100.0) ** 2
        self.volume_changed.emit(percent)

    @property
    def is_silent(self) -> bool:
        """True when the sound card couldn't be opened and playback is going to the null output."""
        return self._silent

    # -- state
    @property
    def is_playing(self) -> bool:
        return self._stream is not None and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._stream is not None and self._paused

    @property
    def fraction(self) -> float:
        return self._pos / len(self._buf) if len(self._buf) else 0.0

    # -- control
    def play(self, audio, sample_rate: int, start_fraction: float = 0.0) -> None:
        self._close_stream()
        self._buf = np.asarray(audio, dtype=np.float32).reshape(-1)
        self._pos = int(max(0.0, min(start_fraction, 0.999)) * len(self._buf))
        self._paused = False
        self._ended = False
        self._silent = False
        try:
            self._stream = self._factory(sample_rate, self._callback)
            self._stream.start()
            self._warned = False
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            get_logger("player").warning("audio device unavailable, playing on the null output: %s", reason)
            try:
                self._stream and self._stream.close()
            except Exception:
                pass
            self._stream = NullOutputStream(sample_rate, self._callback)
            self._stream.start()
            self._silent = True
            if not self._warned:
                self._warned = True
                self.device_unavailable.emit(reason)
        self._timer.start()
        self.state_changed.emit("playing")

    def pause(self) -> None:
        if self._stream is not None and not self._paused:
            self._paused = True
            self.state_changed.emit("paused")

    def resume(self) -> None:
        if self._stream is not None and self._paused:
            self._paused = False
            self.state_changed.emit("playing")

    def stop(self) -> None:
        was_active = self._stream is not None
        self._close_stream()
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0
        if was_active:
            self.state_changed.emit("stopped")

    # -- internals
    def _callback(self, outdata, frames, time_info, status) -> None:  # runs on the audio thread
        if self._paused or self._ended:
            outdata.fill(0)
            return
        chunk = self._buf[self._pos:self._pos + frames]
        n = len(chunk)
        gain = self._gain
        outdata[:n, 0] = chunk if gain == 1.0 else chunk * gain
        if n < frames:
            outdata[n:] = 0
            self._pos = len(self._buf)
            self._ended = True
        else:
            self._pos += frames

    def _poll(self) -> None:  # runs on the GUI thread
        if self._stream is None:
            self._timer.stop()
            return
        if not self._paused:
            self.position_changed.emit(self.fraction)
        if self._ended:
            self._close_stream()
            self.state_changed.emit("stopped")
            self.finished.emit()

    def _close_stream(self) -> None:
        self._timer.stop()
        stream, self._stream = self._stream, None
        self._ended = False
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
