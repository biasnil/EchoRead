"""Disk cache of synthesized paragraphs at every speed, filled by one background worker.

Layout:  <cache>/<doc_hash>/<speed>/<paragraph>.flac      (speed like "0.75", "1.00", ...)
Order:   current paragraph and the next two at the active speed, the same paragraphs at the other
         speeds (so switching speed is instant), then the rest of the document forward, then wrapping.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import threading
import traceback
import uuid

import soundfile as sf
from PyQt6.QtCore import QObject, pyqtSignal

from .errors import get_logger
from .models import SPEEDS, is_picture, speed_key
from .paths import AppPaths
from .settings import SettingsManager
from .tts import TTSService
from .workers import TaskWorker


class VoiceCache(QObject):
    paragraph_ready = pyqtSignal(int, float)  # paragraph index, speed
    progress = pyqtSignal(int, int)  # cached, total (for the speeds being precached)
    failed = pyqtSignal(str)  # first synthesis error of a run
    cleared = pyqtSignal()
    disk_full = pyqtSignal(str)  # the disk ran out of space: old cached audio was deleted and the write retried

    LOOKAHEAD = 3

    def __init__(self, paths: AppPaths, tts: TTSService, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._tts = tts
        self._settings = settings
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._gen = 0
        self._texts: list[str] = []
        self._voice = ""
        self._doc_hash = ""
        self._cur = 0
        self._speed = 1.0
        self._done: set[tuple[int, str]] = set()
        self._failed: set[tuple[int, str]] = set()
        self._requests: list[tuple[int, float]] = []
        self._plan: list[tuple[int, float]] = []
        self._plan_pos = 0
        self._done_enabled = 0
        self._fail_reported_gen = -1
        self._quit = False
        self._paused = False  # set by clear_all(): no background precaching until the next start()
        self._starts = 0

    # -- naming
    @staticmethod
    def hash_for(voice_id: str, texts: list[str]) -> str:
        return hashlib.sha1((voice_id + "\x00" + "\x1e".join(texts)).encode("utf-8")).hexdigest()[:16]

    @property
    def doc_hash(self) -> str:
        return self._doc_hash

    def path_in(self, doc_hash: str, index: int, speed: float):
        return self._paths.cache_dir / doc_hash / speed_key(speed) / f"{index:05d}.flac"

    def path_for(self, index: int, speed: float):
        return self.path_in(self._doc_hash, index, speed)

    # -- lifecycle
    def start(self, texts: list[str], voice_id: str, current: int = 0, speed: float = 1.0) -> None:
        """Begin (or restart) precaching for a document. Cancels any earlier run."""
        with self._cond:
            self._gen += 1
            self._texts = list(texts)
            self._voice = voice_id
            self._doc_hash = self.hash_for(voice_id, texts)
            self._cur = max(0, current)
            self._speed = speed
            self._paused = False
            self._done = self._scan_existing()
            self._failed = set()
            self._requests = []
            self._rebuild_plan()
            self._recount()
            if self._texts:
                self._ensure_thread()
                self._touch()
            self._starts += 1
            run_prune = self._starts % 20 == 1  # the first start of a session, then every 20th chapter
            self._cond.notify_all()
        if run_prune:
            threading.Thread(target=self._safe_prune, name="cache-prune", daemon=True).start()
        self._emit_progress()

    def stop(self) -> None:
        """Cancel precaching (document closed or replaced)."""
        with self._cond:
            self._gen += 1
            self._texts = []
            self._plan = []
            self._requests = []
            self._cond.notify_all()

    def shutdown(self, wait: float = 0.0) -> None:
        """Stop for good. `wait`: seconds to give the worker thread to finish what it is doing (0 = don't wait)."""
        with self._cond:
            self._gen += 1
            self._texts = []
            self._quit = True
            self._cond.notify_all()
        thread = self._thread
        if wait > 0 and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(wait)

    def set_focus(self, current: int, speed: float) -> None:
        """The reader moved or changed speed: re-prioritise."""
        with self._cond:
            if not self._texts:
                return
            speed_changed = abs(speed - self._speed) > 1e-9
            self._cur, self._speed = current, speed
            self._rebuild_plan()
            if speed_changed:  # the set of speeds being prepared includes the active one
                self._recount()
            self._cond.notify_all()
        self._emit_progress()

    def request(self, index: int, speed: float) -> None:
        """Highest priority: synthesize this now (playback is waiting for it)."""
        with self._cond:
            key = (index, speed_key(speed))
            if key in self._done:
                return
            self._requests = [r for r in self._requests if r[0] != index or speed_key(r[1]) != key[1]]
            self._requests.insert(0, (index, speed))
            self._cond.notify_all()

    # -- lookup
    def has(self, index: int, speed: float) -> bool:
        return self.path_for(index, speed).exists()

    def get(self, index: int, speed: float):
        path = self.path_for(index, speed)
        if not path.exists():
            return None
        try:
            audio, sr = sf.read(str(path), dtype="float32")
        except Exception:
            return None
        return audio, int(sr)

    def timings_path(self, index: int, speed: float):
        return self.path_for(index, speed).with_suffix(".words.json")

    def get_word_timings(self, index: int, speed: float):
        """[[char_start, char_end, start_fraction, end_fraction], ...] the speech model reported for this paragraph, or None."""
        try:
            data = json.loads(self.timings_path(index, speed).read_text("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, list) and data else None

    def read(self, doc_hash: str, index: int, speed: float):
        """Like get(), for any cached chapter (used by exports of chapters that aren't open)."""
        path = self.path_in(doc_hash, index, speed)
        if not path.exists():
            return None
        try:
            audio, sr = sf.read(str(path), dtype="float32")
        except Exception:
            return None
        return audio, int(sr)

    def write(self, doc_hash: str, index: int, speed: float, audio, sr: int) -> None:
        self._write_to(self.path_in(doc_hash, index, speed), audio, sr)

    def put(self, index: int, speed: float, audio, sr: int) -> None:
        """Store audio produced elsewhere (e.g. by an export) so it isn't synthesized twice."""
        self._write_file(index, speed, audio, sr)
        with self._cond:
            key = (index, speed_key(speed))
            if key not in self._done:
                self._done.add(key)
                if self._is_enabled(speed):
                    self._done_enabled += 1

    def clear_all(self) -> None:
        with self._cond:
            self._gen += 1
            shutil.rmtree(self._paths.cache_dir, ignore_errors=True)
            self._paths.cache_dir.mkdir(parents=True, exist_ok=True)
            self._done = set()
            self._done_enabled = 0
            self._failed = set()
            self._requests = []
            self._paused = True  # stay empty; playback still synthesizes what it needs on demand
            self._rebuild_plan()
            self._cond.notify_all()
        self.cleared.emit()
        self.progress.emit(0, 0)

    # -- size limit: the oldest-used chapters go first, never the one that's open
    def _touch(self) -> None:
        try:
            d = self._paths.cache_dir / self._doc_hash
            d.mkdir(parents=True, exist_ok=True)
            (d / ".used").touch()
        except OSError:
            pass

    def _safe_prune(self) -> None:
        try:
            self.prune()
        except Exception:
            pass

    def prune(self, max_bytes: int | None = None) -> int:
        """Delete least-recently-used cached chapters until the cache fits. Returns bytes freed."""
        limit = self._settings.cache_limit_mb * 1024 * 1024 if max_bytes is None else max_bytes
        if limit <= 0 or not self._paths.cache_dir.exists():
            return 0
        entries = []
        for d in self._paths.cache_dir.iterdir():
            if not d.is_dir():
                continue
            size = 0
            for root, _dirs, files in os.walk(d):
                for name in files:
                    try:
                        size += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
            stamp = d / ".used"
            try:
                used = stamp.stat().st_mtime if stamp.exists() else d.stat().st_mtime
            except OSError:
                used = 0
            entries.append((used, size, d))
        total, freed = sum(e[1] for e in entries), 0
        keep = self._doc_hash
        for used, size, d in sorted(entries, key=lambda e: e[0]):
            if total <= limit:
                break
            if d.name == keep:
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            freed += size
        return freed

    def free_space(self) -> int:
        """Emergency clean-up for a full disk: delete every cached chapter except the open one. Returns bytes freed."""
        freed, keep = 0, self._doc_hash
        if not self._paths.cache_dir.exists():
            return 0
        for d in list(self._paths.cache_dir.iterdir()):
            if d.is_dir() and d.name != keep:
                for root, _dirs, files in os.walk(d):
                    for name in files:
                        try:
                            freed += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
                shutil.rmtree(d, ignore_errors=True)
        return freed

    def size_bytes(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self._paths.cache_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    # -- planning (call with the lock held)
    def _enabled_speeds(self) -> tuple[float, ...]:
        """The speeds prepared in the background: the ones chosen in Settings plus the one being listened to."""
        chosen = list(self._settings.precache_speeds) + [self._speed]
        return tuple(s for s in SPEEDS if any(abs(s - c) < 1e-9 for c in chosen))

    def _is_enabled(self, speed: float) -> bool:
        return any(abs(speed - s) < 1e-9 for s in self._enabled_speeds())

    def _scan_existing(self) -> set[tuple[int, str]]:
        done = set()
        n = len(self._texts)
        for sp in SPEEDS:
            d = self._paths.cache_dir / self._doc_hash / speed_key(sp)
            if d.is_dir():
                for f in d.glob("*.flac"):
                    try:
                        idx = int(f.stem)
                    except ValueError:
                        continue
                    if idx < n:
                        done.add((idx, speed_key(sp)))
        return done

    def _recount(self) -> None:
        keys = {speed_key(s) for s in self._enabled_speeds()}
        self._done_enabled = sum(1 for (_i, k) in self._done if k in keys)

    def _total(self) -> int:
        return 0 if self._paused else sum(1 for x in self._texts if not is_picture(x)) * len(self._enabled_speeds())

    def _rebuild_plan(self) -> None:
        if self._paused:
            self._plan, self._plan_pos = [], 0
            return
        n = len(self._texts)
        act = self._speed
        speeds = self._enabled_speeds()
        others = [s for s in speeds if abs(s - act) > 1e-9]
        cur = min(self._cur, max(n - 1, 0))
        near = list(range(cur, min(cur + self.LOOKAHEAD, n)))
        far = list(range(min(cur + self.LOOKAHEAD, n), n))
        behind = list(range(0, cur))
        plan: list[tuple[int, float]] = []
        plan += [(i, act) for i in near]
        plan += [(i, s) for i in near for s in others]
        plan += [(i, act) for i in far]
        plan += [(i, s) for i in far for s in others]
        plan += [(i, act) for i in behind]
        plan += [(i, s) for i in behind for s in others]
        self._plan = [(i, s) for (i, s) in plan if (i, speed_key(s)) not in self._done and not is_picture(self._texts[i])]
        self._plan_pos = 0

    def _pop_job(self):
        while self._requests:
            index, speed = self._requests.pop(0)
            if index < len(self._texts) and (index, speed_key(speed)) not in self._done:
                return self._gen, index, speed, self._texts[index], self._voice
        while self._plan_pos < len(self._plan):
            index, speed = self._plan[self._plan_pos]
            self._plan_pos += 1
            key = (index, speed_key(speed))
            if key not in self._done and key not in self._failed:
                return self._gen, index, speed, self._texts[index], self._voice
        return None

    # -- worker
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="voice-cache", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                job = None
                while job is None:
                    if self._quit:
                        return
                    job = self._pop_job()
                    if job is None:
                        self._cond.wait()
                gen, index, speed, text, voice = job
            try:
                timed = getattr(self._tts, "synthesize_timed", None)
                if timed is not None:
                    audio, sr, timings = timed(text, voice, speed)
                else:
                    (audio, sr), timings = self._tts.synthesize(text, voice, speed), None
            except Exception as exc:  # report once per run, don't retry forever
                traceback.print_exc()  # raw output (console.log in a windowed build)
                get_logger("voice_cache").error("speech synthesis failed for paragraph %s at %sx", index, speed, exc_info=True)
                first = False
                with self._cond:
                    if gen == self._gen:
                        self._failed.add((index, speed_key(speed)))
                        if self._fail_reported_gen != gen:
                            self._fail_reported_gen = gen
                            first = True
                if first:
                    try:
                        self.failed.emit(TaskWorker.friendly(exc))
                    except RuntimeError:
                        return
                continue
            with self._cond:
                if gen != self._gen:
                    continue  # the document changed while this was synthesizing
            try:
                self._write_file(index, speed, audio, sr)
                if timings:
                    self.timings_path(index, speed).write_text(json.dumps(timings), "utf-8")
            except OSError as exc:
                get_logger("voice_cache").error("could not write cached audio", exc_info=True)
                with self._cond:
                    self._failed.add((index, speed_key(speed)))
                try:
                    self.failed.emit(f"Could not save audio to the cache: {exc}")
                except RuntimeError:
                    return
                continue
            with self._cond:
                if gen != self._gen:
                    continue
                key = (index, speed_key(speed))
                if key not in self._done:
                    self._done.add(key)
                    if self._is_enabled(speed):
                        self._done_enabled += 1
            try:
                self.paragraph_ready.emit(index, float(speed))
                self._emit_progress()
            except RuntimeError:  # Qt object already destroyed (application exiting)
                return

    def _write_file(self, index: int, speed: float, audio, sr: int) -> None:
        self._write_to(self.path_for(index, speed), audio, sr)

    @staticmethod
    def _is_disk_full(exc: BaseException) -> bool:
        return getattr(exc, "errno", None) == errno.ENOSPC or "no space left" in str(exc).lower() or "disk full" in str(exc).lower()

    def _write_to(self, path, audio, sr: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")  # unique: the precache and an export may write the same file at once
            try:
                sf.write(str(tmp), audio, sr, format="FLAC", subtype="PCM_16")
                os.replace(tmp, path)
                return
            except (OSError, RuntimeError) as exc:  # soundfile raises RuntimeError for some write failures
                try:
                    tmp.unlink()
                except OSError:
                    pass
                if attempt == 2 or not self._is_disk_full(exc):
                    raise
                freed = self.free_space()
                get_logger("voice_cache").warning("disk full: deleted %.1f MB of old cached audio and retried", freed / 1048576)
                self.disk_full.emit(f"The disk is full, so EchoRead deleted {freed / 1048576:.0f} MB of old cached audio and carried on.")
                path.parent.mkdir(parents=True, exist_ok=True)

    def _emit_progress(self) -> None:
        with self._cond:
            done, total = self._done_enabled, self._total()
        self.progress.emit(min(done, total), total)
