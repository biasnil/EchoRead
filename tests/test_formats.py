"""EPUB and Word files: reading them and opening them from the window."""
import zipfile

import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication

from core.formats import UnreadableBook, read_docx, read_epub, rows_to_sentences
from tests.fakes import make_services
from tests.test_ui_v2 import wait_for
from ui.main_window import MainWindow
from ui.theme import build_stylesheet

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTAINER = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles>'
             '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')


def make_epub(path, chapters, title="My Little Book", extra=None, encryption=None, linear_no=()):
    items = "".join(f'<item id="c{i}" href="text/ch{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(chapters)))
    spine = "".join(f'<itemref idref="c{i}"{" linear=\"no\"" if i in linear_no else ""}/>' for i in range(len(chapters)))
    opf = (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{title}</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           f'{items}</manifest><spine>{spine}</spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        if encryption:
            z.writestr("META-INF/encryption.xml", encryption)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", "<html><body><nav><ol><li>TABLE OF CONTENTS</li></ol></nav></body></html>")
        for i, body in enumerate(chapters):
            z.writestr(f"OEBPS/text/ch{i}.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                       f'<head><title>x</title><style>p{{color:red}}</style></head><body>{body}</body></html>')
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def make_docx(path, body, title=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        if title:
            z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                       f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title></cp:coreProperties>')


def wp(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


# ============================================================ EPUB
def test_epub_chapters_come_in_spine_order_with_headings_as_paragraphs(tmp_path):
    f = tmp_path / "b.epub"
    make_epub(f, ["<h1>Chapter 1</h1><p>It was a dark night.</p>", "<h2>Chapter 2</h2><p>The <em>morning</em> came, and <b>so</b> did the rain.</p>"])
    title, text = read_epub(f)
    assert title == "My Little Book"
    assert text.split("\n\n") == ["Chapter 1", "It was a dark night.", "Chapter 2", "The morning came, and so did the rain."]


def test_epub_leaves_out_the_contents_page_hidden_pages_notes_and_styles(tmp_path):
    f = tmp_path / "b.epub"
    make_epub(f, ['<p>COVER</p>', '<h1>One</h1><p>Sentence one.<sup><a epub:type="noteref" href="#n1">1</a></sup> Sentence two.</p>'
                  '<aside epub:type="footnote" id="n1"><p>A footnote nobody wants read.</p></aside>'], linear_no=(0,))
    text = read_epub(f)[1]
    assert "TABLE OF CONTENTS" not in text and "COVER" not in text and "footnote" not in text and "color:red" not in text
    assert "Sentence one. Sentence two." in text


def test_epub_paragraph_details(tmp_path):
    f = tmp_path / "b.epub"
    make_epub(f, ["<p>Line one<br/>line two&#160;here&#173;.</p><div>Loose text in a div<br/>second line.</div><ul><li>apple</li><li>pear</li></ul>"
                  "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Ana</td><td>30</td></tr><tr><td>Ben</td><td>25</td></tr></table>"])
    assert read_epub(f)[1].split("\n\n") == ["Line one line two here.", "Loose text in a div second line.", "apple", "pear",
                                             "Name: Ana. Age: 30.", "Name: Ben. Age: 25."]


def test_drm_protected_and_damaged_epubs_say_so(tmp_path):
    drm = tmp_path / "drm.epub"
    make_epub(drm, ["<p>secret</p>"], encryption='<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></EncryptedData></encryption>')
    with pytest.raises(UnreadableBook, match="DRM"):
        read_epub(drm)
    fonts = tmp_path / "fonts.epub"  # font obfuscation is not DRM: the text is readable
    make_epub(fonts, ["<p>readable text</p>"], encryption='<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/></EncryptedData></encryption>')
    assert read_epub(fonts)[1] == "readable text"
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(UnreadableBook, match="valid EPUB"):
        read_epub(bad)
    empty = tmp_path / "empty.epub"
    make_epub(empty, ["<p></p>"])
    with pytest.raises(UnreadableBook, match="No text"):
        read_epub(empty)


# ============================================================ Word
def test_docx_paragraphs_headings_tabs_tables_and_deleted_text(tmp_path):
    f = tmp_path / "d.docx"
    body = (wp("My Report") + '<w:p><w:r><w:t>First</w:t></w:r><w:r><w:tab/><w:t xml:space="preserve"> second</w:t></w:r>'
            '<w:del><w:r><w:delText>deleted words</w:delText></w:r></w:del></w:p>'
            '<w:tbl><w:tr><w:tc>' + wp("Item") + '</w:tc><w:tc>' + wp("Cost") + '</w:tc></w:tr><w:tr><w:tc>' + wp("Tea") + '</w:tc><w:tc>' + wp("3")
            + '</w:tc></w:tr></w:tbl><w:sdt><w:sdtContent>' + wp("Inside a content control.") + '</w:sdtContent></w:sdt><w:p/>' + wp("   "))
    make_docx(f, body, title="Quarterly")
    title, text = read_docx(f)
    assert title == "Quarterly"
    assert text.split("\n\n") == ["My Report", "First second", "Item: Tea. Cost: 3.", "Inside a content control."]


def test_docx_without_a_title_uses_the_file_name_and_bad_files_say_so(tmp_path):
    f = tmp_path / "notes.docx"
    make_docx(f, wp("Hello."))
    assert read_docx(f) == ("notes", "Hello.")
    bad = tmp_path / "old.docx"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 an old binary or encrypted file")
    with pytest.raises(UnreadableBook, match="password-protected or damaged"):
        read_docx(bad)
    empty = tmp_path / "e.docx"
    make_docx(empty, "<w:p/>")
    with pytest.raises(UnreadableBook, match="No text"):
        read_docx(empty)
    notword = tmp_path / "x.docx"
    with zipfile.ZipFile(notword, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(UnreadableBook, match="Word"):
        read_docx(notword)


def test_table_sentences():
    assert rows_to_sentences([["Name", "Age"], ["Ana", "30"]]) == ["Name: Ana. Age: 30."]
    assert rows_to_sentences([["1", "2"], ["3", "4"]]) == ["1, 2.", "3, 4."]
    assert rows_to_sentences([["", ""], ["", ""]]) == []


# ============================================================ opening them in the window
@pytest.fixture
def win(qapp, root, monkeypatch):
    s = make_services(root)
    qapp.setStyleSheet(build_stylesheet("dark"))
    w = MainWindow(s)
    w.errors_shown = []
    monkeypatch.setattr(w, "_error", lambda title, text: w.errors_shown.append((title, text)))
    w.show()
    yield w
    w.close()


def test_epub_and_docx_open_in_the_reader(win, qapp, tmp_path):
    epub = tmp_path / "novel.epub"
    make_epub(epub, ["<h1>Chapter 1</h1><p>It was a dark night.</p><p>The rule is x² + y² = z².</p>"], title="The Novel")
    win.open_path(epub)
    assert wait_for(qapp, lambda: len(win.s.model) == 3 and win._job is None)
    assert win.s.state.doc.title == "The Novel" and win.s.state.doc.kind == "file"
    assert win.s.model[2].text == "The rule is x squared plus y squared equals z squared."  # formulas are said aloud here too
    docx = tmp_path / "essay.docx"
    make_docx(docx, wp("Only paragraph."), title="An Essay")
    win.open_path(docx)
    assert wait_for(qapp, lambda: win.s.state.doc.title == "An Essay" and win._job is None)
    assert win.s.model[0].text == "Only paragraph." and win.errors_shown == []


def test_a_damaged_book_gives_a_message_not_a_crash(win, qapp, tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"nope")
    win.open_path(bad)
    assert wait_for(qapp, lambda: win.errors_shown and win._job is None)
    assert win.errors_shown[0][0] == "Could not open this file" and "valid EPUB" in win.errors_shown[0][1]


def test_old_word_files_get_a_helpful_toast_and_dropping_a_book_opens_it(win, qapp, tmp_path):
    old = tmp_path / "old.doc"
    old.write_bytes(b"x")
    win.open_path(old)
    assert ".docx" in win._toast.last_text and win.errors_shown == []
    epub = tmp_path / "dropped.epub"
    make_epub(epub, ["<p>Dropped book text.</p>"], title="Dropped")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(epub))])
    win.dropEvent(QDropEvent(QPointF(5, 5), __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.DropAction.CopyAction, md,
                             __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.MouseButton.LeftButton,
                             __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.KeyboardModifier.NoModifier))
    assert wait_for(qapp, lambda: win.s.state.doc is not None and win.s.state.doc.title == "Dropped" and win._job is None)
