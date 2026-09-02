"""Character alignment -> word and line timings, with ElevenLabs audio tags masked out."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from ..models import Word

TAG_RE = re.compile(r"\[[^\[\]\n]{1,40}\]")
SEP = "\n\n"


def strip_tags(text: str) -> str:
    """Remove [audio tags] and tidy whitespace; newlines are kept (they separate lines in a segment)."""
    out = TAG_RE.sub("", text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    return out.strip()


def build_segment_text(line_texts: list[str], sep: str = SEP) -> tuple[str, list[tuple[int, int]]]:
    """Join line texts into one segment; return the text and each line's [start, end) span."""
    text = ""
    spans: list[tuple[int, int]] = []
    for i, t in enumerate(line_texts):
        if i:
            text += sep
        start = len(text)
        text += t
        spans.append((start, len(text)))
    return text, spans


@dataclass
class CharAlignment:
    characters: list[str]
    starts: list[float]
    ends: list[float]

    @property
    def text(self) -> str:
        return "".join(self.characters)


def map_alignment(text: str, align: CharAlignment) -> tuple[list[float | None], list[float | None]]:
    """Per character of `text`: (start, end) seconds, None where the aligner had nothing."""
    n = len(text)
    starts: list[float | None] = [None] * n
    ends: list[float | None] = [None] * n
    if align.text == text:
        for i in range(n):
            starts[i] = float(align.starts[i])
            ends[i] = float(align.ends[i])
        return starts, ends
    sm = difflib.SequenceMatcher(None, list(text), align.characters, autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            starts[a + k] = float(align.starts[b + k])
            ends[a + k] = float(align.ends[b + k])
    return starts, ends


def word_spans(text: str, span: tuple[int, int]) -> list[tuple[str, int, int]]:
    """Whitespace tokens inside `span` as (clean_word, start_idx, end_idx); tags are dropped."""
    s0, s1 = span
    out: list[tuple[str, int, int]] = []
    for m in re.finditer(r"\S+", text[s0:s1]):
        raw = m.group(0)
        clean = TAG_RE.sub("", raw)
        if not clean:
            continue
        out.append((clean, s0 + m.start(), s0 + m.end()))
    return out


def _fill(values: list[float | None], lo: float, hi: float) -> list[float]:
    """Linear interpolation for None entries between known neighbours (or the bounds)."""
    n = len(values)
    out: list[float] = [0.0] * n
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return [lo + (hi - lo) * (i + 0.5) / n for i in range(n)]
    for i in range(n):
        v = values[i]
        if v is not None:
            out[i] = v
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        pv = values[prev] if prev is not None else lo
        nv = values[nxt] if nxt is not None else hi
        pi = prev if prev is not None else -1
        ni = nxt if nxt is not None else n
        out[i] = pv + (nv - pv) * (i - pi) / (ni - pi)  # type: ignore[operator]
    return out


def line_timing(
    text: str, span: tuple[int, int], starts: list[float | None], ends: list[float | None], seg_end: float
) -> tuple[float, float, list[Word]]:
    """(line_start, line_end, words) in segment seconds for the characters in `span`."""
    ws = word_spans(text, span)
    w_starts: list[float | None] = []
    w_ends: list[float | None] = []
    for _w, a, b in ws:
        cs = [starts[i] for i in range(a, b) if starts[i] is not None]
        ce = [ends[i] for i in range(a, b) if ends[i] is not None]
        w_starts.append(min(cs) if cs else None)
        w_ends.append(max(ce) if ce else None)
    if not ws:
        cs = [starts[i] for i in range(span[0], span[1]) if starts[i] is not None]
        ce = [ends[i] for i in range(span[0], span[1]) if ends[i] is not None]
        s = min(cs) if cs else 0.0
        e = max(ce) if ce else s
        return s, e, []
    lo = min((v for v in w_starts if v is not None), default=0.0)
    hi = max((v for v in w_ends if v is not None), default=seg_end)
    fs = _fill(w_starts, lo, hi)
    fe = _fill(w_ends, lo, hi)
    words: list[Word] = []
    for (w, _a, _b), s, e in zip(ws, fs, fe, strict=True):
        e = max(e, s)
        words.append(Word(w=w, s=round(s, 3), e=round(e, 3)))
    return words[0].s, words[-1].e, words
