"""Logic tests for the v2 features: settings migration, profile, volume, word timing, highlights, errors, cache, loader."""
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from core.errors import ErrorReporter, get_logger
from core.highlights import HighlightStore
from core.paths import AppPaths
from core.player import AudioPlayer, NullOutputStream
from core.profile import ProfileManager
from core.settings import SettingsManager
from core.wordtiming import WordTimeline
from tests.fakes import FakeStream, FakeTTS, fake_stream_factory, make_services


# ------------------------------------------------------------------ settings
def test_defaults_and_grouped_file(qapp, root):
    paths = AppPaths(root)
    s = SettingsManager(paths)
    assert (s.volume, s.font_family, s.font_size, s.line_spacing) == (100, "Georgia", 18, 1.65)
    assert s.highlight_sentence and s.highlight_word and s.profile_name == "" and not s.onboarded
    s.volume = 40
    s.font_family = "Lora"
    s.profile_name = "Ana"
    data = json.loads(paths.settings_file.read_text("utf-8"))
    assert data["playback"]["volume"] == 40 and data["reader"]["font_family"] == "Lora" and data["profile"]["name"] == "Ana"
    assert data["theme"] == "dark" and "user_name" not in data
    again = SettingsManager(paths)
    assert (again.volume, again.font_family, again.profile_name) == (40, "Lora", "Ana")


def test_ranges_are_clamped(qapp, root):
    s = SettingsManager(AppPaths(root))
    s.volume, s.font_size, s.line_spacing = 500, 99, 9
    assert (s.volume, s.font_size, s.line_spacing) == (100, 28, 2.0)
    s.volume, s.font_size, s.line_spacing = -5, 1, 0.1
    assert (s.volume, s.font_size, s.line_spacing) == (0, 12, 1.2)


def test_old_flat_file_is_migrated_and_name_dropped(qapp, root):
    paths = AppPaths(root)
    paths.root.mkdir(parents=True)
    paths.settings_file.write_text(json.dumps({"theme": "light", "voice": "am_adam", "speed": 1.5, "font_size": 21,
                                               "user_name": "Iven", "device": "GPU"}), "utf-8")
    s = SettingsManager(paths)
    assert (s.theme, s.voice, s.speed, s.font_size, s.device) == ("light", "am_adam", 1.5, 21, "GPU")
    assert s.profile_name == "" and not s.onboarded  # the hard-coded name is gone; first-run setup will ask
    on_disk = json.loads(paths.settings_file.read_text("utf-8"))
    assert "user_name" not in on_disk and on_disk["playback"]["speed"] == 1.5 and on_disk["reader"]["font_size"] == 21


def test_old_default_font_size_does_not_stick(qapp, root):
    paths = AppPaths(root)
    paths.root.mkdir(parents=True)
    paths.settings_file.write_text(json.dumps({"font_size": 17}), "utf-8")
    assert SettingsManager(paths).font_size == 18


def test_reset_keeps_profile(qapp, root):
    s = SettingsManager(AppPaths(root))
    s.profile_name, s.onboarded, s.volume = "Ana", True, 20
    s.reset()
    assert s.profile_name == "Ana" and s.onboarded and s.volume == 100


# ------------------------------------------------------------------ profile
def test_profile_flow(qapp, root):
    s = SettingsManager(AppPaths(root))
    p = ProfileManager(s)
    seen = []
    p.changed.connect(lambda: seen.append(1))
    assert p.needs_onboarding and not p.has_profile and p.initial == ""
    assert p.save("   ") is False and p.needs_onboarding
    assert p.save("  ana   maria ", "#EC4899") and p.name == "ana maria" and p.initial == "A"
    assert p.avatar_color == "#EC4899" and not p.needs_onboarding and seen
    p.clear()
    assert not p.has_profile and not p.needs_onboarding


def test_skip_does_not_create_profile(qapp, root):
    p = ProfileManager(SettingsManager(AppPaths(root)))
    p.skip()
    assert not p.needs_onboarding and not p.has_profile


# ------------------------------------------------------------------ player: volume, null output
def test_volume_applies_live_without_restarting(qapp):
    player = AudioPlayer(stream_factory=fake_stream_factory)
    player.play(np.ones(4000, dtype=np.float32), 24000)
    stream = FakeStream.instances[-1]
    assert np.allclose(stream.pull(100), 1.0)
    player.set_volume(50)
    assert player.is_playing and FakeStream.instances[-1] is stream  # same stream: not restarted
    assert np.allclose(stream.pull(100), 0.25)  # squared curve: 50 % -> 0.25 amplitude
    player.set_volume(0)
    assert np.allclose(stream.pull(100), 0.0)
    player.set_volume(100)
    assert np.allclose(stream.pull(100), 1.0)


def test_volume_is_clamped_and_signalled(qapp):
    player = AudioPlayer(stream_factory=fake_stream_factory)
    seen = []
    player.volume_changed.connect(seen.append)
    player.set_volume(150)
    player.set_volume(-3)
    assert seen == [100, 0] and player.volume == 0


def test_no_sound_card_falls_back_to_silent_output(qapp):
    def broken(rate, cb):
        raise OSError("no default output device")

    player = AudioPlayer(stream_factory=broken)
    warned = []
    player.device_unavailable.connect(warned.append)
    finished = []
    player.finished.connect(lambda: finished.append(1))
    player.play(np.zeros(2400, dtype=np.float32), 24000)  # 0.1 s
    assert player.is_silent and player.is_playing and len(warned) == 1
    player.play(np.zeros(2400, dtype=np.float32), 24000)
    assert len(warned) == 1  # warned once, not on every paragraph
    deadline = time.time() + 3
    while not finished and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert finished, "the null output must still advance and finish so auto-advance works"


def test_null_stream_calls_back_in_real_time(qapp):
    calls = []
    s = NullOutputStream(8000, lambda out, frames, t, st: calls.append(frames))
    s.start()
    time.sleep(0.4)
    s.close()
    assert 1 <= len(calls) <= 6  # 1024 frames at 8 kHz = 0.128 s per block


# ------------------------------------------------------------------ word timeline
def test_estimated_timeline_is_monotonic_and_covers_all_words():
    text = "Hello, world. This is a test — it's 3.14 long!"
    tl = WordTimeline(text)
    assert tl.source == "estimate"
    assert [text[a:b] for a, b in (tl.word_span(i) for i in range(len(tl.words)))] == \
        ["Hello", "world", "This", "is", "a", "test", "it's", "3.14", "long"]
    starts = [w.f0 for w in tl.words]
    assert starts == sorted(starts) and starts[0] == 0 and tl.words[-1].f1 == pytest.approx(1.0)
    assert tl.word_at(0.0) == 0 and tl.word_at(0.999) == len(tl.words) - 1
    assert text[slice(*tl.sentence_span(tl.sentence_at(0.05)))] == "Hello, world."
    assert text[slice(*tl.sentence_span(tl.sentence_at(0.95)))] == "This is a test — it's 3.14 long!"


def test_click_position_maps_to_a_start_fraction():
    text = "one two three four"
    tl = WordTimeline(text)
    assert tl.fraction_at_char(0) == 0
    f = tl.fraction_at_char(text.index("three") + 2)  # in the middle of a word: that word's start
    assert f == pytest.approx(tl.words[2].f0, abs=1e-3) and f >= tl.words[2].f0 and tl.word_at(f) == 2
    assert tl.fraction_at_char(999) == pytest.approx(tl.words[-1].f0, abs=1e-3)


def test_cjk_characters_are_words():
    tl = WordTimeline("你好世界")
    assert len(tl.words) == 4


def test_model_timings_are_used_when_sane_and_ignored_when_not():
    text = "a b c"
    good = WordTimeline(text, [[0, 1, 0.0, 0.2], [2, 3, 0.3, 0.5], [4, 5, 0.6, 0.9]])
    assert good.source == "model" and good.word_at(0.35) == 1
    assert WordTimeline(text, [[0, 1, 0.0, 0.2]]).source == "estimate"  # too few words timed
    assert WordTimeline(text, [[0, 1, 0.5, 0.6], [2, 3, 0.1, 0.2], [4, 5, 0.6, 0.9]]).source == "estimate"  # goes backwards
    assert WordTimeline(text, [[0, 1, 0, 2], [2, 3, 0, 1], [4, 5, 0, 1]]).source == "estimate"  # fractions out of range
    assert WordTimeline(text, "garbage").source == "estimate"


def test_engine_timings_conversion():
    class W:
        def __init__(self, text, cs, ce, a, b):
            self.text, self.char_start, self.char_end, self.start_sample, self.end_sample = text, cs, ce, a, b

    class Result:
        word_timings = [W("Hi", 0, 2, 0, 1000), W("there.", 3, 9, 1000, 3000)]

    out = __import__("core.tts", fromlist=["TTSService"]).TTSService.word_timings_from(Result, "Hi there.", 4000)
    assert out == [[0, 2, 0.0, 0.25], [3, 9, 0.25, 0.75]]

    class Wrong:  # char ranges that don't point at those words
        word_timings = [W("zzz", 0, 2, 0, 1000), W("yyy", 3, 9, 1000, 3000)]

    assert __import__("core.tts", fromlist=["TTSService"]).TTSService.word_timings_from(Wrong, "Hi there.", 4000) is None
    assert __import__("core.tts", fromlist=["TTSService"]).TTSService.word_timings_from(object(), "Hi", 100) is None


# ------------------------------------------------------------------ highlights
def test_highlight_overlap_rules_and_persistence(qapp, root):
    paths = AppPaths(root)
    paths.ensure()
    store = HighlightStore(paths)
    store.open("doc1", "hash1")
    store.add(3, 10, 40, "yellow", 100)
    store.add(3, 20, 25, "pink", 100)  # inside the yellow one: splits it
    got = [(h.start, h.end, h.color) for h in store.for_paragraph(3)]
    assert got == [(10, 20, "yellow"), (20, 25, "pink"), (25, 40, "yellow")]
    assert store.at(3, 22).color == "pink" and store.at(3, 5) is None
    store.add(3, 0, 50, "blue", 100)  # covers everything
    assert [(h.start, h.end, h.color) for h in store.for_paragraph(3)] == [(0, 50, "blue")]
    store.add(4, 5, 5, "blue", 100)  # empty range ignored
    assert store.for_paragraph(4) == []
    store.flush()
    other = HighlightStore(paths)
    other.open("doc1", "hash1")
    assert [(h.para, h.start, h.end, h.color) for h in other.for_paragraph(3)] == [(3, 0, 50, "blue")]
    other.open("doc1", "changed text")  # different text: highlights don't apply
    assert other.count() == 0


def test_highlight_recolor_remove_and_signal(qapp, root):
    paths = AppPaths(root)
    paths.ensure()
    store = HighlightStore(paths)
    store.open("d", "h")
    seen = []
    store.changed.connect(seen.append)
    h = store.add(7, 2, 9, "yellow", 50)
    store.set_color(h.id, "green")
    assert store.get(h.id).color == "green"
    store.remove(h.id)
    assert store.count() == 0 and seen == [7, 7, 7]


def test_legacy_paragraph_flags_become_full_ranges(qapp, root):
    paths = AppPaths(root)
    paths.ensure()
    store = HighlightStore(paths)
    store.open("d", "h")
    made = store.import_paragraph_flags([1, 2, 99], lambda g: {1: 30, 2: 12}.get(g, 0))
    assert made == 2 and [(h.start, h.end) for h in store.for_paragraph(1)] == [(0, 30)]


# ------------------------------------------------------------------ errors and logging
def test_errors_are_logged_and_reported(qapp, root):
    paths = AppPaths(root)
    paths.ensure()
    reporter = ErrorReporter(paths)
    try:
        got = []
        reporter.error_occurred.connect(lambda summary, details: got.append((summary, details)))
        reporter.attach_ui(True)
        get_logger("unit").warning("a warning line")
        try:
            1 / 0
        except ZeroDivisionError:
            reporter.report(*sys.exc_info(), where="test")
        for h in logging.getLogger("echoread").handlers:
            h.flush()
        text = paths.log_file.read_text("utf-8")
        assert "a warning line" in text and "ZeroDivisionError" in text and "echoread.unit" in text and "ERROR" in text
        assert got and got[0][0].startswith("Something went wrong") and "ZeroDivisionError" in got[0][1]
    finally:
        reporter.close()


def test_excepthook_installs_and_restores(qapp, root):
    paths = AppPaths(root)
    paths.ensure()
    before = sys.excepthook
    reporter = ErrorReporter(paths)
    try:
        reporter.install()
        assert sys.excepthook != before
        got = []
        reporter.error_occurred.connect(lambda s, d: got.append(d))
        reporter.attach_ui(True)
        try:
            raise RuntimeError("from the hook")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        assert got and "from the hook" in got[0]
    finally:
        reporter.close()
    assert sys.excepthook == before


def test_log_rotation_settings(qapp, root):
    from logging.handlers import RotatingFileHandler

    paths = AppPaths(root)
    paths.ensure()
    reporter = ErrorReporter(paths)
    try:
        h = next(h for h in logging.getLogger("echoread").handlers if isinstance(h, RotatingFileHandler))
        assert h.maxBytes == 5 * 1024 * 1024 and h.backupCount == 3
        assert Path(h.baseFilename) == paths.root / "logs" / "echoread.log"
    finally:
        reporter.close()


# ------------------------------------------------------------------ voice cache: timings, disk full
def test_cache_stores_model_timings_next_to_audio(qapp, root):
    s = make_services(root, with_timings=True)
    s.cache.start(["alpha beta gamma", "delta"], "af_heart", 0, 1.0)
    deadline = time.time() + 5
    while s.cache.get_word_timings(0, 1.0) is None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    t = s.cache.get_word_timings(0, 1.0)
    s.cache.shutdown()
    assert t and t[0][:2] == [0, 5] and len(t) == 3


def test_disk_full_clears_old_cache_and_retries(qapp, root, monkeypatch):
    import errno

    import core.voice_cache as vc

    s = make_services(root)
    old = s.paths.cache_dir / "olddochash" / "1.00"
    old.mkdir(parents=True)
    (old / "00000.flac").write_bytes(b"x" * 2048)
    s.cache._doc_hash = "currentdoc"
    real_write, calls = vc.sf.write, {"n": 0}

    def flaky(path, audio, sr, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(path, audio, sr, **kw)

    monkeypatch.setattr(vc.sf, "write", flaky)
    warned = []
    s.cache.disk_full.connect(warned.append)
    s.cache.put(0, 1.0, np.zeros(2400, dtype=np.float32), 24000)
    assert calls["n"] == 2 and warned and not (s.paths.cache_dir / "olddochash").exists()
    assert s.cache.path_for(0, 1.0).exists()


def test_other_write_errors_still_raise(qapp, root, monkeypatch):
    import core.voice_cache as vc

    s = make_services(root)
    s.cache._doc_hash = "x"
    monkeypatch.setattr(vc.sf, "write", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        s.cache.put(0, 1.0, np.zeros(10, dtype=np.float32), 24000)


# ------------------------------------------------------------------ playback: start part-way in
def test_play_from_a_fraction(qapp, root):
    s = make_services(root)
    s.model.set_document(["one two three four five six seven eight", "next"])
    s.cache.start(s.model.all_texts(), "af_heart", 0, 1.0)
    deadline = time.time() + 5
    while not s.cache.has(0, 1.0) and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    s.playback.play_from(0, fraction=0.5)
    assert s.state.playback == "playing" and abs(s.player.fraction - 0.5) < 0.01
    s.cache.shutdown()


# ------------------------------------------------------------------ loader: damaged PDF fallback
def _make_pdf(path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Recoverable text on a page.")
    doc.save(str(path))
    doc.close()


def test_open_recovered_renders_with_pdfium(qapp, tmp_path):
    from core.loader import DocumentLoader

    pdf = tmp_path / "ok.pdf"
    _make_pdf(pdf)
    loader = DocumentLoader()
    assert loader.open_recovered(pdf) == 1
    assert loader.kind == "recovered" and loader.is_open and not loader.has_text_layer(0)
    img = loader.render_page(0, zoom=1.0)
    assert img.size[0] > 100 and loader.text_blocks(0) == []
    loader.close()
    assert not loader.is_open


def test_open_recovered_rejects_garbage(qapp, tmp_path):
    from core.loader import DocumentLoader

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(Exception):
        DocumentLoader().open_recovered(bad)


# ------------------------------------------------------------------ profile picture
def _picture(path, size=(300, 200), fmt=None):
    """Left/right thirds green, centre red, so a centre crop must come out red."""
    from PIL import Image

    im = Image.new("RGB", size, (0, 200, 0))
    w, h = size
    im.paste((220, 0, 0), ((w - h) // 2, 0, (w - h) // 2 + h, h))
    im.save(path, format=fmt)
    return path


def test_prepare_image_crops_to_a_centred_square(tmp_path):
    im = ProfileManager.prepare_image(_picture(tmp_path / "wide.png"))
    assert im.size == (256, 256) and im.mode == "RGBA"
    assert im.getpixel((128, 128))[:3] == (220, 0, 0) and im.getpixel((2, 128))[:3] == (220, 0, 0)  # only the red square is left
    tall = ProfileManager.prepare_image(_picture(tmp_path / "tall.png", (150, 400)))
    assert tall.size == (256, 256)


@pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp"), ("BMP", "bmp"), ("TIFF", "tif")])
def test_any_common_picture_format_works(tmp_path, fmt, ext):
    im = ProfileManager.prepare_image(_picture(tmp_path / f"p.{ext}", (120, 90), fmt))
    assert im.size == (256, 256)


def test_unreadable_files_are_refused_with_a_friendly_message(tmp_path):
    fake = tmp_path / "not_a_picture.png"
    fake.write_bytes(b"this is text, not a picture")
    for bad in (fake, tmp_path / "missing.png", tmp_path):
        with pytest.raises(ValueError, match="couldn't be read as a picture"):
            ProfileManager.prepare_image(bad)
    trunc = _picture(tmp_path / "t.png")
    trunc.write_bytes(trunc.read_bytes()[:60])
    with pytest.raises(ValueError):
        ProfileManager.prepare_image(trunc)


def test_picture_is_stored_in_our_own_folder_and_replaced_cleanly(qapp, root, tmp_path):
    paths = AppPaths(root)
    paths.ensure()
    s = SettingsManager(paths)
    p = ProfileManager(s, paths)
    seen = []
    p.changed.connect(lambda: seen.append(1))
    original = _picture(tmp_path / "me.png")
    before = original.read_bytes()
    assert p.avatar_image_path is None
    p.set_avatar_from_path(original)
    first = p.avatar_image_path
    assert first and first.parent == paths.profile_dir and first.name.startswith("avatar-") and seen
    assert original.read_bytes() == before  # the user's file is untouched
    time.sleep(0.01)
    p.set_avatar_from_path(_picture(tmp_path / "me2.png", (100, 100)))
    second = p.avatar_image_path
    assert second != first and not first.exists() and len(list(paths.profile_dir.glob("avatar-*.png"))) == 1
    assert json.loads(paths.settings_file.read_text("utf-8"))["profile"]["avatar_image"] == second.name
    again = ProfileManager(SettingsManager(paths), paths)  # next session
    assert again.avatar_image_path == second
    p.clear_avatar_image()
    assert p.avatar_image_path is None and not second.exists()


def test_reset_keeps_picture_and_clearing_the_profile_drops_it(qapp, root, tmp_path):
    paths = AppPaths(root)
    paths.ensure()
    s = SettingsManager(paths)
    p = ProfileManager(s, paths)
    p.save("Ana")
    p.set_avatar_from_path(_picture(tmp_path / "a.png"))
    s.reset()
    assert p.avatar_image_path is not None and p.name == "Ana"
    p.clear()
    assert p.avatar_image_path is None and not list(paths.profile_dir.glob("avatar-*.png")) and not p.has_profile


def test_missing_picture_file_falls_back_to_no_picture(qapp, root, tmp_path):
    paths = AppPaths(root)
    paths.ensure()
    p = ProfileManager(SettingsManager(paths), paths)
    p.set_avatar_from_path(_picture(tmp_path / "a.png"))
    p.avatar_image_path.unlink()  # somebody cleaned the folder
    assert p.avatar_image_path is None


# ------------------------------------------------------------------ bundled images must have a transparent background
@pytest.mark.parametrize("name", ["icon.png", "chevron_dark.png", "chevron_light.png"])
def test_bundled_pngs_are_transparent_and_live_in_assets_icons(name):
    from PIL import Image

    path = Path(__file__).resolve().parent.parent / "assets" / "icons" / name
    assert path.exists() and not (path.parent.parent / name).exists()  # moved, not copied
    im = Image.open(path)
    assert im.mode == "RGBA"
    w, h = im.size
    assert all(im.getpixel(c)[3] == 0 for c in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)])
    assert im.getchannel("A").getextrema()[1] == 255  # and there is something opaque in it


def test_stylesheet_points_at_existing_chevron_files(qapp):
    import re

    from ui.theme import build_stylesheet

    for theme in ("dark", "light"):
        m = re.search(r"url\(([^)]*chevron_%s\.png)\)" % theme, build_stylesheet(theme))
        assert m and Path(m.group(1).strip("'\"")).is_file() and "/icons/" in m.group(1)


# ------------------------------------------------------------------ which speeds are precached
def _wait_cache(qapp, s, speeds, n, seconds=6):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if all(s.cache.has(i, sp) for i in range(n) for sp in speeds):
            return True
        time.sleep(0.02)
    return False


def _stop(s):
    """Stop the cache and wait for its thread, so no signal is emitted at an object that is being garbage-collected."""
    s.cache.shutdown()
    if s.cache._thread is not None:
        s.cache._thread.join(3)


def _cached_speeds(s):
    return sorted(p.name for p in (s.paths.cache_dir / s.cache.doc_hash).iterdir() if p.is_dir())


def test_default_is_only_one_speed(qapp, root):
    s = SettingsManager(AppPaths(root))
    assert s.precache_speeds == (1.0,)


def test_old_precache_all_setting_is_dropped_and_new_default_applies(qapp, root):
    for old in (True, False):
        paths = AppPaths(root / str(old))
        paths.root.mkdir(parents=True)
        paths.settings_file.write_text(json.dumps({"precache_all_speeds": old, "theme": "light"}), "utf-8")
        s = SettingsManager(paths)
        assert s.precache_speeds == (1.0,) and s.theme == "light"
        assert "precache_all_speeds" not in json.loads(paths.settings_file.read_text("utf-8"))


def test_chosen_speeds_are_cleaned_saved_and_reloaded(qapp, root):
    paths = AppPaths(root)
    s = SettingsManager(paths)
    s.precache_speeds = [2.0, 1.25, 1.25, 7.0, "junk", 0.75]
    assert s.precache_speeds == (0.75, 1.25, 2.0)  # sorted, once each, only real speeds
    assert json.loads(paths.settings_file.read_text("utf-8"))["precache_speeds"] == [0.75, 1.25, 2.0]
    assert SettingsManager(paths).precache_speeds == (0.75, 1.25, 2.0)
    s.precache_speeds = []
    assert s.precache_speeds == ()  # nothing extra is allowed: only the active speed is prepared
    s.precache_speeds = None
    assert s.precache_speeds == ()


def test_by_default_only_the_active_speed_is_prepared(qapp, root):
    s = make_services(root)
    s.cache.start(["one two", "three four"], "af_heart", 0, 1.0)
    assert _wait_cache(qapp, s, [1.0], 2)
    time.sleep(0.3)  # give a wrongly enabled background speed the chance to appear
    assert _cached_speeds(s) == ["1.00"]
    _stop(s)


def test_chosen_speeds_are_prepared_and_the_active_one_always_is(qapp, root):
    s = make_services(root)
    s.settings.precache_speeds = [1.5]
    s.cache.start(["one two", "three four"], "af_heart", 0, 1.0)  # listening at 1.0x, 1.5x is chosen
    assert _wait_cache(qapp, s, [1.0, 1.5], 2)
    assert s.cache._enabled_speeds() == (1.0, 1.5)
    time.sleep(0.3)
    assert _cached_speeds(s) == ["1.00", "1.50"]
    s.cache.set_focus(0, 2.0)  # switch to a speed nobody chose: it becomes the active one and is prepared too
    assert s.cache._enabled_speeds() == (1.5, 2.0)
    assert _wait_cache(qapp, s, [1.5, 2.0], 2)
    _stop(s)


def test_progress_total_counts_only_the_prepared_speeds(qapp, root):
    s = make_services(root)
    totals = []
    s.cache.progress.connect(lambda done, total: totals.append(total))
    s.cache.start(["a b", "c d", "e f"], "af_heart", 0, 1.0)
    assert totals[-1] == 3  # 3 paragraphs x 1 speed
    s.settings.precache_speeds = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    s.cache.start(["a b", "c d", "e f"], "af_heart", 0, 1.0)
    assert totals[-1] == 18  # 3 paragraphs x 6 speeds
    _stop(s)


def test_audio_of_the_active_speed_is_made_before_other_speeds(qapp, root):
    s = make_services(root)
    s.settings.precache_speeds = [1.0, 2.0]
    s.cache.start(["a b", "c d", "e f", "g h"], "af_heart", 0, 1.5)  # active 1.5x
    assert _wait_cache(qapp, s, [1.5], 3)
    calls = [(text, speed) for text, _v, speed in s.cache._tts.calls]
    first_three = [sp for _t, sp in calls[:3]]
    assert first_three == [1.5, 1.5, 1.5]  # current paragraph and the next two at the speed being listened to
    _stop(s)


# ------------------------------------------------------------------ Japanese / Chinese voices need optional add-ons
def test_missing_chinese_addon_gets_a_clear_message():
    from core.workers import TaskWorker

    text = TaskWorker.friendly(ModuleNotFoundError("No module named 'jieba'", name="jieba"))
    assert "Chinese" in text and 'pip install "kokorog2p[zh]"' in text and "restart EchoRead" in text
    assert "English voices don't need it" in text


def test_missing_japanese_addon_is_found_even_when_wrapped():
    from core.workers import TaskWorker

    try:
        try:
            raise ModuleNotFoundError("No module named 'pyopenjtalk'", name="pyopenjtalk")
        except ModuleNotFoundError as inner:
            raise ImportError("Japanese OpenJTalk support is not installed. Install it with `pip install 'kokorog2p[ja]'`.") from inner
    except ImportError as exc:
        text = TaskWorker.friendly(exc)
    assert "Japanese" in text and "pip install pyopenjtalk-plus" in text
    assert "Japanese" in TaskWorker.friendly(ImportError("OpenJTalk missing"))  # no cause attached: still recognised


def test_other_errors_keep_their_old_messages():
    from core.workers import TaskWorker

    assert "pip install paddlepaddle paddleocr" in TaskWorker.friendly(ModuleNotFoundError("x", name="paddleocr"))
    assert "espeakng-loader" in TaskWorker.friendly(RuntimeError("eSpeak could not be initialized"))
    assert TaskWorker.friendly(ValueError("plain problem")) == "plain problem"


def test_cache_reports_the_friendly_message_when_a_voice_needs_an_addon(qapp, root):
    class NeedsAddon(FakeTTS):
        def synthesize_timed(self, text, voice, speed):
            raise ModuleNotFoundError("No module named 'jieba'", name="jieba")

    s = make_services(root, tts=NeedsAddon())
    got = []
    s.cache.failed.connect(got.append)
    s.cache.start(["你好"], "zf_xiaobei", 0, 1.0)
    end = time.time() + 5
    while not got and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    _stop(s)
    assert got and "Chinese" in got[0] and "kokorog2p[zh]" in got[0]


def test_requirements_list_the_chinese_and_japanese_addons():
    from packaging.requirements import Requirement

    lines = [l.strip() for l in (Path(__file__).resolve().parent.parent / "requirements-base.txt").read_text("utf-8").splitlines()]
    reqs = [Requirement(l) for l in lines if l and not l.startswith("#")]
    by_name = {r.name: r for r in reqs}
    assert "zh" in by_name["kokorog2p"].extras
    ja = by_name["pyopenjtalk-plus"]
    assert ja.marker is not None and ja.marker.evaluate({"sys_platform": "win32"}) and not ja.marker.evaluate({"sys_platform": "linux"})
    from core.tts import VoiceCatalog

    names = [name for _code, name, _voices in VoiceCatalog.PRESETS]  # the Voices page says the same thing the requirements do
    assert "Japanese (needs pyopenjtalk-plus)" in names and "Chinese (needs kokorog2p[zh])" in names


# ------------------------------------------------------------------ Chinese voices: the ones pykokoro's Chinese model really has
# The exact list from the error Kokoro printed on Iven's PC ("Voice 'zf_xiaobei' not found. Available voices: ...").
V11_ZH_VOICES = set(("af_maple af_sol bf_vale " + " ".join(f"zf_{n:03d}" for n in (
    1, 2, 3, 4, 5, 6, 7, 8, 17, 18, 19, 21, 22, 23, 24, 26, 27, 28, 32, 36, 38, 39, 40, 42, 43, 44, 46, 47, 48, 49, 51, 59, 60, 67,
    70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 83, 84, 85, 86, 87, 88, 90, 92, 93, 94, 99)) + " " + " ".join(f"zm_{n:03d}" for n in (
    9, 10, 11, 12, 13, 14, 15, 16, 20, 25, 29, 30, 31, 33, 34, 35, 37, 41, 45, 50, 52, 53, 54, 55, 56, 57, 58, 61, 62, 63, 64, 65, 66,
    68, 69, 80, 81, 82, 89, 91, 95, 96, 97, 98, 100))).split())


def test_every_chinese_voice_offered_exists_in_the_chinese_model(qapp, root):
    from core.tts import VoiceCatalog

    cat = VoiceCatalog(AppPaths(root))
    zh = [v["id"] for v in cat.list() if v["group"].startswith("Chinese")]
    assert len(zh) >= 6 and set(zh) <= V11_ZH_VOICES, set(zh) - V11_ZH_VOICES
    assert not {"zf_xiaobei", "zm_yunxi"} & {v["id"] for v in cat.list()}
    assert all(cat.language(v) == "zh" and cat.resolve(v) == (v, "zh") for v in zh)
    assert cat.label("zf_001") == "001 · ZH female" and cat.label("zm_009") == "009 · ZH male"


def test_a_saved_choice_of_a_removed_chinese_voice_is_replaced(qapp, root):
    paths = AppPaths(root)
    paths.root.mkdir(parents=True)
    paths.settings_file.write_text(json.dumps({"voice": "zf_xiaobei"}), "utf-8")
    s = SettingsManager(paths)
    assert s.voice == "zf_001"
    assert json.loads(paths.settings_file.read_text("utf-8"))["voice"] == "zf_001"


def test_voice_not_found_error_is_short_and_clear():
    from core.workers import TaskWorker

    err = KeyError("Voice 'zm_nobody' not found. Available voices: " + ", ".join(sorted(V11_ZH_VOICES)))
    text = TaskWorker.friendly(err)
    assert "zm_nobody" in text and "Voices page" in text and "zf_001" not in text and len(text) < 200


def test_build_helper_collects_the_japanese_packages_dll_folder(tmp_path, monkeypatch):
    """pyopenjtalk-plus keeps its DLLs in 'pyopenjtalk_plus.libs' (next to the package); without them the .exe can't load it."""
    import importlib

    site = tmp_path / "site"
    (site / "fakeopenjtalk").mkdir(parents=True)
    (site / "fakeopenjtalk" / "__init__.py").write_text("")
    (site / "fakeopenjtalk" / "core.pyd").write_bytes(b"x")
    (site / "fakeopenjtalk_plus.libs").mkdir()
    (site / "fakeopenjtalk_plus.libs" / "msvcp140-abc123.dll").write_bytes(b"x")
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    import importlib.util

    spec = importlib.util.spec_from_file_location("spec_helpers_under_test", Path(__file__).resolve().parent.parent / "tools" / "spec_helpers.py")
    H = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(H)

    plain = H.native_libraries("fakeopenjtalk")
    with_extra = H.native_libraries("fakeopenjtalk", extra_libs=("fakeopenjtalk_plus.libs",))
    assert {dest for _src, dest in plain} == {"fakeopenjtalk"}  # the old behaviour: only the package itself
    assert {(Path(src).name, dest) for src, dest in with_extra} == {("core.pyd", "fakeopenjtalk"),
                                                                    ("msvcp140-abc123.dll", "fakeopenjtalk_plus.libs")}


def test_spec_collects_all_pyopenjtalk_modules():
    """Its compiled .pyd imports pyopenjtalk._known_symbols by name; PyInstaller can't see that, so the frozen exe lost it
    (self-test: 'Japanese (pyopenjtalk._known_symbols ...)'). The spec must collect every submodule of the package."""
    import re

    spec = (Path(__file__).resolve().parent.parent / "echoread.spec").read_text("utf-8")
    known = re.search(r"KNOWN_LAZY = \[(.*?)\]", spec, re.S).group(1)
    assert '"pyopenjtalk"' in known