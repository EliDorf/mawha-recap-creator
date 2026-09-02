from pathlib import Path

from PIL import Image

from mawha_recap.stages.ingest import discover_chapters, natural_key, stitch
from tests.conftest import make_strip, split_into_tiles


def test_natural_key_sorts_numbers():
    names = ["10.jpg", "2.jpg", "1.jpg", "img_12.png", "img_3.png"]
    assert sorted(names, key=natural_key) == ["1.jpg", "2.jpg", "10.jpg", "img_3.png", "img_12.png"]


def test_discover_strip_and_tiles(tmp_path: Path):
    inp = tmp_path / "input"
    make_strip(inp / "ch012.png", [500, 500])
    full = tmp_path / "full.png"
    make_strip(full, [700, 700, 700])
    split_into_tiles(full, inp / "Chapter 3", tile_h=600)
    (inp / "notes.txt").write_text("x")
    found = discover_chapters(inp)
    assert list(found) == ["ch003", "ch012"]
    assert found["ch012"]["kind"] == "strip"
    assert found["ch003"]["kind"] == "tiles" and len(found["ch003"]["files"]) == 4
    assert [p.name for p in found["ch003"]["files"]] == ["001.jpg", "002.jpg", "003.jpg", "004.jpg"]


def test_stitch_matches_original(tmp_path: Path):
    full = tmp_path / "full.png"
    make_strip(full, [800, 900, 1000])
    tiles = split_into_tiles(full, tmp_path / "tiles", tile_h=700)
    out = tmp_path / "strip.png"
    w, h, n = stitch(tiles, out)
    with Image.open(full) as a, Image.open(out) as b:
        assert (w, h) == a.size == b.size
        assert n == len(tiles)
