"""The object graph main.py builds and hands to the window."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import ErrorReporter
from .extractor import TextExtractor
from .exporter import Exporter
from .highlights import HighlightStore
from .library import Library
from .loader import DocumentLoader
from .ocr import OCRService
from .paragraphs import ParagraphModel
from .paths import AppPaths
from .player import AudioPlayer
from .playback import PlaybackController
from .profile import ProfileManager
from .regions import RegionMask
from .settings import SettingsManager
from .state import AppState
from .tts import TTSService, VoiceCatalog
from .voice_cache import VoiceCache
from .web import WebExtractor


@dataclass
class Services:
    paths: AppPaths
    settings: SettingsManager
    state: AppState
    catalog: VoiceCatalog
    loader: DocumentLoader
    ocr: OCRService
    regions: RegionMask
    model: ParagraphModel
    tts: TTSService
    cache: VoiceCache
    player: AudioPlayer
    playback: PlaybackController
    library: Library
    web: WebExtractor
    extractor: TextExtractor
    exporter: Exporter
    errors: ErrorReporter
    profile: ProfileManager
    highlights: HighlightStore

    @classmethod
    def build(cls, paths: AppPaths | None = None, **overrides) -> "Services":
        """Wire the default object graph. `overrides` swap in fakes (tests): tts=, ocr=, player=."""
        paths = paths or AppPaths()
        paths.ensure()
        errors = ErrorReporter(paths)  # logging starts first so everything after it can be logged
        settings = SettingsManager(paths)
        state = AppState()
        state.speed = settings.speed
        catalog = VoiceCatalog(paths)
        loader = DocumentLoader()
        ocr = overrides.get("ocr") or OCRService(settings)
        regions = RegionMask()
        model = ParagraphModel()
        tts = overrides.get("tts") or TTSService(settings, catalog)
        cache = VoiceCache(paths, tts, settings)
        player = overrides.get("player") or AudioPlayer()
        player.set_volume(settings.volume)
        settings.changed.connect(lambda key: key == "playback.volume" and player.set_volume(settings.volume))
        playback = PlaybackController(state, model, cache, player)
        library = Library(paths)
        web = WebExtractor(settings)
        extractor = TextExtractor(loader, ocr, regions, settings, store_image=paths.store_image)
        exporter = Exporter(model, cache, tts, settings)
        profile = ProfileManager(settings, paths)
        highlights = HighlightStore(paths)
        return cls(paths, settings, state, catalog, loader, ocr, regions, model, tts, cache, player,
                   playback, library, web, extractor, exporter, errors, profile, highlights)
