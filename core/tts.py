"""Kokoro text-to-speech (via PyKokoro) plus the catalog of preset voices and imported voice packs."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from .gpu import CudaLibraries
from .paths import AppPaths
from .settings import SettingsManager


class VoiceCatalog:
    """Kokoro presets + user-imported voice packs (nothing is cloned)."""

    # (PyKokoro language code, display name, voices)
    PRESETS = [
        ("en-us", "English (US)", ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "am_puck"]),
        ("en-gb", "English (UK)", ["bf_emma", "bf_isabella", "bm_george", "bm_lewis"]),
        ("es", "Spanish", ["ef_dora", "em_alex"]),
        ("fr-fr", "French", ["ff_siwis"]),
        ("it", "Italian", ["if_sara", "im_nicola"]),
        ("pt", "Portuguese (BR)", ["pf_dora", "pm_alex"]),
        ("ja", "Japanese (needs pyopenjtalk-plus)", ["jf_alpha", "jm_kumo"]),
        # Chinese uses a different model (Kokoro v1.1-zh) with numbered voices; the v1.0 names zf_xiaobei / zm_yunxi don't exist there.
        ("zh", "Chinese (needs kokorog2p[zh])", ["zf_001", "zf_002", "zf_003", "zf_004", "zm_009", "zm_010", "zm_011", "zm_012"]),
    ]
    REGIONS = {"a": "US", "b": "UK", "e": "ES", "f": "FR", "i": "IT", "p": "PT", "j": "JP", "z": "ZH"}
    SAMPLES = {
        "en-us": "Hello! This is a preview of my voice.",
        "en-gb": "Hello! This is a preview of my voice.",
        "es": "¡Hola! Esta es una muestra de mi voz.",
        "fr-fr": "Bonjour ! Voici un aperçu de ma voix.",
        "it": "Ciao! Questa è un'anteprima della mia voce.",
        "pt": "Olá! Esta é uma prévia da minha voz.",
        "ja": "こんにちは。これは私の声のプレビューです。",
        "zh": "你好，这是我的声音预览。",
    }
    STYLE_SHAPE = (511, 1, 256)  # a Kokoro v1 voice style array
    PACK_PREFIX = "pack:"

    def __init__(self, paths: AppPaths):
        self._paths = paths
        self._preset_lang = {v: code for code, _n, vs in self.PRESETS for v in vs}
        self.language_names = {code: name for code, name, _vs in self.PRESETS}

    # -- listing
    def is_pack(self, voice_id: str) -> bool:
        return voice_id.startswith(self.PACK_PREFIX)

    def _pack_meta(self, voice_id: str) -> dict | None:
        try:
            return json.loads((self._paths.voices_dir / f"{voice_id[len(self.PACK_PREFIX):]}.json").read_text("utf-8"))
        except Exception:
            return None

    def label(self, voice_id: str) -> str:
        if self.is_pack(voice_id):
            return f"{voice_id[len(self.PACK_PREFIX):]} (voice pack)"
        name = voice_id.split("_", 1)[-1].title()
        gender = "female" if voice_id[1:2] == "f" else "male"
        return f"{name} · {self.REGIONS.get(voice_id[:1], '')} {gender}"

    def language(self, voice_id: str) -> str:
        if self.is_pack(voice_id):
            meta = self._pack_meta(voice_id)
            return (meta or {}).get("lang", "en-us")
        return self._preset_lang.get(voice_id, "en-us")

    def sample_text(self, voice_id: str) -> str:
        return self.SAMPLES.get(self.language(voice_id), self.SAMPLES["en-us"])

    def list(self) -> list[dict]:
        out = []
        for code, name, voices in self.PRESETS:
            for v in voices:
                out.append({"id": v, "label": self.label(v), "group": name, "pack": False})
        if self._paths.voices_dir.exists():
            for meta_file in sorted(self._paths.voices_dir.glob("*.json")):
                vid = f"{self.PACK_PREFIX}{meta_file.stem}"
                out.append({"id": vid, "label": self.label(vid), "group": "Imported voice packs", "pack": True})
        return out

    def exists(self, voice_id: str) -> bool:
        return any(v["id"] == voice_id for v in self.list())

    # -- what PyKokoro should be given
    def resolve(self, voice_id: str):
        """Return (voice argument for PyKokoro, language code)."""
        if not self.is_pack(voice_id):
            return voice_id, self.language(voice_id)
        meta = self._pack_meta(voice_id)
        if meta is None:
            raise FileNotFoundError(f"Voice pack not found: {voice_id[len(self.PACK_PREFIX):]}")
        if meta.get("type") == "blend":
            return meta["blend"], meta.get("lang", "en-us")
        arr = np.load(self._paths.voices_dir / f"{meta['name']}.npy")
        return arr, meta.get("lang", "en-us")

    # -- importing packs
    def import_pack(self, path, lang: str = "en-us", name: str | None = None) -> str:
        """Import a Kokoro voice style (.npy/.npz/.bin) or a blend recipe (.json). Returns the voice id."""
        path = Path(path)
        name = re.sub(r"[^\w\- ]", "", name or path.stem).strip()
        if not name:
            raise ValueError("Please give the voice pack a name.")
        self._paths.voices_dir.mkdir(parents=True, exist_ok=True)
        ext = path.suffix.lower()
        if ext == ".json":
            data = json.loads(path.read_text("utf-8"))
            blend = data.get("blend")
            if not isinstance(blend, str) or not blend.strip():
                raise ValueError('A blend pack is JSON like {"blend": "af_heart:60,am_adam:40", "lang": "en-us"}.')
            meta = {"name": name, "type": "blend", "blend": blend.strip(), "lang": data.get("lang", lang)}
        elif ext in (".npy", ".npz", ".bin"):
            if ext == ".npy":
                arr = np.load(path, allow_pickle=False)
            elif ext == ".npz":
                with np.load(path, allow_pickle=False) as z:
                    if not z.files:
                        raise ValueError("That .npz file is empty.")
                    arr = z[z.files[0]]
            else:
                arr = np.fromfile(path, dtype=np.float32)
            arr = self._normalize_style(arr)
            np.save(self._paths.voices_dir / f"{name}.npy", arr)
            meta = {"name": name, "type": "style", "lang": lang}
        else:
            raise ValueError("Voice packs are .npy, .npz, .bin (Kokoro style arrays) or .json (blend recipes).")
        (self._paths.voices_dir / f"{name}.json").write_text(json.dumps(meta), "utf-8")
        return f"{self.PACK_PREFIX}{name}"

    def _normalize_style(self, arr: np.ndarray) -> np.ndarray:
        arr = np.asarray(arr)
        if not np.issubdtype(arr.dtype, np.floating):
            raise ValueError("A voice style must be a floating-point array.")
        arr = arr.astype(np.float32)
        if arr.ndim == 1 and arr.size == int(np.prod(self.STYLE_SHAPE)):
            arr = arr.reshape(self.STYLE_SHAPE)
        elif arr.ndim == 2 and arr.shape == (self.STYLE_SHAPE[0], self.STYLE_SHAPE[2]):
            arr = arr.reshape(self.STYLE_SHAPE)
        if arr.shape != self.STYLE_SHAPE:
            raise ValueError(f"Expected a Kokoro voice style of shape {self.STYLE_SHAPE}, got {tuple(arr.shape)}.")
        return arr

    def delete_pack(self, voice_id: str) -> None:
        name = voice_id[len(self.PACK_PREFIX):]
        for ext in (".json", ".npy"):
            try:
                (self._paths.voices_dir / f"{name}{ext}").unlink()
            except FileNotFoundError:
                pass


class TTSService(QObject):
    """Turns text into audio. One synthesis at a time (Kokoro is not re-entrant)."""

    status = pyqtSignal(str)
    download_progress = pyqtSignal(str, int)  # file name, percent 0..100 (100 = finished): the first-run model download

    def __init__(self, settings: SettingsManager, catalog: VoiceCatalog, engine_factory=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._catalog = catalog
        self._factory = engine_factory or self._make_kokoro
        self._engine = None
        self._engine_lock = threading.Lock()
        self._lock = threading.Lock()
        self._force_cpu = False  # set when the GPU couldn't start: everything after that runs on the CPU
        self._cuda = CudaLibraries()
        self._force_cpu = False  # set when the GPU turned out not to work; lasts until EchoRead restarts

    def _provider(self) -> str:
        return "cuda" if (self._settings.device == "GPU" and not self._force_cpu) else "cpu"

    GPU_FAILURE_WORDS = ("cudnn", "cuda", "execution provider", "loadlibrary", "cublas", "onnxruntime_providers", "gpu")

    @classmethod
    def _is_gpu_failure(cls, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(word in msg for word in cls.GPU_FAILURE_WORDS)

    @property
    def uses_gpu(self) -> bool:
        return self._settings.device == "GPU" and not self._force_cpu

    @staticmethod
    def _quiet_onnx_logging() -> None:
        """ONNX Runtime prints routine warnings (e.g. which operations it kept on the CPU). Show errors only."""
        try:
            import onnxruntime

            onnxruntime.set_default_logger_severity(3)
        except Exception:
            pass

    def _make_kokoro(self):
        from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

        def on_asset(event):
            if event.bytes_total:
                pct = int(100 * event.bytes_done // event.bytes_total)
                self.status.emit(f"Downloading {event.filename} — {pct}%")
                self.download_progress.emit(str(event.filename), pct)

        gpu = self.uses_gpu
        if gpu:
            self._cuda.prepare()  # find CUDA 13 / cuDNN 9 and make them visible before ONNX Runtime starts
        self._quiet_onnx_logging()
        config = PipelineConfig(
            voice="af_heart",
            generation=GenerationConfig(lang="en-us"),
            provider="cuda" if gpu else "cpu",
            asset_progress=on_asset,
        )
        return KokoroPipeline(config)

    def _ensure(self):
        with self._engine_lock:
            if self._engine is None:
                self.status.emit("Loading speech model (the first run downloads it)…")
                self._engine = self._factory()
            return self._engine

    GPU_ERROR_HINTS = ("cuda", "cudnn", "gpu", "execution provider", "loadlibrary", "onnxruntime")

    def _gpu_failure(self, exc: Exception) -> bool:
        return self._provider() == "cuda" and any(k in str(exc).lower() for k in self.GPU_ERROR_HINTS)

    def _fall_back_to_cpu(self, exc: Exception) -> None:
        """The GPU can't run the speech model: use the CPU (for this session) instead of failing every paragraph."""
        self._force_cpu = True
        with self._engine_lock:
            self._engine = None
        low = str(exc).lower()
        if "cudnn" in low:
            why, fix = "cuDNN wasn't found", 'pip install "onnxruntime-gpu[cuda,cudnn]"'
        elif "cpu-only" in low or "not available" in low:
            why, fix = "this onnxruntime has no CUDA support", "pip uninstall -y onnxruntime onnxruntime-gpu, then pip install -r requirements-gpu.txt"
        else:
            why, fix = "the CUDA libraries couldn't be loaded", 'pip install "onnxruntime-gpu[cuda,cudnn]" and update the NVIDIA driver'
        self.status.emit(f"GPU speech isn't working ({why}), so EchoRead is using the CPU. Fix: {fix}")

    def _run_timed(self, text: str, voice_id: str, speed: float):
        engine = self._ensure()
        voice, lang = self._catalog.resolve(voice_id)
        result = engine.run(text, lang=lang, voice=voice, generation={"speed": float(speed)})
        audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        return audio, int(result.sample_rate), self.word_timings_from(result, text, len(audio))

    @staticmethod
    def word_timings_from(result, text: str, n_samples: int):
        """The model's own word timings as [[char_start, char_end, start_fraction, end_fraction], ...], or None.

        pykokoro reports `result.word_timings` (sample positions of each source word) when the model can. They are only used
        if the character ranges really point at those words in `text`; otherwise the caller falls back to an estimate."""
        raw = getattr(result, "word_timings", None)
        if not raw or n_samples <= 0:
            return None
        out, matched = [], 0
        try:
            for w in raw:
                cs, ce = int(w.char_start), int(w.char_end)
                if not (0 <= cs < ce <= len(text)):
                    return None
                word = str(getattr(w, "text", "") or "").strip()
                if word and text[cs:ce].strip().strip(".,;:!?\"'()[]").lower() == word.strip(".,;:!?\"'()[]").lower():
                    matched += 1
                out.append([cs, ce, max(0.0, min(1.0, w.start_sample / n_samples)), max(0.0, min(1.0, w.end_sample / n_samples))])
        except (AttributeError, TypeError, ValueError):
            return None
        if not out or matched < 0.6 * len(out):
            return None
        return out

    def synthesize_timed(self, text: str, voice_id: str, speed: float):
        """Return (float32 mono audio, sample rate, word timings or None). If the GPU can't start, switch to the CPU and carry on."""
        with self._lock:
            try:
                return self._run_timed(text, voice_id, speed)
            except Exception as exc:
                if not (self.uses_gpu and self._is_gpu_failure(exc)):
                    raise
                with self._engine_lock:
                    self._force_cpu = True
                    self._engine = None
                first_line = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0][:160]
                self.status.emit("The GPU couldn't be used for speech (CUDA/cuDNN not found?), so the CPU is being used "
                                 f"instead. Details: {first_line}   (Run  python gpu_check.py  to see what is missing.)")
                return self._run_timed(text, voice_id, speed)

    def synthesize(self, text: str, voice_id: str, speed: float):
        """Return (float32 mono audio, sample rate)."""
        audio, sr, _timings = self.synthesize_timed(text, voice_id, speed)
        return audio, sr