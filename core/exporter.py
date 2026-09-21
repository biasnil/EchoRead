"""Export text (.txt/.md/.json) and audio (.wav/.mp3), streaming to disk so nothing has to fit in memory."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .models import DocInfo, is_picture, speed_label
from .paragraphs import ParagraphModel
from .settings import SettingsManager
from .tts import TTSService
from .voice_cache import VoiceCache
from .workers import TaskWorker


class Exporter:
    GAP_SECONDS = 0.5
    TEXT_FORMATS = {".txt", ".md", ".json"}
    AUDIO_FORMATS = {".wav", ".mp3"}

    def __init__(self, model: ParagraphModel, cache: VoiceCache, tts: TTSService, settings: SettingsManager):
        self._model = model
        self._cache = cache
        self._tts = tts
        self._settings = settings

    # ------------------------------------------------------------------ text (the whole document)
    def export_text(self, path, doc: DocInfo | None) -> None:
        path = Path(path)
        ext = path.suffix.lower()
        title = doc.title if doc else "Untitled"
        model = self._model
        texts = model.document_texts()
        keep = [g for g in range(len(texts)) if not model.is_ignored(g) and not is_picture(texts[g])]
        if ext == ".json":
            marks, flagged = set(model.bookmarks()), set(model.highlights())
            data = {
                "title": title,
                "source": doc.source if doc else None,
                "kind": doc.kind if doc else None,
                "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "paragraphs": [{"index": g, "text": texts[g], "bookmarked": g in marks, "highlighted": g in flagged} for g in keep],
            }
            if model.section_count > 1:
                data["sections"] = [{"title": s.title, "start": s.start, "end": s.end} for s in model.sections]
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        elif ext == ".md":
            path.write_text(f"# {title}\n\n" + "\n\n".join(texts[g] for g in keep) + "\n", "utf-8")
        else:
            path.write_text("\n\n".join(texts[g] for g in keep) + "\n", "utf-8")

    # ------------------------------------------------------------------ audio
    def _audio_for(self, doc_hash: str, texts: list[str], index: int, speed: float):
        got = self._cache.read(doc_hash, index, speed)
        if got is not None:
            return got
        audio, sr = self._tts.synthesize(texts[index], self._settings.voice, speed)
        self._cache.write(doc_hash, index, speed, audio, sr)
        return audio, sr

    CHAPTER_GAP_SECONDS = 1.5  # silence between two chapters of one file

    def _render(self, worker: TaskWorker, path: Path, texts: list[str], indices: list[int], speed: float, prefix: str) -> bool:
        """Write the given paragraphs to one audio file, one paragraph at a time. False if cancelled."""
        return self._render_parts(worker, path, [(texts, indices)], speed, prefix)

    def _render_parts(self, worker: TaskWorker, path: Path, parts: list[tuple[list[str], list[int]]], speed: float, prefix: str) -> bool:
        """One audio file from several parts (each part = the texts of one chapter and which of them to read). Audio already
        made while reading (the cache) is reused. False if cancelled."""
        parts = [(texts, indices) for texts, indices in parts if indices]
        total = sum(len(indices) for _t, indices in parts)
        if not total:
            raise ValueError("There is nothing to export.")
        fmt = "MP3" if path.suffix.lower() == ".mp3" else "WAV"
        tmp = path.with_name(path.name + ".part")
        out = None
        done = 0
        try:
            for part_no, (texts, indices) in enumerate(parts):
                doc_hash = self._cache.hash_for(self._settings.voice, texts)
                for index in indices:
                    if worker.cancelled:
                        return False
                    pct = int(100 * done / total)
                    label = f"{prefix} — chapter {part_no + 1} / {len(parts)} — " if len(parts) > 1 else f"{prefix} — "
                    worker.report(f"{label}paragraph {done + 1} / {total} — {pct}%", pct)
                    audio, rate = self._audio_for(doc_hash, texts, index, speed)
                    if out is None:
                        out = sf.SoundFile(str(tmp), "w", samplerate=rate, channels=1, format=fmt)
                    out.write(np.asarray(audio, dtype=np.float32))
                    out.write(np.zeros(int(rate * self.GAP_SECONDS), dtype=np.float32))
                    done += 1
                if part_no < len(parts) - 1 and out is not None:
                    out.write(np.zeros(int(out.samplerate * self.CHAPTER_GAP_SECONDS), dtype=np.float32))
            out.close()
            out = None
            os.replace(tmp, path)
            return True
        finally:
            if out is not None:
                out.close()
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _readable(self, texts: list[str], base: int) -> list[int]:
        """Paragraph numbers (within `texts`, which start at global paragraph `base`) that are to be read."""
        model = self._model
        return [i for i in range(len(texts)) if not model.is_ignored(base + i) and not model.is_skipped(base + i) and not is_picture(texts[i])]

    def export_book_audio(self, worker: TaskWorker, path, speed: float) -> str:
        """The WHOLE document as one audio file: every chapter in order (one chapter after another, a pause between them)."""
        model = self._model
        if model.section_count < 2:
            return self.export_audio(worker, path, speed)
        texts_all = model.document_texts()
        parts = [(texts_all[sec.start:sec.end], self._readable(texts_all[sec.start:sec.end], sec.start)) for sec in model.sections]
        return str(path) if self._render_parts(worker, Path(path), parts, speed, "Exporting the whole book") else ""

    def export_chapter_texts(self, folder, ext: str = ".txt") -> str:
        """One text file per chapter, numbered, in a folder ("0001 - Title.txt")."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        model = self._model
        texts_all = model.document_texts()
        for si, sec in enumerate(model.sections):
            texts = texts_all[sec.start:sec.end]
            keep = [texts[i] for i in self._readable(texts, sec.start)]
            if not keep:
                continue
            title = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", sec.title or "Document")).strip()[:70]
            body = "\n\n".join(keep) + "\n"
            if ext == ".md":
                body = f"# {sec.title or title}\n\n" + body
            (folder / f"{si + 1:04d} - {title}{ext}").write_text(body, "utf-8")
        return str(folder)

    def export_audio(self, worker: TaskWorker, path, speed: float) -> str:
        """The open chapter (or the whole document if it isn't split) as one file. Skipped/ignored paragraphs are left out."""
        texts = self._model.all_texts()
        indices = [p.index for p in self._model if not p.ignored and not p.skip and not p.picture]
        return str(path) if self._render(worker, Path(path), texts, indices, speed, "Exporting") else ""

    def export_chapters(self, worker: TaskWorker, folder, speed: float, ext: str = ".mp3") -> str:
        """One audio file per chapter, numbered, in a folder. Chapters already exported are skipped, so a stopped
        export can be started again and carries on."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        model = self._model
        texts_all = model.document_texts()
        total = model.section_count
        for si, sec in enumerate(model.sections):
            texts = texts_all[sec.start:sec.end]
            if not texts:
                continue
            title = re.sub(r'[\\/:*?"<>|]+', " ", sec.title or "Document").strip()[:70]
            path = folder / f"{si + 1:04d} - {title}{ext}"
            if path.exists() and path.stat().st_size > 0:
                continue
            indices = [i for i in range(len(texts)) if not model.is_ignored(sec.start + i) and not model.is_skipped(sec.start + i) and not is_picture(texts[i])]
            if not indices:
                continue
            if not self._render(worker, path, texts, indices, speed, f"Chapter {si + 1} / {total}"):
                return ""
        return str(folder)

    def export_paragraph_folder(self, worker: TaskWorker, folder, speed: float) -> str:
        """0001.wav, 0002.wav, … plus index.json, one file per paragraph of the open chapter."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        texts = self._model.all_texts()
        doc_hash = self._cache.hash_for(self._settings.voice, texts)
        indices = [p.index for p in self._model if not p.ignored and not p.skip and not p.picture]
        entries = []
        for k, index in enumerate(indices):
            if worker.cancelled:
                return ""
            pct = int(100 * k / max(len(indices), 1))
            worker.report(f"Paragraph {k + 1} / {len(indices)} — Synthesizing speech — {pct}%", pct)
            audio, rate = self._audio_for(doc_hash, texts, index, speed)
            name = f"{k + 1:04d}.wav"
            sf.write(str(folder / name), audio, rate, format="WAV")
            entries.append({"file": name, "paragraph": index, "text": texts[index]})
        (folder / "index.json").write_text(
            json.dumps({"speed": speed_label(speed), "voice": self._settings.voice, "files": entries}, indent=2, ensure_ascii=False), "utf-8")
        return str(folder)
