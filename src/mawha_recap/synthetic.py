"""Synthetic webtoon-like strips for tests and the offline demo (`recap demo`)."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

WIDTH = 800


def draw_content(draw: ImageDraw.ImageDraw, y0: int, y1: int, w: int, rng: random.Random, fg) -> None:
    """Fill a panel region with non-uniform content: a fill, some blobs and text-like bars."""
    draw.rectangle([0, y0, w - 1, y1 - 1], fill=fg)
    for _ in range(12):
        x = rng.randint(0, w - 80)
        y = rng.randint(y0, max(y0, y1 - 60))
        r = rng.randint(20, 70)
        color = tuple(rng.randint(0, 200) for _ in range(3))
        draw.ellipse([x, y, min(w - 1, x + r), min(y1 - 1, y + r)], fill=color)
    for yy in range(y0 + 10, y1 - 10, 37):
        draw.line([(10, yy), (w - 10, yy)], fill=(20, 20, 20), width=3)


def make_strip(
    path: Path,
    panel_heights: list[int],
    gutter: int = 40,
    bg=(255, 255, 255),
    width: int = WIDTH,
    margin: int = 30,
    seed: int = 1,
) -> list[tuple[int, int]]:
    """Write a strip PNG and return the true [y0, y1) of every panel."""
    rng = random.Random(seed)
    total = margin * 2 + sum(panel_heights) + gutter * (len(panel_heights) - 1)
    im = Image.new("RGB", (width, total), bg)
    draw = ImageDraw.Draw(im)
    bounds = []
    y = margin
    fg = (200, 215, 235) if sum(bg) > 380 else (70, 60, 90)
    for h in panel_heights:
        draw_content(draw, y, y + h, width, rng, fg)
        bounds.append((y, y + h))
        y += h + gutter
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return bounds


def split_into_tiles(strip: Path, out_dir: Path, tile_h: int = 1200) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    with Image.open(strip) as im:
        for i, y in enumerate(range(0, im.height, tile_h), start=1):
            tile = im.crop((0, y, im.width, min(im.height, y + tile_h)))
            p = out_dir / f"{i:03d}.jpg"
            tile.save(p, quality=92)
            files.append(p)
    return files


def write_demo_project(root: Path, chapters: int = 3, variant: str = "sleep", runtime_min: float = 2.0) -> Path:
    """A small self-contained project: mixed strip/tile inputs, config.yaml, series.yaml."""
    rng = random.Random(7)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "series: Demo Series\n"
        f"slug: demo-{variant}\n"
        f"variant: {variant}\n"
        f"target_runtime_min: {runtime_min}\n"
        "voice: {id: '', model: eleven_v3}\n"
        "style:\n"
        "  voice_guide: |\n"
        "    Warm, steady narrator. Present tense. Plain language.\n"
        "render: {title_card_s: 2.0}\n",
        encoding="utf-8",
    )
    (root / "series.yaml").write_text(
        "name: Demo Series\ncharacters:\n  - name: Demo Hero\n    aliases: [the hunter]\n    notes: protagonist\n", encoding="utf-8"
    )
    for i in range(1, chapters + 1):
        ch = f"ch{i:03d}"
        heights = [rng.choice([500, 700, 900, 1200, 1500, 2600]) for _ in range(rng.randint(4, 7))]
        bg = (255, 255, 255) if i % 3 else (0, 0, 0)
        if i % 2:
            make_strip(root / "input" / f"{ch}.png", heights, bg=bg, seed=i)
        else:
            tmp = root / "input" / f"_{ch}_full.png"
            make_strip(tmp, heights, bg=bg, seed=i)
            split_into_tiles(tmp, root / "input" / ch)
            tmp.unlink()
    return root
