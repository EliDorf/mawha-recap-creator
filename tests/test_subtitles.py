from mawha_recap.config import SubtitleConfig
from mawha_recap.media.subtitles import ass_time, build_ass, build_srt, cues_for_line, srt_time, timeline_cues, wrap_lines
from mawha_recap.models import Timeline, TimelineLine, Word


def _tl(lines):
    return Timeline(chapter="ch001", sample_rate=48000, duration=100.0, lead_in=0.0, lines=lines)


def test_time_formats():
    assert ass_time(3661.256) == "1:01:01.26"
    assert srt_time(3661.2564) == "01:01:01,256"


def test_wrap_balances_two_lines():
    t = wrap_lines("the quick brown fox jumps over the lazy dog near the river bank", 30, "\\N")
    a, b = t.split("\\N")
    assert abs(len(a) - len(b)) <= 12 and len(a) <= 40 and len(b) <= 40
    assert wrap_lines("short", 30, "\\N") == "short"


def test_long_line_split_into_cues_by_word_times():
    words = [Word(w=f"w{i}", s=1.0 + i * 0.4, e=1.3 + i * 0.4) for i in range(30)]
    line = TimelineLine(id="l1", start=1.0, end=13.0, panel_ids=["p1"], text=" ".join(w.w for w in words), words=words)
    cfg = SubtitleConfig(max_chars_per_line=20)
    cues = cues_for_line(line, cfg)
    assert len(cues) >= 3
    assert cues[0][0] == 1.0 and cues[-1][1] >= 13.0
    assert all(a[1] <= b[0] + 1e-9 for a, b in zip(cues, cues[1:]))
    assert " ".join(c[2] for c in cues) == line.text


def test_ass_and_srt_build():
    lines = [
        TimelineLine(id="l1", start=2.0, end=4.0, panel_ids=["p1"], text="Hello {there}.", words=[Word(w="Hello", s=2.0, e=2.5), Word(w="there.", s=3.0, e=4.0)]),
        TimelineLine(id="l2", start=4.2, end=6.0, panel_ids=["p2"], text="Second line.", words=[]),
    ]
    tl = _tl(lines)
    cfg = SubtitleConfig()
    cues = timeline_cues(tl, cfg)
    assert cues[0][1] <= cues[1][0] - 0.05 + 1e-9  # linger clipped before the next cue
    ass = build_ass(tl, cfg, "Inter SemiBold", 1920, 1080)
    assert "PlayResX: 1920" in ass and "Style: Default,Inter SemiBold,44," in ass
    assert "Dialogue: 0,0:00:02.00," in ass and "Hello (there)." in ass
    srt = build_srt([(0.0, tl), (100.0, tl)], cfg)
    assert srt.count("-->") == 4 and "00:01:42,000 -->" in srt and srt.startswith("1\n")
