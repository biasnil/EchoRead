"""Optional article fetching. Only ever used when Internet Usage is switched on."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .paragraphs import ParagraphModel
from .settings import SettingsManager


class WebExtractError(Exception):
    """A fetch was refused or failed; the message is shown to the user."""


@dataclass
class ExtractedArticle:
    title: str
    text: str
    url: str


class WebExtractor:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
    MAX_BYTES = 6 * 1024 * 1024
    TIMEOUT = 25

    def __init__(self, settings: SettingsManager):
        self._settings = settings

    def extract(self, url: str) -> ExtractedArticle:
        if not self._settings.internet_usage:
            raise WebExtractError("Internet Usage is off. Enable it in Settings to fetch web pages.")
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise WebExtractError("That doesn't look like a web address (it should start with http:// or https://).")
        import requests

        headers = {"User-Agent": self.USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        self._check_robots(requests, parsed, headers)
        try:
            resp = requests.get(url, headers=headers, timeout=self.TIMEOUT, stream=True)
            resp.raise_for_status()
            content = self._read_limited(resp)
        except requests.RequestException as exc:
            raise WebExtractError(f"Could not fetch the page: {exc}") from exc
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower():
            raise WebExtractError(f"That address isn't a web page (it returned {ctype or 'unknown content'}).")
        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        title, text = self.parse_html(content.decode(encoding, errors="replace"), url)
        if not text.strip():
            raise WebExtractError("No readable article text was found on that page.")
        return ExtractedArticle(title, text, url)

    # -- pieces
    def _check_robots(self, requests, parsed, headers) -> None:
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            resp = requests.get(robots_url, headers=headers, timeout=self.TIMEOUT)
        except requests.RequestException as exc:
            raise WebExtractError(f"Could not check the site's robots.txt: {exc}") from exc
        if resp.status_code >= 500:
            raise WebExtractError("The site's robots.txt could not be read (server error), so the page was not fetched.")
        if resp.status_code != 200:
            return  # no robots.txt (or not accessible): nothing forbids the fetch
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        if not parser.can_fetch(self.USER_AGENT, parsed.geturl()):
            raise WebExtractError("This site's robots.txt asks automated tools not to fetch that page, so EchoRead won't.")

    def _read_limited(self, resp) -> bytes:
        chunks, total = [], 0
        for chunk in resp.iter_content(65536):
            total += len(chunk)
            if total > self.MAX_BYTES:
                raise WebExtractError("That page is too large to read.")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def parse_html(html: str, url: str = "") -> tuple[str, str]:
        """Readability extraction -> (title, paragraphs separated by blank lines)."""
        from lxml import html as lh

        title, body = url, html
        try:
            from readability import Document

            doc = Document(html)
            title, body = doc.short_title() or url, doc.summary()
        except ImportError:
            pass
        tree = lh.fromstring(body)
        h1 = [ParagraphModel.clean(el.text_content()) for el in tree.xpath("//h1")]
        if h1 and h1[0]:  # the article's own heading beats "Page title - Site name"
            title = h1[0][:120]
        parts = [ParagraphModel.clean(el.text_content()) for el in tree.xpath("//h1|//h2|//h3|//p|//li")]
        return title, "\n\n".join(p for p in parts if p)
