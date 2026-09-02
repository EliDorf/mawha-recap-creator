"""ASS (burn-in) and SRT (sidecar) subtitles from timeline word timings."""

from __future__ import annotations

from ..config import SubtitleConfig
from ..models import Timeline, TimelineLine

LINGER_S = 0.35
MIN_GAP_S = 0.05


def ass_time(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def srt_time(s: float) -> str:
    s = max(0.0, s)
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def wrap_lines(text: str, max_chars: int, newline: str) -> str:
    """Balance a cue over at most two lines of about `max_chars` characters."""
    words = text.split()
    if len(text) <= max_chars or len(words) < 2:
        return text
    best_i, best_score = 1, float("inf")
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b)) + (1000 if max(len(a), len(b)) > max_chars * 1.25 else 0)
        if score < best_score:
            best_i, best_score = i, score
    return " ".join(words[:best_i]) + newline + " ".join(words[best_i:])


def cues_for_line(line: TimelineLine, cfg: SubtitleConfig) -> list[tuple[float, float, str]]:
    """Split a long line into cues of <= 2 display lines using word timings."""
    limit = cfg.max_chars_per_line * 2
    text = " ".join(line.text.split())
    if len(text) <= limit or not line.words:
        return [(line.start, line.end, text)]
    # group subtitle words (from `text`) with timeline words (same order, tags already removed)
    words = text.split()
    times = line.words
    cues: list[tuple[float, float, str]] = []
    chunk: list[str] = []
    chunk_start = None
    chunk_end = line.start
    for i, w in enumerate(words):
        t = times[min(i, len(times) - 1)]
        if chunk and len(" ".join(chunk + [w])) > limit:
            cues.append((chunk_start, chunk_end, " ".join(chunk)))  # type: ignore[arg-type]
            chunk, chunk_start = [], None
        if chunk_start is None:
            chunk_start = t.s
        chunk.append(w)
        chunk_end = t.e
    if chunk:
        cues.append((chunk_start, max(chunk_end, line.end), " ".join(chunk)))  # type: ignore[arg-type]
    return cues


def timeline_cues(tl: Timeline, cfg: SubtitleConfig, offset: float = 0.0) -> list[tuple[float, float, str]]:
    raw: list[tuple[float, float, str]] = []
    for line in tl.lines:
        raw.extend(cues_for_line(line, cfg))
    out: list[tuple[float, float, str]] = []
    for i, (s, e, text) in enumerate(raw):
        end = e + LINGER_S
        if i + 1 < len(raw):
            end = min(end, raw[i + 1][0] - MIN_GAP_S)
        end = max(end, s + 0.3)
        out.append((s + offset, end + offset, text))
    return out


def ass_color(hex_rgb: str, alpha: float) -> str:
    """ASS colours are &HAABBGGRR."""
    r, g, b = int(hex_rgb[1:3], 16), int(hex_rgb[3:5], 16), int(hex_rgb[5:7], 16)
    a = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def build_ass(tl: Timeline, cfg: SubtitleConfig, font_name: str, width: int, height: int) -> str:
    primary = ass_color("#FFFFFF", cfg.alpha)
    outline = ass_color("#000000", cfg.alpha)
    back = ass_color("#000000", max(cfg.alpha, 0.6))
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{cfg.font_size},{primary},&H000000FF,{outline},{back},0,0,0,0,100,100,0,0,1,"
        f"{cfg.outline},1,2,80,80,{cfg.margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for s, e, text in timeline_cues(tl, cfg):
        t = wrap_lines(text, cfg.max_chars_per_line, "\\N").replace("{", "(").replace("}", ")")
        events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{t}")
    return header + "\n".join(events) + "\n"


def build_srt(chapters: list[tuple[float, Timeline]], cfg: SubtitleConfig) -> str:
    out = []
    n = 1
    for offset, tl in chapters:
        for s, e, text in timeline_cues(tl, cfg, offset):
            out.append(f"{n}\n{srt_time(s)} --> {srt_time(e)}\n{wrap_lines(text, cfg.max_chars_per_line, chr(10))}\n")
            n += 1
    return "\n".join(out)
