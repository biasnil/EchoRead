"""The layout-aware PDF reader (columns, headings, headers/footers, footnotes, verse numbers), the sharper-OCR setting,
column-aware OCR text, and the settings that control them. Uses a sample of a real Bible PDF (tests/data/csb_sample.pdf)."""
import json
import re
import sys
import types
from pathlib import Path

import pymupdf
import pytest

from core.extractor import TextExtractor
from core.loader import DocumentLoader
from core.models import OcrLine
from core.ocr import OCRService
from core.paths import AppPaths
from core.pdflayout import LayoutOptions, PdfLayoutReader, finish_paragraph, stitch_pages
from core.regions import RegionMask
from core.settings import SettingsManager
from tests.fakes import FakeOCR, make_services

SAMPLE = Path(__file__).resolve().parent / "data" / "csb_sample.pdf"
# page numbers in the sample: 0 copyright, 1 introduction (two columns of prose), 2 Genesis 1, 3 Genesis 2-3, 4 Genesis 3-4, 5 Genesis 5-7
COPYRIGHT, INTRO, GEN1, GEN2, GEN3, GEN5 = range(6)


@pytest.fixture(scope="module")
def loader():
    ld = DocumentLoader()
    ld.open(SAMPLE)
    yield ld
    ld.close()


def blocks(loader, page, **opts):
    return loader.layout_blocks(page, LayoutOptions(**opts))


def texts(bs, kind=None):
    return [b.text for b in bs if kind is None or b.kind == kind]


# ================================================================== a real Bible
def test_a_bible_is_recognised_from_its_verse_numbers(loader):
    assert loader.looks_like_scripture() is True


def test_genesis_1_is_titles_chapter_heading_and_one_paragraph_per_verse(loader):
    bs = blocks(loader, GEN1)
    assert [(b.kind, b.text) for b in bs[:3]] == [("title", "Genesis"), ("chapter", "Chapter 1"), ("heading", "THE CREATION")]
    verses = texts(bs, "body")
    assert len(verses) == 31
    assert verses[0] == "In the beginning God created the heavens and the earth."
    assert verses[1].startswith("Now the earth was formless and empty, darkness covered")
    assert verses[7] == "God called the expanse “sky.” Evening came and then morning: the second day."
    assert verses[26] == "So God created man in his own image; he created him in the image of God; he created them male and female."
    assert verses[29].startswith("for all the wildlife of the earth")  # verse 30 starts in the middle of a sentence
    assert verses[30].endswith("the sixth day.")


def test_no_verse_numbers_footnote_letters_or_soft_hyphens_are_left_in_the_text(loader):
    for page in (GEN1, GEN2, GEN3, GEN5):
        for text in texts(blocks(loader, page)):
            assert "\u00ad" not in text and "\u2009" not in text
            assert not re.match(r"^\d+\s", text), text  # a paragraph doesn't begin with its verse number
            assert not re.search(r"\s[a-l]\s(?:and|on|in|Evening)\b", text), text  # ...or with a footnote letter mid-sentence
    joined = " ".join(texts(blocks(loader, GEN1)))
    assert "seed-bearing" in joined and "seed-bear-" not in joined and "separated" in joined


def test_columns_are_read_one_after_the_other(loader):
    bs = blocks(loader, GEN5)
    order = texts(bs)
    def at(fragment):
        return next(i for i, t in enumerate(order) if fragment in t)
    # left column first (top to bottom), then the right column
    assert at("Noah was 500 years old") < at("Chapter 6") < at("When mankind began") < at("Noah, however, found favor")
    assert at("Noah, however, found favor") < at("These are the family records of Noah") < at("“Make yourself an ark of gopher wood")
    assert at("“Make yourself an ark") < at("“Understand that I am bringing a flood") < at("Chapter 7") < at("THE FLOOD")


def test_chapter_numbers_become_chapter_paragraphs_before_their_section_heading(loader):
    order = [(b.kind, b.text) for b in blocks(loader, GEN2)]
    i = order.index(("chapter", "Chapter 3"))
    assert order[i + 1] == ("heading", "THE TEMPTATION AND THE FALL")
    assert order[0] == ("chapter", "Chapter 2")


def test_page_headers_footers_and_printers_marks_are_not_read_by_default(loader):
    everything = " ".join(texts(blocks(loader, GEN2)))
    assert "CSB_Pew_Bible" not in everything and "10/26/17" not in everything and "Genesis 2-3" not in everything
    shown = " ".join(texts(blocks(loader, GEN2, furniture=True)))
    assert "CSB_Pew_Bible.indb" in shown and "Genesis 2-3" in shown


def test_footnotes_are_off_by_default_and_come_after_the_text_when_on(loader):
    assert "Or created the universe" not in " ".join(texts(blocks(loader, GEN1)))
    with_notes = blocks(loader, GEN1, footnotes=True)
    notes = [b for b in with_notes if b.kind == "footnote"]
    assert len(notes) == 12 and notes[0].text == "1:1 Or created the universe"
    assert with_notes[-len(notes):] == notes  # after every verse
    assert all(not re.match(r"^[a-l]\s?\d", n.text) for n in notes)  # the note's little letter is not read


def test_headings_can_be_switched_off(loader):
    bs = blocks(loader, GEN1, headings=False)
    assert "THE CREATION" not in " ".join(texts(bs)) and "Genesis" not in texts(bs)
    assert texts(bs)[0] == "Chapter 1"  # chapter numbers stay: they are how the book is navigated


def test_verse_numbers_can_be_left_in_the_text(loader):
    bs = blocks(loader, GEN1, verses="never")
    joined = " ".join(texts(bs))
    assert "Chapter" not in joined and "2 Now the earth was formless" in joined and "3 Then God said" in joined
    assert len(texts(bs, "body")) < 12  # a few big paragraphs instead of one per verse


def test_bible_mode_does_not_flatten_ordinary_prose_pages(loader):
    bs = blocks(loader, INTRO)  # the document is a Bible, this page is an introduction
    body = texts(bs, "body")
    assert len(body) >= 8
    assert body[0].startswith("The Bible is God’s revelation to humanity. It is our only source for completely reliable information about God")
    assert "in- formation" not in " ".join(body)  # a line-break hyphen is removed, not left with a gap
    assert ("heading", "Goals of This Translation") in [(b.kind, b.text) for b in bs]
    bullets = [t for t in body if t.startswith(("Provide", "Affirm"))]
    assert len(bullets) == 4 and all("•" not in t for t in bullets)  # each bullet its own paragraph, the dot not read


def test_intro_page_two_columns_read_left_then_right(loader):
    joined = " ".join(texts(blocks(loader, INTRO)))
    assert joined.index("Textual Base of the CSB") < joined.index("Goals of This Translation") < joined.index("Translation Philosophy")


def test_block_positions_are_fractions_of_the_page(loader):
    for b in blocks(loader, GEN1, footnotes=True, furniture=True):
        assert 0 <= b.cx <= 1 and 0 <= b.cy <= 1


# ================================================================== the extractor uses it
class Worker:
    cancelled = False

    def __init__(self):
        self.log = []

    def report(self, text, pct=-1):
        self.log.append(text)


def make_extractor(root, ld, **settings):
    s = make_services(root, ocr=FakeOCR())
    for k, v in settings.items():
        setattr(s.settings, k, v)
    ex = TextExtractor(ld, s.ocr, RegionMask(), s.settings)
    return ex, s


def test_extractor_reads_the_sample_with_the_layout_reader(loader, root, qapp):
    ex, _s = make_extractor(root, loader)
    text = ex.extract(Worker())
    paras = text.split("\n\n")
    assert "Chapter 1" in paras and "Chapter 2" in paras and "Chapter 3" in paras
    assert "In the beginning God created the heavens and the earth." in paras
    assert "CSB_Pew_Bible" not in text and "10/26/17" not in text
    assert not any(re.match(r"^\d+\s", p) for p in paras)


def test_a_paragraph_that_carries_on_over_a_page_break_is_joined(loader, root, qapp):
    ex, _s = make_extractor(root, loader)
    text = ex.extract(Worker())
    assert "walking in the garden at the time of the evening breeze, and they hid from the Lord God" in text.replace("\n", " ")
    assert "garden at the\n\ntime of the evening" not in text


def test_changing_a_reading_setting_changes_the_checkpoint_signature(loader, root, qapp):
    ex, s = make_extractor(root, loader)
    before = ex._signature()
    s.settings.pdf_footnotes = True
    assert ex._signature() != before
    after = ex._signature()
    s.settings.ocr_quality = "sharp"
    assert ex._signature() != after


def test_footnote_and_heading_settings_reach_the_extracted_text(loader, root, qapp):
    ex, _s = make_extractor(root, loader, pdf_footnotes=True, pdf_headings=False)
    text = ex.extract(Worker())
    assert "1:1 Or created the universe" in text and "THE CREATION" not in text


def test_keep_and_ignore_regions_still_work_on_layout_blocks(loader, root, qapp):
    s = make_services(root, ocr=FakeOCR())
    mask = RegionMask()
    mask.only = True
    mask.add("keep", (0.0, 0.0, 1.0, 0.16), page=GEN1)  # the top strip of Genesis 1: the title and the chapter heading
    ex = TextExtractor(loader, s.ocr, mask, s.settings)
    page = ex._extract_page(Worker(), GEN1, 6)
    assert "Genesis" in page and "In the beginning" not in page


def test_if_the_layout_reader_fails_the_old_text_blocks_are_used(loader, root, qapp, monkeypatch):
    ex, _s = make_extractor(root, loader)
    monkeypatch.setattr(loader, "layout_blocks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    page = ex._extract_page(Worker(), GEN1, 6)
    assert "In the beginning" in page and "\u00ad" not in page  # the plain blocks, at least tidied of soft hyphens


# ================================================================== synthetic pages: other typesetting
def span(text, x0, y0, size=10.0, font="Body-Regular", flags=4, raised=0.0, width=None):
    w = width if width is not None else len(text) * size * 0.5
    return {"text": text, "size": size, "font": font, "flags": flags, "bbox": (x0, y0, x0 + w, y0 + size * 1.15),
            "origin": (x0, y0 + size - raised)}


def line(*spans):
    x0 = min(s["bbox"][0] for s in spans)
    y0 = min(s["bbox"][1] for s in spans)
    return {"bbox": (x0, y0, max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans)), "spans": list(spans)}


def page(*lines):
    return {"blocks": [{"type": 0, "bbox": (0, 0, 1, 1), "lines": list(lines)}]}


def read(pg, w=400, h=600, **opts):
    return PdfLayoutReader.read_page(pg, w, h, LayoutOptions(**opts), scripture=opts.get("verses") != "never" and None)


def test_superscript_verse_numbers_and_footnote_marks_niv_style():
    """Small raised numbers after a space are verse numbers; small raised letters/digits stuck to a word are footnote marks."""
    def row(y, *parts):
        return line(*parts)
    pg = page(
        row(100, span("In the beginning God created the heavens and the earth.", 50, 100, width=280),
            span("A", 330, 100, size=6, raised=4, font="Body-Regular", flags=5)),
        row(112, span("2", 50, 112, size=6, raised=4, flags=5, width=4), span(" Now the earth was formless and empty,", 55, 112, width=250)),
        row(124, span("darkness covered the surface of the deep.", 50, 124, width=230),
            span("3", 282, 124, size=6, raised=4, flags=5, width=4), span(" Then God said, “Let there be light,”", 290, 124, width=90)),
        row(136, span("and there was light.", 50, 136, width=100), span("4", 152, 136, size=6, raised=4, flags=5, width=4),
            span(" God saw that the light was good.", 158, 136, width=180)),
        row(148, span("5", 50, 148, size=6, raised=4, flags=5, width=4), span(" God called the light “day,”", 55, 148, width=150)),
        row(160, span("6", 50, 160, size=6, raised=4, flags=5, width=4), span(" Then God said, “Let there be an expanse.”", 55, 160, width=230)),
    )
    got = read(pg, verses="always")
    assert [b.text for b in got] == [
        "In the beginning God created the heavens and the earth.",
        "Now the earth was formless and empty, darkness covered the surface of the deep.",
        "Then God said, “Let there be light,” and there was light.",
        "God saw that the light was good.",
        "God called the light “day,”",
        "Then God said, “Let there be an expanse.”"]


def test_a_footnote_number_stuck_to_a_word_is_dropped_not_taken_for_a_verse():
    pg = page(
        line(span("The committee reported its findings", 50, 100, width=200), span("12", 250, 100, size=6, raised=4, flags=5, width=8),
             span(" to the board.", 258, 100, width=70)),
        line(span("Nothing else happened that year at all.", 50, 112, width=190)))
    text = " ".join(b.text for b in read(pg, verses="always"))
    assert text == "The committee reported its findings to the board. Nothing else happened that year at all."


def test_ordinary_numbers_inside_sentences_are_left_alone():
    pg = page(line(span("Adam was 130 years old when he fathered a son.", 50, 100, width=250)))
    assert [b.text for b in read(pg, verses="always")] == ["Adam was 130 years old when he fathered a son."]


def test_a_single_column_with_ragged_lines_is_not_split():
    lines = [line(span("word " * n, 50, 100 + 12 * i, width=n * 5 * 5)) for i, n in enumerate([12, 9, 14, 6, 13, 11, 8, 12, 10, 7])]
    stats = {}
    PdfLayoutReader.read_page(page(*lines), 400, 600, LayoutOptions(verses="never"), scripture=False, stats=stats)
    assert stats["columns"] == 1


def test_two_columns_with_a_full_width_heading():
    left = ["left one", "left two", "left three", "left four", "left five", "left six"]
    right = ["right one", "right two", "right three", "right four", "right five", "right six"]
    lines = [line(span("A Heading Across Both Columns", 60, 60, size=16, font="Head-Bold", flags=16, width=260))]
    for i, (a, b) in enumerate(zip(left, right)):
        lines.append(line(span(a + " text that fills the line", 50, 100 + 12 * i, width=140)))
        lines.append(line(span(b + " text that fills the line", 210, 100 + 12 * i, width=140)))
    stats = {}
    got = PdfLayoutReader.read_page(page(*lines), 400, 600, LayoutOptions(verses="never"), scripture=False, stats=stats)
    assert stats["columns"] == 2
    assert got[0].kind == "title" and got[0].text == "A Heading Across Both Columns"
    body = " ".join(b.text for b in got[1:])
    assert body.index("left six") < body.index("right one")  # the whole left column, then the right one


def test_drop_capital_is_joined_to_its_paragraph():
    pg = page(line(span("T", 50, 100, size=28, font="Body-Bold", flags=16, width=16), span("he morning came slowly over the hills.", 68, 112, width=200)),
              line(span("Nobody was awake to see it happen at all.", 50, 124, width=210)))
    assert [b.text for b in read(pg, verses="never")] == ["The morning came slowly over the hills. Nobody was awake to see it happen at all."]


def test_line_break_hyphens_are_removed_but_real_compounds_keep_theirs():
    pg = page(
        line(span("The report gave the most complete in-", 50, 100, width=230)),
        line(span("formation about the well-known author.", 50, 112, width=230)),
        line(span("The self-", 50, 124, width=60)),
        line(span("assured speaker was self-assured indeed.", 50, 136, width=190)))
    text = read(pg, verses="never")[0].text
    assert "information about the well-known author" in text
    assert "self-assured speaker was self-assured" in text or "selfassured" not in text


def test_a_page_number_and_running_head_are_dropped_and_can_be_kept():
    pg = page(
        line(span("Chapter 3  The River", 50, 20, size=8, font="Sans-Semibold", flags=20, width=90)),
        line(span("The river was wide and slow near the town.", 50, 100, width=240)),
        line(span("It carried timber from the hills each spring.", 50, 112, width=240)),
        line(span("47", 190, 570, size=8, font="Sans-Semibold", flags=20, width=10)))
    assert [b.text for b in read(pg, verses="never")] == ["The river was wide and slow near the town. It carried timber from the hills each spring."]
    kept = " ".join(b.text for b in read(pg, verses="never", furniture=True))
    assert "Chapter 3 The River" in kept and "47" in kept


# ================================================================== small helpers
def test_stitch_pages_only_joins_a_sentence_that_really_carries_on():
    pages = ["First paragraph.\n\nThe sentence is still going on at the very end of the page and",
             "then it finishes here.\n\nNew paragraph."]
    out = stitch_pages(pages)
    assert out[0] == "First paragraph." and out[1].startswith("The sentence is still going on at the very end of the page and then it finishes here.")
    assert stitch_pages(["It ended properly.", "and this starts lowercase."]) == ["It ended properly.", "and this starts lowercase."]
    assert stitch_pages(["It carries on without a full stop", "Next Starts With Capital."]) == ["It carries on without a full stop", "Next Starts With Capital."]
    assert stitch_pages(["Chapter 3", "lowercase start"]) == ["Chapter 3", "lowercase start"]


def test_finish_paragraph_tidies_leaders_spaces_and_bullets():
    assert finish_paragraph("Genesis . . . . . . Gn . . . . . . 1") == "Genesis Gn 1"
    assert finish_paragraph("He said , “ Hello ” .") == "He said, “Hello”."
    assert finish_paragraph("• Provide an accurate translation") == "Provide an accurate translation"
    assert finish_paragraph("be\u00adgin\u00adning\u2009of  it") == "beginning of it"


# ================================================================== OCR: columns, sharpness
def ocr_line(text, cy, x0, x1, h=12):
    return OcrLine(text, x0, cy - h / 2, x1, cy + h / 2)


def two_column_page():
    left = ["In the beginning God created the heavens", "and the earth. Now the earth was formless", "and empty, darkness covered the surface",
            "of the watery depths, and the Spirit of", "God was hovering over the surface of the", "waters. Then God said, let there be light.",
            "God saw that the light was good, and", "God separated the light from the dark."]
    right = ["that it was good. Evening came and then", "morning: the fourth day. Then God said,", "let the water swarm with living creatures",
             "and let birds fly above the earth across", "the expanse of the sky. So God created the", "large sea-creatures and every living thing",
             "that moves and swarms in the water, all", "according to their kinds, and it was so."]
    lines = []
    for i, (a, b) in enumerate(zip(left, right)):
        lines.append(ocr_line(a, 100 + 15 * i, 15, 290))
        lines.append(ocr_line(b, 100 + 15 * i, 300, 580))
    return lines


def test_ocr_reads_columns_one_after_the_other_not_line_by_line():
    text = OCRService.lines_to_text(two_column_page())
    assert text.index("God separated the light from the dark") < text.index("that it was good. Evening came")
    assert "heavens that it was good" not in text and "heavens\nthat" not in text


def test_ocr_full_width_heading_comes_first_and_single_columns_are_unchanged():
    lines = two_column_page() + [ocr_line("GENESIS, THE FIRST BOOK", 60, 150, 450, 20)]
    assert OCRService.lines_to_text(lines).startswith("GENESIS, THE FIRST BOOK")
    single = [ocr_line(f"a line of ordinary text number {i} in one column", 100 + 15 * i, 50, 500) for i in range(12)]
    assert len(OCRService.column_groups(single)) == 1
    assert OCRService.lines_to_text(single).count("\n\n") == 0


def test_ocr_a_ragged_single_column_with_a_short_indented_block_is_not_split():
    lines = [ocr_line("x" * n, 100 + 15 * i, 50, 50 + n * 6) for i, n in enumerate([60, 55, 40, 58, 61, 20, 59, 57, 33, 60])]
    assert len(OCRService.column_groups(lines)) == 1


class FakePaddle:
    """A stand-in for the `paddleocr` module that records how the engine was built."""

    calls: list = []
    reject_size_limit = False

    def __init__(self, **kw):
        if FakePaddle.reject_size_limit and "text_det_limit_side_len" in kw:
            raise TypeError("unexpected keyword argument 'text_det_limit_side_len'")
        FakePaddle.calls.append(kw)


@pytest.fixture
def fake_paddle(monkeypatch):
    FakePaddle.calls, FakePaddle.reject_size_limit = [], False
    module = types.ModuleType("paddleocr")
    module.PaddleOCR = FakePaddle
    monkeypatch.setitem(sys.modules, "paddleocr", module)
    return FakePaddle


def test_sharp_ocr_asks_the_detector_for_a_bigger_size_limit(root, qapp, fake_paddle):
    settings = SettingsManager(AppPaths(root))
    OCRService(settings)._make_paddle()
    assert "text_det_limit_side_len" not in fake_paddle.calls[-1]
    settings.ocr_quality = "sharp"
    OCRService(settings)._make_paddle()
    assert fake_paddle.calls[-1]["text_det_limit_side_len"] == 2560 and fake_paddle.calls[-1]["text_det_limit_type"] == "max"


def test_sharp_ocr_still_works_on_a_paddleocr_that_does_not_know_the_size_limit(root, qapp, fake_paddle):
    fake_paddle.reject_size_limit = True
    settings = SettingsManager(AppPaths(root))
    settings.ocr_quality = "sharp"
    OCRService(settings)._make_paddle()
    assert "text_det_limit_side_len" not in fake_paddle.calls[-1] and "enable_mkldnn" in fake_paddle.calls[-1]


def test_changing_the_sharpness_rebuilds_the_ocr_engine(root, qapp):
    settings = SettingsManager(AppPaths(root))
    svc = OCRService(settings, engine_factory=lambda: object())
    first = svc._ensure()
    assert svc._ensure() is first
    settings.ocr_quality = "sharp"
    assert svc._ensure() is not first


def test_sharp_scans_are_rendered_at_a_higher_resolution(root, qapp, tmp_path, monkeypatch):
    from PIL import Image

    scan = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    Image.new("RGB", (300, 400), "white").save(tmp_path / "blank.png")
    page.insert_image(page.rect, filename=str(tmp_path / "blank.png"))  # a picture, no text: needs OCR
    doc.save(scan)
    doc.close()
    ld = DocumentLoader()
    ld.open(scan)
    seen = []
    real = ld.render_page
    monkeypatch.setattr(ld, "render_page", lambda i, zoom=2.0: seen.append(zoom) or real(i, zoom=zoom))

    class Ocr(FakeOCR):
        def recognize_text(self, image):
            return "recognised text"

    s = make_services(root, ocr=Ocr())
    ex = TextExtractor(ld, s.ocr, RegionMask(), s.settings)
    ex._extract_page(Worker(), 0, 1)
    s.settings.ocr_quality = "sharp"
    ex._extract_page(Worker(), 0, 1)
    ld.close()
    assert seen == [2.0, 3.5]


# ================================================================== settings
def test_reading_settings_have_the_defaults_you_asked_for(root, qapp):
    s = SettingsManager(AppPaths(root))
    assert (s.pdf_headings, s.pdf_footnotes, s.pdf_furniture, s.pdf_verses, s.ocr_quality) == (True, False, False, "auto", "standard")


def test_reading_settings_are_saved_grouped_and_reloaded(root, qapp):
    paths = AppPaths(root)
    s = SettingsManager(paths)
    s.pdf_headings, s.pdf_footnotes, s.pdf_furniture, s.pdf_verses, s.ocr_quality = False, True, True, "always", "sharp"
    data = json.loads(paths.settings_file.read_text("utf-8"))
    assert {k: data["pdf"][k] for k in ("headings", "footnotes", "furniture", "tables", "verses")} == {
        "headings": False, "footnotes": True, "furniture": True, "tables": True, "verses": "always"} and data["ocr_quality"] == "sharp"
    again = SettingsManager(paths)
    assert (again.pdf_headings, again.pdf_footnotes, again.pdf_furniture, again.pdf_verses, again.ocr_quality) == (False, True, True, "always", "sharp")
    again.pdf_verses, again.ocr_quality = "nonsense", "nonsense"
    assert (again.pdf_verses, again.ocr_quality) == ("auto", "standard")


# ================================================================== math out loud
from core.paragraphs import math_to_speech


@pytest.mark.parametrize("written,said", [
    ("x² + y² = z²", "x squared plus y squared equals z squared"),
    ("E = mc²", "E equals mc squared"),
    ("The area is πr² and the sum is 2+3=5.", "The area is pi r squared and the sum is 2 plus 3 equals 5."),
    ("5 - 3 = 2", "5 minus 3 equals 2"),
    ("a·b ≤ c", "a times b is less than or equal to c"),
    ("Profit rose 15% at 20°C.", "Profit rose 15 percent at 20 degrees Celsius."),
    ("√16 = 4", "the square root of 16 equals 4"),
    ("x^2 + 3x - 4 = 0", "x squared plus 3x minus 4 equals 0"),
    ("f(x) = 2x + 1", "f(x) equals 2x plus 1"),
    ("α + β = γ", "alpha plus beta equals gamma"),
    ("x₁ + x₂", "x sub 1 plus x sub 2"),
    ("2 < 3 and 5 > 4", "2 is less than 3 and 5 is greater than 4"),
    ("3 * 4 and 6 / 3", "3 times 4 and 6 divided by 3"),
    ("I paid $5 + tax = $5.40", "I paid $5 plus tax equals $5.40"),
])
def test_formulas_are_written_the_way_they_are_said(written, said):
    assert math_to_speech(written) == said


@pytest.mark.parametrize("prose", [
    "It's a well-known fact - and true.", "Call 555-1234 or 1999-2005.", "See Fig. 3 and section 4.2, p. 5-7.", "C++ is a language.",
    "1/2 cup of flour and and/or so on.", "She was born in 1990 - a good year.", "Visit https://example.com/a?id=5&b=6 now",
    "Email me at a=b@example.com", "Ελληνικά είναι", "<b>bold</b> text", "ratio 3:1 and a/b", "H₂O is water.", "Nothing to change here at all.",
])
def test_ordinary_text_is_left_alone(prose):
    assert math_to_speech(prose) == prose.replace("H₂O", "H2O")


def test_math_reading_can_be_switched_off_and_reaches_the_extracted_text(root, qapp, tmp_path):
    pdf = tmp_path / "math.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for i, t in enumerate(["The theorem states that x² + y² = z² whenever the triangle", "has a right angle, and E = mc² is a different one."]):
        page.insert_text((40, 80 + 14 * i), t, fontsize=10)
    doc.save(pdf)
    doc.close()
    ld = DocumentLoader()
    ld.open(pdf)
    ex, s = make_extractor(root, ld)
    assert "x squared plus y squared equals z squared" in ex.extract(Worker()) and "E equals mc squared" in ex.extract(Worker())
    before = ex._signature()
    s.settings.read_math = False
    assert ex._signature() != before  # saved pages from the other setting are not reused
    assert "x² + y² = z²" in ex.extract(Worker())
    ld.close()


# ================================================================== tables
def ruled_table_pdf(path, rows, above="Here is a table of people:", below="And a paragraph after the table that goes on and on."):
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=300)
    x0, y0, cw, rh = 50, 80, 100, 24
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            p.insert_text((x0 + c * cw + 6, y0 + r * rh + 16), cell, fontsize=10)
    for r in range(len(rows) + 1):
        p.draw_line((x0, y0 + r * rh), (x0 + len(rows[0]) * cw, y0 + r * rh))
    for c in range(len(rows[0]) + 1):
        p.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + len(rows) * rh))
    p.insert_text((50, 60), above, fontsize=10)
    p.insert_text((50, 220), below, fontsize=10)
    doc.save(path)
    doc.close()


def test_a_table_is_read_row_by_row_in_its_place(tmp_path):
    pdf = tmp_path / "t.pdf"
    ruled_table_pdf(pdf, [["Name", "Age", "City"], ["Ana", "30", "Oslo"], ["Ben", "25", "Rome"], ["", "41", "Lima"]])
    ld = DocumentLoader()
    ld.open(pdf)
    got = [(b.kind, b.text) for b in ld.layout_blocks(0, LayoutOptions(verses="never"))]
    assert got == [("body", "Here is a table of people:"), ("table", "Name: Ana. Age: 30. City: Oslo."),
                   ("table", "Name: Ben. Age: 25. City: Rome."), ("table", "Age: 41. City: Lima."),
                   ("body", "And a paragraph after the table that goes on and on.")]
    off = " ".join(b.text for b in ld.layout_blocks(0, LayoutOptions(verses="never", tables=False)))
    assert "Name: Ana" not in off  # the setting really turns it off
    ld.close()


def test_a_table_without_a_header_row_is_read_cell_by_cell(tmp_path):
    pdf = tmp_path / "n.pdf"
    ruled_table_pdf(pdf, [["10", "20", "30"], ["40", "50", "60"]])
    ld = DocumentLoader()
    ld.open(pdf)
    rows = [b.text for b in ld.layout_blocks(0, LayoutOptions(verses="never")) if b.kind == "table"]
    assert rows == ["10, 20, 30.", "40, 50, 60."]
    ld.close()


def test_tables_are_not_invented_on_ordinary_pages(loader):
    for page in range(loader.page_count):
        assert not [b for b in blocks(loader, page) if b.kind == "table"]


def test_table_setting_is_part_of_the_checkpoint_signature_and_saved(root, qapp):
    s = SettingsManager(AppPaths(root))
    assert s.pdf_tables is True and s.read_math is True
    s.pdf_tables = False
    s.read_math = False
    data = json.loads(AppPaths(root).settings_file.read_text("utf-8"))
    assert data["pdf"]["tables"] is False and data["read_math"] is False
    assert LayoutOptions(tables=False).signature() != LayoutOptions(tables=True).signature()


# ================================================================== pictures inside a PDF that has text
def picture_pdf(path, tmp_path):
    from PIL import Image

    Image.new("RGB", (300, 120), "lightgray").save(tmp_path / "chart.png")
    Image.new("RGB", (24, 24), "red").save(tmp_path / "icon.png")
    Image.new("RGB", (400, 560), "white").save(tmp_path / "bg.png")
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    page.insert_text((50, 60), "Introduction paragraph above the picture and more words.", fontsize=10)
    page.insert_image(pymupdf.Rect(50, 100, 350, 220), filename=str(tmp_path / "chart.png"))
    page.insert_image(pymupdf.Rect(50, 240, 74, 264), filename=str(tmp_path / "icon.png"))  # too small to read
    page.insert_text((50, 320), "Closing paragraph below the picture and more words.", fontsize=10)
    doc.save(path)
    doc.close()


class PictureOcr(FakeOCR):
    def __init__(self):
        super().__init__()
        self.sizes = []

    def recognize_text(self, image):
        self.sizes.append(image.size)
        return "Sales rose 20 percent in the second quarter"


def test_pictures_are_left_alone_unless_the_user_asks(root, qapp, tmp_path):
    pdf = tmp_path / "pic.pdf"
    picture_pdf(pdf, tmp_path)
    ld = DocumentLoader()
    ld.open(pdf)
    s = make_services(root, ocr=PictureOcr())
    ex = TextExtractor(ld, s.ocr, RegionMask(), s.settings)
    assert s.settings.pdf_read_images is False
    text = ex._extract_page(Worker(), 0, 1)
    assert "Sales rose" not in text and s.ocr.sizes == []  # not even sent to the OCR
    s.settings.pdf_read_images = True
    text = ex._extract_page(Worker(), 0, 1)
    paras = text.split("\n\n")
    assert paras == ["Introduction paragraph above the picture and more words.", "Sales rose 20 percent in the second quarter",
                     "Closing paragraph below the picture and more words."]  # in its place on the page
    assert len(s.ocr.sizes) == 1 and s.ocr.sizes[0][0] > 600  # only the chart, rendered sharply; not the little icon
    ld.close()


def test_a_scan_behind_its_own_text_layer_is_not_read_twice(root, qapp, tmp_path):
    from PIL import Image

    Image.new("RGB", (400, 560), "white").save(tmp_path / "bg.png")
    pdf = tmp_path / "ocrd.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    page.insert_image(page.rect, filename=str(tmp_path / "bg.png"))
    page.insert_text((50, 60), "Text that the scanner already recognised for us.", fontsize=10)
    doc.save(pdf)
    doc.close()
    ld = DocumentLoader()
    ld.open(pdf)
    assert ld.image_regions(0) == []
    ld.close()


def test_picture_setting_is_saved_and_changes_the_signature(root, qapp, tmp_path):
    pdf = tmp_path / "pic.pdf"
    picture_pdf(pdf, tmp_path)
    ld = DocumentLoader()
    ld.open(pdf)
    s = make_services(root, ocr=PictureOcr())
    ex = TextExtractor(ld, s.ocr, RegionMask(), s.settings)
    before = ex._signature()
    s.settings.pdf_read_images = True
    assert ex._signature() != before and json.loads(s.paths.settings_file.read_text("utf-8"))["pdf"]["read_images"] is True
    ld.close()


# ================================================================== Smart layout (PP-StructureV3), off by default
from PIL import Image  # noqa: E402

TABLE_HTML = "<html><body><table><tr><th>Name</th><th>Age</th></tr><tr><td>Ana</td><td>30</td></tr></table></body></html>"


def blk(label, content):
    return {"block_label": label, "block_content": content, "block_bbox": [0, 0, 1, 1]}


PAGE_BLOCKS = [blk("header", "Genesis 2-3   2"), blk("doc_title", "Genesis"), blk("paragraph_title", "THE CREATION"),
               blk("text", "In the beginning God created the heavens and\nthe earth. Now the earth was form-\nless and empty."),
               blk("image", "a picture"), blk("figure_title", "Figure 1. The world"), blk("table", TABLE_HTML),
               blk("footnote", "a 1:1 Or created the universe"), blk("number", "17"), blk("footer", "CSB Pew Bible")]


class FakeStructure:
    calls = 0

    def __init__(self, **kw):
        FakeStructure.kw = kw

    def predict(self, arr):
        FakeStructure.calls += 1
        return [{"parsing_res_list": PAGE_BLOCKS}]


class FakeStandard:
    def predict(self, arr):
        return [{"rec_texts": ["standard ocr line"], "rec_boxes": [[0, 0, 100, 12]]}]


@pytest.fixture
def smart(root, qapp, monkeypatch):
    FakeStructure.calls = 0
    module = types.ModuleType("paddleocr")
    module.PPStructureV3 = FakeStructure
    monkeypatch.setitem(sys.modules, "paddleocr", module)
    settings = SettingsManager(AppPaths(root))
    svc = OCRService(settings, engine_factory=lambda: FakeStandard())
    status = []
    svc.status.connect(status.append)
    svc.messages = status
    return svc, settings


PICTURE = Image.new("RGB", (200, 100), "white")


def test_smart_layout_is_off_by_default_and_the_standard_ocr_is_used(smart):
    svc, settings = smart
    assert settings.ocr_smart is False
    assert svc.recognize_text(PICTURE) == "standard ocr line" and FakeStructure.calls == 0


def test_smart_layout_reads_regions_in_order_without_headers_footers_and_notes(smart):
    svc, settings = smart
    settings.ocr_smart = True
    assert svc.recognize_text(PICTURE).split("\n\n") == [
        "Genesis", "THE CREATION", "In the beginning God created the heavens and the earth. Now the earth was formless and empty.",
        "Figure 1. The world", "Name: Ana. Age: 30."]
    assert FakeStructure.kw["use_formula_recognition"] is False and FakeStructure.kw["use_table_recognition"] is True


def test_smart_layout_follows_the_same_reading_settings(smart):
    svc, settings = smart
    settings.ocr_smart = True
    settings.pdf_headings, settings.pdf_footnotes, settings.pdf_furniture, settings.pdf_tables = False, True, True, False
    text = svc.recognize_text(PICTURE)
    assert "THE CREATION" not in text and "Genesis" not in text.split("\n\n")
    assert "a 1:1 Or created the universe" in text and "Genesis 2-3 2" in text and "CSB Pew Bible" in text and "17" in text
    assert "Name: Ana" not in text and "Ana" in text  # a table with tables switched off is just its cell text


def test_smart_layout_accepts_result_objects_too(smart):
    svc, settings = smart
    settings.ocr_smart = True

    class Item:
        def __init__(self, label, content):
            self.label, self.content = label, content

    class Result(dict):
        pass

    res = Result(json={"res": {"parsing_res_list": [Item("text", "Object style block.")]}})
    assert svc.blocks_to_paragraphs(svc._parsing_list(res), settings) == ["Object style block."]


def test_smart_layout_model_is_built_once(smart):
    svc, settings = smart
    settings.ocr_smart = True
    svc.recognize_text(PICTURE)
    svc.recognize_text(PICTURE)
    assert FakeStructure.calls == 2 and svc._structure is not None


def test_if_smart_layout_fails_the_standard_ocr_takes_over_and_is_not_retried(smart, monkeypatch):
    svc, settings = smart
    settings.ocr_smart = True
    tries = []

    def broken():
        tries.append(1)
        raise RuntimeError("could not download the layout model")

    svc._structure_factory = broken
    assert svc.recognize_text(PICTURE) == "standard ocr line"
    assert svc.recognize_text(PICTURE) == "standard ocr line" and len(tries) == 1  # not retried on every page
    assert any("Smart layout isn't available" in m and "could not download" in m for m in svc.messages)
    settings.ocr_smart = False
    settings.ocr_smart = True  # choosing it again is a new try
    svc.recognize_text(PICTURE)
    assert len(tries) == 2


def test_smart_layout_copes_with_a_paddleocr_that_does_not_know_the_switches(root, qapp, monkeypatch):
    calls = []

    class Picky:
        def __init__(self, **kw):
            calls.append(kw)
            if "use_formula_recognition" in kw:
                raise TypeError("unexpected keyword argument")

    module = types.ModuleType("paddleocr")
    module.PPStructureV3 = Picky
    monkeypatch.setitem(sys.modules, "paddleocr", module)
    OCRService(SettingsManager(AppPaths(root)))._make_structure()
    assert list(calls[-1]) == ["device"]


def test_smart_layout_setting_is_saved_and_part_of_the_checkpoint_signature(root, qapp):
    s = make_services(root, ocr=FakeOCR())
    ex = TextExtractor(DocumentLoader(), s.ocr, RegionMask(), s.settings)
    before = ex._signature()
    s.settings.ocr_smart = True
    assert ex._signature() != before and json.loads(s.paths.settings_file.read_text("utf-8"))["ocr_smart"] is True


# ================================================================== pictures kept in the document
from core.models import is_picture, picture_marker, picture_name  # noqa: E402
from tests.diagram_pdf import make as make_diagram_pdf  # noqa: E402


def test_picture_markers_round_trip():
    m = picture_marker("ab12cd34ef567890.png")
    assert picture_name(m) == "ab12cd34ef567890.png" and is_picture("  " + m + "\n")
    assert picture_name("A normal paragraph.") is None and picture_name("⟦image:⟧") is None and not is_picture("")


def test_a_picture_is_stored_once_by_its_content(root):
    paths = AppPaths(root)
    a = paths.store_image(b"\x89PNG one")
    assert a == paths.store_image(b"\x89PNG one") and a != paths.store_image(b"\x89PNG two")
    assert (paths.images_dir / a).read_bytes() == b"\x89PNG one" and a.endswith(".png")


def test_slash_between_words_is_not_division():
    assert math_to_speech("1. Transmission Mode / Duplex Communication (FDD & TDD)") == "1. Transmission Mode / Duplex Communication (FDD & TDD)"
    assert math_to_speech("read and / or write") == "read and / or write"
    assert math_to_speech("6 / 3 and a / b") == "6 divided by 3 and a divided by b"


@pytest.fixture(scope="module")
def diagram(tmp_path_factory):
    path = tmp_path_factory.mktemp("d") / "diagram.pdf"
    make_diagram_pdf(str(path))
    ld = DocumentLoader()
    ld.open(path)
    yield ld
    ld.close()


def test_a_drawing_with_labels_is_found_as_figures_and_ordinary_pages_have_none(diagram, loader):
    figs = diagram.figure_regions(0)
    assert len(figs) == 2 and figs[0][0][1] < figs[1][0][1]  # the arrows above, the two boxes below
    assert all(png[:8] == b"\x89PNG\r\n\x1a\n" for _bbox, png in figs)
    assert all(not loader.figure_regions(p) for p in range(loader.page_count))  # the Bible pages: nothing


def test_figure_labels_leave_the_text_and_the_figure_keeps_its_place(diagram):
    figs = [(bbox, picture_marker(f"f{i}.png")) for i, (bbox, _png) in enumerate(diagram.figure_regions(0))]
    got = [(b.kind, b.text) for b in diagram.layout_blocks(0, LayoutOptions(verses="never"), figures=figs)]
    assert got == [("body", "1. Transmission Mode / Duplex Communication (FDD & TDD)"), ("body", "Simplex: transmit only one way on a channel"),
                   ("body", "Full-duplex: two-way communication, achieved using FDD or TDD"), ("body", "FDD (Frequency Division Duplex):"),
                   ("figure", "⟦image:f0.png⟧"), ("figure", "⟦image:f1.png⟧"), ("body", "Forward channel and reverse channel use different frequencies")]
    without = " ".join(b.text for b in diagram.layout_blocks(0, LayoutOptions(verses="never")))
    assert "Download" in without and "Upload" in without  # pictures not kept: the labels are read as before


def test_figure_labels_can_be_read_when_asked(diagram):
    figs = [(bbox, picture_marker(f"f{i}.png")) for i, (bbox, _png) in enumerate(diagram.figure_regions(0))]
    got = diagram.layout_blocks(0, LayoutOptions(verses="never", picture_text=True), figures=figs)
    labels = [b.text for b in got if b.kind == "image"]
    assert len(labels) == 2 and "Download" in labels[0] and "Upload" in labels[0] and "Base Station" in labels[1]
    assert all(b.has_text for b in got if b.kind == "figure")


def test_extractor_keeps_pictures_between_the_paragraphs(root, qapp, diagram):
    s = make_services(root, ocr=FakeOCR())
    ex = TextExtractor(diagram, s.ocr, RegionMask(), s.settings, store_image=s.paths.store_image)
    paras = ex._extract_page(Worker(), 0, 1).split("\n\n")
    assert [is_picture(p) for p in paras] == [False, False, False, False, True, True, False]
    assert paras[3] == "FDD (Frequency Division Duplex):" and paras[6].startswith("Forward channel")
    for p in paras:
        if is_picture(p):
            assert (s.paths.images_dir / picture_name(p)).stat().st_size > 1000
    s.settings.pdf_show_pictures = False
    off = ex._extract_page(Worker(), 0, 1)
    assert "⟦image" not in off and "Download" in off
    before = ex._signature()
    s.settings.pdf_show_pictures = True
    assert ex._signature() != before


def test_default_settings_keep_pictures_but_do_not_read_them(root, qapp):
    s = SettingsManager(AppPaths(root))
    assert s.pdf_show_pictures is True and s.pdf_read_images is False


def test_raster_picture_without_text_can_be_ocrd_after_its_marker(root, qapp, tmp_path):
    pdf = tmp_path / "pic.pdf"
    picture_pdf(pdf, tmp_path)
    ld = DocumentLoader()
    ld.open(pdf)
    s = make_services(root, ocr=PictureOcr())
    s.settings.pdf_read_images = True
    ex = TextExtractor(ld, s.ocr, RegionMask(), s.settings, store_image=s.paths.store_image)
    paras = ex._extract_page(Worker(), 0, 1).split("\n\n")
    assert [is_picture(p) for p in paras] == [False, True, False, False]
    assert paras[2] == "Sales rose 20 percent in the second quarter"  # the OCR'd words come right after their picture
    ld.close()


# ---------------------------------------------------------------- scans: finding pictures on a page image
def scan_page():
    from PIL import ImageDraw

    im = Image.new("RGB", (1190, 1684), "white")
    d = ImageDraw.Draw(im)
    lines = []
    for i in range(6):  # ordinary text lines (only their boxes matter: the scan is blanked where OCR found text)
        y = 100 + 40 * i
        d.rectangle((100, y, 900, y + 16), fill="black")
        lines.append(OcrLine(f"A paragraph line number {i} of the page.", 100, y, 900, y + 16))
    d.rectangle((250, 500, 900, 900), outline="navy", width=4)  # a diagram: a frame, an arrow, a coloured block
    d.polygon([(300, 600), (700, 600), (700, 570), (780, 650), (700, 730), (700, 700), (300, 700)], fill=(90, 200, 60))
    d.rectangle((800, 780, 880, 880), fill=(10, 20, 100))
    lines.append(OcrLine("Download", 420, 640, 560, 664))  # a label inside the diagram
    d.rectangle((100, 1000, 900, 1016), fill="black")
    lines.append(OcrLine("Text after the picture.", 100, 1000, 900, 1016))
    return im, lines


def test_a_picture_on_a_scanned_page_is_found_and_text_is_not():
    im, lines = scan_page()
    regions = OCRService.find_picture_regions(im, lines)
    assert len(regions) == 1
    x0, y0, x1, y1 = regions[0]
    assert x0 <= 250 and y0 <= 500 and x1 >= 900 and y1 >= 900 and y0 > 300 and y1 < 1000  # around the diagram, not the text


def test_blank_pages_dense_text_and_page_edge_shadows_are_not_pictures():
    from PIL import ImageDraw

    blank = Image.new("RGB", (1190, 1684), "white")
    assert OCRService.find_picture_regions(blank, []) == []
    im = Image.new("RGB", (1190, 1684), "white")
    d = ImageDraw.Draw(im)
    lines = []
    for i in range(30):  # a table-like block: lots of text (recognised) with ruled lines around it
        y = 400 + 24 * i
        d.rectangle((150, y, 1000, y + 14), fill="black")
        lines.append(OcrLine("row text", 150, y, 1000, y + 14))
    d.rectangle((140, 390, 1010, 1130), outline="black", width=3)
    assert OCRService.find_picture_regions(im, lines) == []
    shadow = Image.new("RGB", (1190, 1684), "white")
    ImageDraw.Draw(shadow).rectangle((0, 0, 1190, 90), fill=(70, 70, 70))  # the dark edge of a scan
    assert OCRService.find_picture_regions(shadow, []) == []


def test_scan_pictures_are_kept_in_place_and_their_labels_left_out(root, qapp):
    im, lines = scan_page()
    settings = SettingsManager(AppPaths(root))
    svc = OCRService(settings, engine_factory=lambda: None)
    svc.recognize = lambda image: list(lines)
    saved = []
    text = svc.recognize_with_pictures(im, lambda crop: saved.append(crop.size) or picture_marker("scan1.png"))
    paras = text.split("\n\n")
    assert paras[-2:] == ["⟦image:scan1.png⟧", "Text after the picture."] and "Download" not in text and len(saved) == 1
    settings.pdf_read_images = True
    assert "Download" in svc.recognize_with_pictures(im, lambda crop: picture_marker("scan1.png"))


def test_ocr_paragraphs_split_where_the_lines_have_gaps_even_next_to_big_labels():
    lines = [OcrLine("1. Transmission Mode / Duplex Communication (FDD)", 40, 100, 700, 118),
             OcrLine("Simplex: transmit only one way on a channel", 40, 136, 640, 154),
             OcrLine("Full-duplex: two-way communication, achieved using FDD", 40, 172, 700, 190),
             OcrLine("Mobile Terminal", 60, 400, 260, 432), OcrLine("Base Station", 400, 400, 620, 432), OcrLine("Forward Channel", 280, 405, 390, 425)]
    assert OCRService.lines_to_text(lines).split("\n\n")[:3] == [
        "1. Transmission Mode / Duplex Communication (FDD)", "Simplex: transmit only one way on a channel", "Full-duplex: two-way communication, achieved using FDD"]


def test_smart_layout_keeps_its_picture_regions(smart):
    svc, settings = smart
    settings.ocr_smart = True
    global PAGE_BLOCKS
    original = list(PAGE_BLOCKS)
    PAGE_BLOCKS[:] = [blk("text", "Before."), {"block_label": "image", "block_content": "", "block_bbox": [10, 20, 190, 90]}, blk("text", "After.")]
    try:
        text = svc.recognize_with_pictures(Image.new("RGB", (200, 100), "white"), lambda crop: picture_marker(f"c{crop.size[0]}.png"))
    finally:
        PAGE_BLOCKS[:] = original
    assert text.split("\n\n") == ["Before.", "⟦image:c180.png⟧", "After."]
