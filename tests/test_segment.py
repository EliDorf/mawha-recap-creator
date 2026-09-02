from pathlib import Path

from mawha_recap.config import SegmentConfig
from mawha_recap.stages.segment import segment_strip
from tests.conftest import make_strip


def _check(bounds, panels, tol=2):
    assert len(panels) == len(bounds), [(p.y0, p.y1, p.flags) for p in panels]
    for (y0, y1), p in zip(bounds, panels, strict=True):
        assert abs(p.y0 - y0) <= tol and abs(p.y1 - y1) <= tol, (p.y0, p.y1, y0, y1)


def test_white_gutters(tmp_path: Path):
    strip = tmp_path / "ch001.png"
    bounds = make_strip(strip, [900, 1400, 700, 1100], gutter=40)
    doc = segment_strip(strip, tmp_path / "out", "ch001", SegmentConfig())
    _check(bounds, doc.panels)
    assert doc.bg_rgb == [255, 255, 255]
    assert all((tmp_path / "out" / p.file).exists() for p in doc.panels)
    assert doc.panels[0].id == "ch001_p001"
    assert all(p.ink > 0.05 for p in doc.panels)


def test_black_gutters(tmp_path: Path):
    strip = tmp_path / "ch002.png"
    bounds = make_strip(strip, [800, 1200, 900], gutter=60, bg=(0, 0, 0))
    doc = segment_strip(strip, tmp_path / "out", "ch002", SegmentConfig())
    _check(bounds, doc.panels)
    assert doc.bg_rgb == [0, 0, 0]


def test_tall_panel_is_soft_split_and_flagged(tmp_path: Path):
    strip = tmp_path / "ch003.png"
    make_strip(strip, [800, 4200, 600], gutter=40)
    doc = segment_strip(strip, tmp_path / "out", "ch003", SegmentConfig(max_panel_h=3200))
    assert len(doc.panels) == 4
    flagged = [p for p in doc.panels if "forced_split" in p.flags]
    assert len(flagged) == 2
    assert all(p.h <= 3200 for p in doc.panels)


def test_short_panels_are_merged(tmp_path: Path):
    strip = tmp_path / "ch004.png"
    make_strip(strip, [900, 120, 900], gutter=30)
    doc = segment_strip(strip, tmp_path / "out", "ch004", SegmentConfig(min_panel_h=200))
    assert len(doc.panels) == 2
    assert "merged" in doc.panels[0].flags


def test_overrides_split_and_remove(tmp_path: Path):
    strip = tmp_path / "ch005.png"
    bounds = make_strip(strip, [1000, 1000], gutter=40)
    mid = (bounds[0][0] + bounds[0][1]) // 2
    doc = segment_strip(
        strip,
        tmp_path / "out",
        "ch005",
        SegmentConfig(),
        overrides={"split_rows": [mid], "skip": ["ch005_p003"]},
    )
    assert len(doc.panels) == 3
    assert "skip" in doc.panels[2].flags
    assert all("overridden" in p.flags for p in doc.panels)
    gutter_mid = (bounds[0][1] + bounds[1][0]) // 2
    doc2 = segment_strip(strip, tmp_path / "out2", "ch005", SegmentConfig(), overrides={"remove_cuts": [gutter_mid]})
    assert len(doc2.panels) == 1
    doc3 = segment_strip(strip, tmp_path / "out3", "ch005", SegmentConfig(), overrides={"panels": [[10, 500], [600, 900]]})
    assert [(p.y0, p.y1) for p in doc3.panels] == [(10, 500), (600, 900)]
    assert all("manual" in p.flags for p in doc3.panels)
