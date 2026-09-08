"""FFmpeg output formats vary across versions."""

from subprocess import CompletedProcess

import pytest

from mawha_recap.media import ffmpeg as ff


@pytest.mark.parametrize("flags", ["..", "...", "T.", "T.."])
def test_filter_listing_accepts_old_and_new_flags(monkeypatch, flags):
    monkeypatch.setattr(
        ff, "run", lambda args: CompletedProcess(args, 0, f" {flags} subtitles V->V Render captions\n", "")
    )
    ff.filters.cache_clear()
    try:
        assert "subtitles" in ff.filters()
    finally:
        ff.filters.cache_clear()


@pytest.mark.parametrize("tail", ["", "\n[out#0/null] video:0KiB audio:1500KiB\nsize=N/A time=00:00:02.00\n"])
def test_loudnorm_stats_allow_trailing_progress(monkeypatch, tail):
    stats = '{"input_i":"-22.05", "target_offset":"-0.05"}'
    monkeypatch.setattr(
        ff,
        "run",
        lambda args, **kwargs: CompletedProcess(args, 0, "", "[Parsed_loudnorm_1]\n" + stats + tail),
    )
    assert ff.loudnorm_measure("test.wav", -16)["input_i"] == "-22.05"


@pytest.mark.parametrize(
    "major,option", [(6, "-filter_complex_script"), (8, "-filter_complex_script"), (9, "-/filter_complex")]
)
def test_filter_script_option_matches_ffmpeg_version(monkeypatch, major, option):
    monkeypatch.setattr(ff, "major_version", lambda: major)
    assert ff.filter_script_args("a graph.txt") == [option, "a graph.txt"]
