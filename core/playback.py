"""Glue between the paragraph model, the voice cache and the audio player.

Owns the rules: click a paragraph -> stop, jump, play; when a paragraph ends -> advance;
change speed -> keep the place inside the paragraph; audio not cached yet -> wait for it.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .models import SPEEDS
from .paragraphs import ParagraphModel
from .player import AudioPlayer
from .state import AppState
from .voice_cache import VoiceCache


class PlaybackController(QObject):
    error = pyqtSignal(str)

    def __init__(self, state: AppState, model: ParagraphModel, cache: VoiceCache, player: AudioPlayer, parent=None):
        super().__init__(parent)
        self.state = state
        self.model = model
        self.cache = cache
        self.player = player
        self._pending: tuple[int, float, float] | None = None  # (paragraph, speed, start fraction)
        player.finished.connect(self._on_finished)
        cache.paragraph_ready.connect(self._on_ready)
        cache.failed.connect(self._on_cache_failed)

    # -- public commands
    def play_from(self, index: int, step: int = 1, fraction: float = 0.0) -> None:
        """Play paragraph `index` (or the next playable one). `fraction` starts part-way in (a clicked word)."""
        idx = self.model.next_playable(index, step)
        if idx < 0:
            self.stop()
        else:
            self._start(idx, fraction if idx == index else 0.0)

    def toggle(self) -> None:
        pb = self.state.playback
        if pb == "stopped":
            self.play_from(self.model.current if self.model.current >= 0 else 0)
        elif pb == "playing":
            self.player.pause()
            self.state.playback = "paused"
        elif pb == "paused":
            self.player.resume()
            self.state.playback = "playing"
        else:  # loading: cancel
            self.stop()

    def stop(self) -> None:
        self._pending = None
        self.player.stop()
        self.state.playback = "stopped"

    def step(self, delta: int) -> None:
        """Previous (-1) / next (+1) paragraph, crossing into the next/previous chapter at the ends.
        While reading it starts the new paragraph; otherwise it only moves the selection."""
        cur = self.model.current
        if cur < 0:
            return
        active = self.state.is_active
        idx = (self.model.next_playable if active else self.model.next_visible)(cur + delta, delta)
        if idx < 0:
            self.step_section(delta, keep_reading=active, at_end=(delta < 0))
        elif active:
            self._start(idx)
        else:
            self.model.set_current(idx)

    # -- chapters
    def step_section(self, delta: int, keep_reading: bool | None = None, at_end: bool = False) -> None:
        """Open the previous/next chapter (at its first paragraph, or its last when going back by paragraph)."""
        target = self.model.section_index + delta
        active = self.state.is_active if keep_reading is None else keep_reading
        if not 0 <= target < self.model.section_count:
            if active and delta > 0:
                self.stop()
            return
        last = self.model.sections[target].count - 1
        self.model.open_section(target, last if at_end else 0)
        if active:
            self.play_from(self.model.current, -1 if at_end else 1)

    def goto_section(self, index: int, local: int = 0) -> None:
        """Open a chapter (from the chapter list). Keeps reading if you were reading."""
        if not 0 <= index < self.model.section_count:
            return
        was_active = self.state.is_active
        if index != self.model.section_index:
            self.model.open_section(index, local)
        else:
            self.model.set_current(local)
        if was_active:
            self.play_from(self.model.current)

    def jump_to_global(self, global_index: int) -> None:
        """Play from a paragraph anywhere in the document (bookmarks)."""
        section, local = self.model.locate(global_index)
        if section != self.model.section_index:
            self.model.open_section(section, local)
        self.play_from(local)

    def restart_current(self) -> None:
        """Voice changed: re-read the paragraph in the new voice."""
        if self.state.is_active and self.model.current >= 0:
            self._start(self.model.current)

    def handle_ignored(self, index: int) -> None:
        if self.state.is_active and index == self.model.current:
            self.play_from(index + 1)

    def set_speed(self, speed: float) -> None:
        from .models import nearest_speed

        if nearest_speed(speed) == self.state.speed:
            return
        old_state = self.state.playback
        fraction = self.player.fraction if old_state in ("playing", "paused") else 0.0
        self.state.speed = speed
        speed = self.state.speed
        cur = self.model.current
        if cur < 0:
            return
        self.cache.set_focus(cur, speed)
        if old_state in ("playing", "paused", "loading"):
            self._start(cur, fraction, keep_paused=(old_state == "paused"))

    def speed_up(self) -> None:
        self._nudge_speed(+1)

    def speed_down(self) -> None:
        self._nudge_speed(-1)

    def _nudge_speed(self, delta: int) -> None:
        idx = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - self.state.speed))
        idx = max(0, min(len(SPEEDS) - 1, idx + delta))
        if SPEEDS[idx] != self.state.speed:
            self.set_speed(SPEEDS[idx])

    # -- internals
    def _start(self, index: int, fraction: float = 0.0, keep_paused: bool = False) -> None:
        speed = self.state.speed
        self._pending = None
        self.model.set_current(index)
        self.cache.set_focus(index, speed)
        got = self.cache.get(index, speed)
        if got is not None:
            self._begin(got, fraction, keep_paused)
        else:
            self._pending = (index, speed, fraction)
            self.player.stop()
            self.state.playback = "loading"
            self.cache.request(index, speed)

    def _begin(self, got, fraction: float, keep_paused: bool = False) -> None:
        audio, sr = got
        try:
            self.player.play(audio, sr, fraction)
        except Exception as exc:
            self._pending = None
            self.state.playback = "stopped"
            self.error.emit(str(exc) or exc.__class__.__name__)
            return
        if keep_paused:
            self.player.pause()
            self.state.playback = "paused"
        else:
            self.state.playback = "playing"

    def _on_ready(self, index: int, speed: float) -> None:
        p = self._pending
        if p is None or p[0] != index or abs(p[1] - speed) > 1e-9:
            return
        got = self.cache.get(index, speed)
        if got is None:
            return
        self._pending = None
        self._begin(got, p[2])

    def _on_cache_failed(self, message: str) -> None:
        if self._pending is not None:
            self._pending = None
            self.stop()
        self.error.emit(message)

    def _on_finished(self) -> None:
        if self.state.playback != "playing":
            return  # e.g. a voice preview ended
        nxt = self.model.next_playable(self.model.current + 1)
        if nxt >= 0:
            self._start(nxt)
            return
        # end of the chapter: carry on into the next one that has something to read
        while self.model.section_index + 1 < self.model.section_count:
            self.model.open_section(self.model.section_index + 1, 0)
            first = self.model.next_playable(0)
            if first >= 0:
                self._start(first)
                return
        self.stop()
