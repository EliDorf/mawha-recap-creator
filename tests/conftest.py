"""Fixtures: synthetic strips with known panel boundaries, and stub project directories."""

from __future__ import annotations

from pathlib import Path

import pytest

from mawha_recap.synthetic import make_strip, split_into_tiles

__all__ = ["make_strip", "split_into_tiles"]


@pytest.fixture
def strip_factory(tmp_path: Path):
    def _make(name: str = "ch001.png", **kw):
        p = tmp_path / name
        bounds = make_strip(p, **kw)
        return p, bounds

    return _make


def write_project(root: Path, chapters: dict[str, list[int]], variant: str = "sleep", runtime: float = 3.0) -> Path:
    """Create a project dir with config.yaml and synthetic input strips. Returns the root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "series: Test Series\n"
        f"slug: test-{variant}\n"
        f"variant: {variant}\n"
        f"target_runtime_min: {runtime}\n"
        "voice: {id: stub-voice}\n"
        "opening: {enabled: false}\n"
        "render: {chapter_cards: true, workers: 2, title_card_s: 1.0, width: 640, height: 360}\n",
        encoding="utf-8",
    )
    for i, (ch, heights) in enumerate(chapters.items()):
        if i % 2 == 0:
            make_strip(root / "input" / f"{ch}.png", heights, seed=i + 1)
        else:
            tmp = root / "input" / f"_{ch}_full.png"
            make_strip(tmp, heights, seed=i + 1)
            split_into_tiles(tmp, root / "input" / ch)
            tmp.unlink()
    return root


@pytest.fixture
def project_factory(tmp_path: Path):
    def _make(chapters: dict[str, list[int]] | None = None, **kw) -> Path:
        chapters = chapters or {"ch001": [900, 1400, 700], "ch002": [1100, 600, 1300, 800]}
        return write_project(tmp_path / "proj", chapters, **kw)

    return _make
