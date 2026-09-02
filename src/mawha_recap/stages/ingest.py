"""Stage 0: discover chapters under input/, stitch tiles into one strip per chapter, write meta.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from ..config import chapter_id, chapter_number
from ..manifest import fingerprint, run_stage, sha256_file

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "00_ingest"
VERSION = "ingest-v1"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

Image.MAX_IMAGE_PIXELS = None  # webtoon strips are legitimately huge; we sanity-check sizes ourselves
MAX_STRIP_PIXELS = 1_500_000_000


def natural_key(name: str) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _image_files(d: Path) -> list[Path]:
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")]
    return sorted(files, key=lambda p: natural_key(p.name))


def discover_chapters(input_dir: Path) -> dict[str, dict]:
    """Return {chapter_id: {"kind": "strip"|"tiles", "files": [Path, ...]}} sorted by chapter number."""
    found: dict[str, dict] = {}
    if not input_dir.exists():
        return found
    for entry in sorted(input_dir.iterdir(), key=lambda p: natural_key(p.name)):
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                files = _image_files(entry)
                if not files:
                    continue
                ch = chapter_id(entry.name)
                kind, flist = ("tiles", files) if len(files) > 1 else ("strip", files)
            elif entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
                ch = chapter_id(entry.stem)
                kind, flist = "strip", [entry]
            else:
                continue
        except ValueError:
            continue  # no chapter number in the name
        if ch in found:
            raise ValueError(f"chapter {ch} appears twice under {input_dir} ({found[ch]['files'][0]} and {flist[0]})")
        found[ch] = {"kind": kind, "files": flist}
    return dict(sorted(found.items(), key=lambda kv: chapter_number(kv[0])))


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def stitch(files: list[Path], out_path: Path) -> tuple[int, int, int]:
    """Vertically stitch tiles (resized to the modal width) into one PNG. Returns (w, h, n_tiles)."""
    sizes = []
    for f in files:
        with Image.open(f) as im:
            sizes.append(im.size)
    widths = [w for w, _ in sizes]
    target_w = max(set(widths), key=widths.count)
    heights = [round(h * target_w / w) if w != target_w else h for w, h in sizes]
    total_h = sum(heights)
    if target_w * total_h > MAX_STRIP_PIXELS:
        raise ValueError(f"stitched strip would be {target_w}x{total_h}, refusing (> {MAX_STRIP_PIXELS} px)")
    canvas = Image.new("RGB", (target_w, total_h), (255, 255, 255))
    y = 0
    for f, (w, _h), h2 in zip(files, sizes, heights, strict=True):
        with Image.open(f) as im:
            im = _to_rgb(im)
            if w != target_w:
                im = im.resize((target_w, h2), Image.LANCZOS)
            canvas.paste(im, (0, y))
        y += h2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", compress_level=1)
    return target_w, total_h, len(files)


def convert_single(src: Path, out_path: Path) -> tuple[int, int, int]:
    with Image.open(src) as im:
        im = _to_rgb(im)
        if im.width * im.height > MAX_STRIP_PIXELS:
            raise ValueError(f"{src} is {im.width}x{im.height}, refusing (> {MAX_STRIP_PIXELS} px)")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, format="PNG", compress_level=1)
        return im.width, im.height, 1


def run_chapter(ctx: Context, ch: str) -> str:
    project = ctx.project
    entry = discover_chapters(project.input_dir).get(ch)
    if entry is None:
        raise FileNotFoundError(f"{ch}: nothing under {project.input_dir}")
    files: list[Path] = entry["files"]
    sources = [{"file": project.rel(f), "sha256": sha256_file(f)} for f in files]
    fp = fingerprint(stage=STAGE, version=VERSION, kind=entry["kind"], sources=sources)
    strip = project.strip_path(ch)
    meta = project.ingest_meta(ch)

    def fn() -> dict:
        if entry["kind"] == "tiles":
            w, h, n = stitch(files, strip)
        else:
            w, h, n = convert_single(files[0], strip)
        meta.write_text(
            json.dumps(
                {"chapter": ch, "kind": entry["kind"], "width": w, "height": h, "tiles": n, "sources": sources},
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"size": [w, h], "tiles": n}

    return run_stage(ctx.manifest, ch, STAGE, fp, [strip, meta], fn, force=ctx.force)
