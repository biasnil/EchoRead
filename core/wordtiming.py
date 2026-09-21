"""Where in a paragraph's audio each word / sentence is spoken.

Preferred source: the word timings the speech model reports (pykokoro `word_timings`, stored next to the cached audio).
Fallback (always available): split the paragraph into words and spread the audio over them in proportion to their length,
with a longer pause after commas and full stops. Both give the same thing: spans of characters with a start/end fraction (0..1)
of the paragraph's audio, so the reader never needs to know which one it got.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_WORD = re.compile(r"[^\W_]+(?:['’.,\-][^\W_]+)*")
_CJK_CHAR = re.compile(f"[{_CJK}]")
_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])[\"'”’)\]]*\s+|(?<=[。！？])")


@dataclass(frozen=True)
class Span:
    start: int  # first character
    end: int  # one past the last character
    f0: float  # where it starts in the paragraph's audio, 0..1
    f1: float  # where it ends


class WordTimeline:
    """Words and sentences of one paragraph with their positions in the audio."""

    def __init__(self, text: str, timings=None):
        self.text = text
        self.source = "estimate"
        words = self._from_engine(text, timings) if timings else None
        if words is None:
            words = self._estimate(text)
        else:
            self.source = "model"
        self.words: list[Span] = words
        self._starts = [w.f0 for w in words]
        self._char_starts = [w.start for w in words]
        self.sentences: list[Span] = self._build_sentences(text, words)
        self._sentence_starts = [s.f0 for s in self.sentences]

    # ------------------------------------------------------------------ building
    @staticmethod
    def word_spans(text: str) -> list[tuple[int, int]]:
        """Character spans of the words (a CJK character counts as a word of its own)."""
        spans: list[tuple[int, int]] = []
        for m in _WORD.finditer(text):
            if _CJK_CHAR.search(m.group()):
                for i, ch in enumerate(m.group()):
                    if _CJK_CHAR.match(ch):
                        spans.append((m.start() + i, m.start() + i + 1))
                    elif ch.strip():
                        j = m.start() + i
                        if spans and spans[-1][1] == j:
                            spans[-1] = (spans[-1][0], j + 1)
                        else:
                            spans.append((j, j + 1))
            else:
                spans.append((m.start(), m.end()))
        return spans

    @classmethod
    def _estimate(cls, text: str) -> list[Span]:
        spans = cls.word_spans(text)
        if not spans:
            return []
        weights = []
        for k, (s, e) in enumerate(spans):
            nxt = spans[k + 1][0] if k + 1 < len(spans) else len(text)
            gap = text[e:nxt]
            w = float(max(1, e - s)) + 0.6  # every word costs a little more than its letters
            if re.search(r"[.!?…。！？]", gap):
                w += 4.0
            elif re.search(r"[,;:—–、，；：]", gap):
                w += 2.0
            weights.append(w)
        total = sum(weights)
        out, acc = [], 0.0
        for (s, e), w in zip(spans, weights):
            out.append(Span(s, e, acc / total, (acc + w) / total))
            acc += w
        return out

    @staticmethod
    def _from_engine(text: str, raw) -> list[Span] | None:
        """Accept model timings ([char_start, char_end, f0, f1] each) only if they look sane."""
        try:
            words = [Span(int(a), int(b), float(c), float(d)) for a, b, c, d in raw]
        except (TypeError, ValueError):
            return None
        if not words:
            return None
        last_start, last_f = -1, -1.0
        for w in words:
            if not (0 <= w.start < w.end <= len(text)) or not (0.0 <= w.f0 <= w.f1 <= 1.0001):
                return None
            if w.start < last_start or w.f0 + 1e-6 < last_f:
                return None
            last_start, last_f = w.start, w.f0
        expected = len(WordTimeline.word_spans(text))
        if expected and len(words) < 0.5 * expected:
            return None  # the model timed too few words to be useful
        return words

    @staticmethod
    def _build_sentences(text: str, words: list[Span]) -> list[Span]:
        if not words:
            return []
        cuts = [0]
        for m in _SENTENCE_END.finditer(text):
            if m.end() < len(text):
                cuts.append(m.end())
        cuts.append(len(text))
        out: list[Span] = []
        for a, b in zip(cuts, cuts[1:]):
            inside = [w for w in words if a <= w.start < b]
            if not inside:
                continue
            start = a + (len(text[a:b]) - len(text[a:b].lstrip()))
            end = len(text[:b].rstrip())  # include the full stop / closing quote
            out.append(Span(start, end, inside[0].f0, inside[-1].f1))
        return out

    # ------------------------------------------------------------------ queries
    def word_at(self, fraction: float) -> int:
        """Index of the word being spoken at `fraction` of the audio (the last one that has started), or -1."""
        if not self.words:
            return -1
        return max(0, bisect.bisect_right(self._starts, max(0.0, fraction)) - 1)

    def sentence_at(self, fraction: float) -> int:
        if not self.sentences:
            return -1
        return max(0, bisect.bisect_right(self._sentence_starts, max(0.0, fraction)) - 1)

    def word_index_at_char(self, pos: int) -> int:
        """The word containing character `pos`, else the next word after it (or the last word)."""
        if not self.words:
            return -1
        i = bisect.bisect_right(self._char_starts, pos) - 1
        if i >= 0 and pos < self.words[i].end:
            return i
        return min(i + 1, len(self.words) - 1)

    def fraction_of_word(self, index: int) -> float:
        if not self.words:
            return 0.0
        return self.words[max(0, min(index, len(self.words) - 1))].f0

    def fraction_at_char(self, pos: int) -> float:
        """Where to start playing so the word at character `pos` is the first one heard."""
        i = self.word_index_at_char(pos)
        if i < 0:
            return 0.0
        f = self.fraction_of_word(i)
        # a hair inside the word: the audio position is a whole number of samples, and landing a sample early would
        # light up the previous word
        return f + 1e-4 if f > 0 else 0.0

    def word_span(self, index: int) -> tuple[int, int] | None:
        return (self.words[index].start, self.words[index].end) if 0 <= index < len(self.words) else None

    def sentence_span(self, index: int) -> tuple[int, int] | None:
        return (self.sentences[index].start, self.sentences[index].end) if 0 <= index < len(self.sentences) else None
