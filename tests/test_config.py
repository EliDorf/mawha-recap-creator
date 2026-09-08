from pathlib import Path

import pytest

from mawha_recap.config import chapter_id, chapter_number, load_video_config, select_chapters


def test_chapter_ids():
    assert chapter_id("ch12") == "ch012"
    assert chapter_id("Chapter 7") == "ch007"
    assert chapter_id("1004") == "ch1004"
    assert chapter_id(3) == "ch003"
    assert chapter_number("ch1004") == 1004
    with pytest.raises(ValueError):
        chapter_id("nope")


def test_select_chapters():
    avail = ["ch010", "ch002", "ch003", "ch011"]
    assert select_chapters(None, avail) == ["ch002", "ch003", "ch010", "ch011"]
    assert select_chapters("ch003-ch010", avail) == ["ch003", "ch010"]
    assert select_chapters(["11", "ch002"], avail) == ["ch002", "ch011"]
    with pytest.raises(ValueError):
        select_chapters(["ch099"], avail)


def test_presets_merge_under_user_values(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("series: S\nslug: s-1\nvariant: sleep\nstyle:\n  wpm: 120\n", encoding="utf-8")
    cfg = load_video_config(p)
    assert cfg.style.wpm == 120  # user override wins
    assert cfg.style.line_gap_s == 0.0  # sleep preset
    assert cfg.style.loudness_lufs == -22.0
    assert cfg.style.subtitles.alpha == 0.35
    p.write_text("series: S\nslug: s-1\n", encoding="utf-8")
    cfg = load_video_config(p)
    assert cfg.variant == "recap" and cfg.style.wpm == 150 and cfg.style.line_gap_s == 0.0
    assert cfg.opening.enabled
    assert not cfg.render.chapter_cards
    assert not cfg.tts.segment_on_beat and cfg.tts.line_separator == " "


def test_bad_slug(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("series: S\nslug: 'Has Space'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_video_config(p)


def test_subset_for_changes_only_with_relevant_keys(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("series: S\nslug: s-1\nvariant: sleep\n", encoding="utf-8")
    cfg = load_video_config(p)
    a = cfg.subset_for("03_tts")
    cfg2 = cfg.model_copy(deep=True)
    cfg2.package.tags = ["x"]
    assert cfg2.subset_for("03_tts") == a
    cfg2.style.line_gap_s = 0.1
    assert cfg2.subset_for("03_tts") != a
