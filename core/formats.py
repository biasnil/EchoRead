"""EPUB and Word (.docx) books as plain text: paragraphs in reading order, headings as their own paragraphs, tables row by row.

Both formats are zip files of XML, so the standard library and lxml (already needed for web pages) are enough.
`read_epub` / `read_docx` return (title, text) and raise `UnreadableBook` with a message fit for the user.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree, html


class UnreadableBook(Exception):
    """The file can't be turned into text (damaged, protected, or not what its name says)."""


_BLOCK_LEAF = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "figcaption", "caption", "summary", "pre", "address"}
_CONTAINERS = {"body", "div", "section", "article", "main", "blockquote", "ul", "ol", "dl", "figure", "details", "header", "footer",
               "aside", "nav", "form", "fieldset", "center", "tbody", "thead", "tfoot", "hgroup"}
_SKIP = {"script", "style", "head", "title", "meta", "link", "svg", "math", "iframe", "object", "audio", "video", "canvas", "noscript",
         "img", "hr", "button", "input", "select", "textarea"}
_NOTE_TYPES = ("noteref", "footnote", "endnote", "rearnote", "pagebreak", "page-list", "toc")
_FREE_ENCRYPTION = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")  # font obfuscation only: the text is readable
_SPACES = dict.fromkeys(map(ord, "\u00a0\u2002\u2003\u2009\u200a\u202f\t\r\n"), " ")
_ZERO = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)


def _tidy(text: str) -> str:
    return re.sub(r" {2,}", " ", text.translate(_ZERO).translate(_SPACES)).strip()


def rows_to_sentences(rows: list[list[str]]) -> list[str]:
    """A table as sentences. With a header row (all cells filled, none a number) every cell is announced by its column name."""
    rows = [[_tidy(c) for c in r] for r in rows if any(_tidy(c) for c in r)]
    if not rows:
        return []
    first = rows[0]
    header = len(rows) > 1 and len(first) > 1 and all(first) and not any(re.fullmatch(r"[\d\s.,%$€£-]+", h) for h in first)
    out = []
    for r in rows[1:] if header else rows:
        text = ". ".join(f"{h}: {v}" for h, v in zip(first, r) if v) if header else ", ".join(v for v in r if v)
        if text:
            out.append(text if re.search(r"[.!?…]$", text) else text + ".")
    return out


# ================================================================================================= EPUB
def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _epub_type(el) -> str:
    for key, value in el.attrib.items():
        if key.endswith("type") and ("epub" in key or key == "type" or key.endswith("}type")):
            return value
    return ""


def _html_paragraphs(data: bytes) -> list[str]:
    try:  # EPUB pages are UTF-8 unless they say otherwise: decode ourselves so "²" doesn't become "Â²"
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            doc = html.document_fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))
        else:
            doc = html.document_fromstring(data)
    except (etree.ParserError, ValueError):
        return []
    for bad in doc.xpath("//script|//style|//nav"):
        bad.drop_tree()
    for br in doc.iter("br"):  # a line break inside a paragraph is a space
        br.tail = " " + (br.tail or "")
    for el in list(doc.iter()):
        if not isinstance(el.tag, str):
            continue
        kind = _epub_type(el)
        classes = el.get("class", "")
        if (kind and any(k in kind for k in _NOTE_TYPES)) or el.get("role") in ("doc-noteref", "doc-footnote", "doc-endnote") or \
                ("noteref" in classes.split() and el.tag in ("a", "sup")):
            if el.getparent() is not None:
                el.drop_tree()
    body = doc.find("body")
    out: list[str] = []
    if body is not None:
        _walk_html(body, out)
    return [p for p in out if p]


def _walk_html(el, out: list[str]) -> None:
    buffer: list[str] = []

    def flush():
        text = _tidy("".join(buffer))
        buffer.clear()
        if text:
            out.append(text)

    if el.text:
        buffer.append(el.text)
    for child in el:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in _SKIP or not tag:
            pass
        elif tag == "table":
            flush()
            rows = [[_tidy("".join(td.itertext())) for td in tr.xpath("./th|./td")] for tr in child.xpath(".//tr")]
            out.extend(rows_to_sentences(rows))
        elif tag in _BLOCK_LEAF:
            flush()
            text = _tidy(" ".join("".join(child.itertext()).split("\n")))
            if text:
                out.append(text)
        elif tag in _CONTAINERS:
            flush()
            _walk_html(child, out)
        elif tag == "br":
            buffer.append(" ")
        else:  # inline (a, span, em, ...) sitting directly inside a container
            buffer.append("".join(child.itertext()))
        if child.tail:
            buffer.append(child.tail)
    flush()


def read_epub(path) -> tuple[str, str]:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnreadableBook("This doesn't look like a valid EPUB file (it may be damaged).") from exc
    with zf:
        names = set(zf.namelist())
        if "META-INF/encryption.xml" in names:
            algorithms = set(re.findall(r'Algorithm="([^"]+)"', zf.read("META-INF/encryption.xml").decode("utf-8", "replace")))
            if any(a not in _FREE_ENCRYPTION for a in algorithms):
                raise UnreadableBook("This EPUB is protected (DRM), so EchoRead can't read it. Only DRM-free books can be opened.")
        try:
            container = etree.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.xpath("//*[local-name()='rootfile']/@full-path")[0]
            opf = etree.fromstring(zf.read(rootfile))
        except (KeyError, IndexError, etree.XMLSyntaxError) as exc:
            raise UnreadableBook("This EPUB is missing its contents list, so it can't be read.") from exc
        base = posixpath.dirname(rootfile)
        manifest = {}
        for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
            manifest[item.get("id")] = (unquote(item.get("href", "")), item.get("media-type", ""), item.get("properties", ""))
        title = "".join(opf.xpath("//*[local-name()='metadata']/*[local-name()='title'][1]//text()")).strip() or path.stem
        paragraphs: list[str] = []
        for ref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
            if ref.get("linear") == "no" or ref.get("idref") not in manifest:
                continue
            href, media, props = manifest[ref.get("idref")]
            if "html" not in media or "nav" in props.split():
                continue
            name = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
            if name not in names:
                continue
            paragraphs += _html_paragraphs(zf.read(name))
    text = "\n\n".join(paragraphs)
    if not text.strip():
        raise UnreadableBook("No text was found in this EPUB.")
    return title, text


# ================================================================================================= Word
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_paragraph(p) -> str:
    parts = []
    for el in p.iter():
        tag = el.tag
        if tag == _W + "t":
            parts.append(el.text or "")
        elif tag in (_W + "tab", _W + "br", _W + "cr"):
            parts.append(" ")
        elif tag == _W + "noBreakHyphen":
            parts.append("-")
    return _tidy("".join(parts))


def _walk_docx(el, out: list[str]) -> None:
    for child in el:
        tag = child.tag
        if tag == _W + "p":
            text = _docx_paragraph(child)
            if text:
                out.append(text)
        elif tag == _W + "tbl":
            rows = []
            for tr in child.iterfind(_W + "tr"):
                rows.append([" ".join(filter(None, (_docx_paragraph(p) for p in tc.iter(_W + "p")))) for tc in tr.iterfind(_W + "tc")])
            out.extend(rows_to_sentences(rows))
        elif tag in (_W + "sdt", _W + "sdtContent", _W + "customXml", _W + "ins", _W + "smartTag"):
            _walk_docx(child, out)


def read_docx(path) -> tuple[str, str]:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnreadableBook("This Word file can't be read: it may be password-protected or damaged.") from exc
    with zf:
        try:
            doc = etree.fromstring(zf.read("word/document.xml"))
        except (KeyError, etree.XMLSyntaxError) as exc:
            raise UnreadableBook("This doesn't look like a Word (.docx) file.") from exc
        title = ""
        try:
            core = etree.fromstring(zf.read("docProps/core.xml"))
            title = "".join(core.xpath("//*[local-name()='title']//text()")).strip()
        except (KeyError, etree.XMLSyntaxError):
            pass
    body = doc.find(_W + "body")
    out: list[str] = []
    if body is not None:
        _walk_docx(body, out)
    text = "\n\n".join(out)
    if not text.strip():
        raise UnreadableBook("No text was found in this Word file.")
    return title or path.stem, text
