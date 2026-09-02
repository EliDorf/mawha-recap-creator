"""Stage 1: slice a vertical strip into panels using a horizontal projection profile.

Background colour is estimated from the strip's edge columns (white and black gutters both work).
Per-row uniformity = fraction of pixels within `tolerance` of the background. Runs of uniform rows
at least `min_gutter_px` tall are gutters; we cut at their centres, merge panels shorter than
`min_panel_h`, and soft-split panels taller than `max_panel_h` (flagged `forced_split`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from ..config import SegmentConfig
from ..manifest import fingerprint, run_stage, sha256_file, sha256_text
from ..models import Panel, PanelsDoc

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "01_panels"
VERSION = "segment-v1"


@dataclass
class Interval:
    y0: int
    y1: int
    flags: list[str] = field(default_factory=list)

    @property
    def h(self) -> int:
        return self.y1 - self.y0


# ------------------------------------------------------------------ analysis


def _mode_color(sample: np.ndarray) -> np.ndarray:
    q = (sample // 16).astype(np.int32)
    keys = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    top = vals[counts.argmax()]
    return sample[keys == top].mean(axis=0).round().astype(np.int16)


def _margin_run(cols: np.ndarray, flat: np.ndarray, tol: int, from_top: bool) -> tuple[np.ndarray | None, int]:
    """Colour and length of the contiguous flat, same-coloured run at the top (or bottom) of the strip."""
    order = range(cols.shape[0]) if from_top else range(cols.shape[0] - 1, -1, -1)
    first = None
    n = 0
    for y in order:
        if not flat[y]:
            break
        color = cols[y].mean(axis=0)
        if first is None:
            first = color
        elif np.abs(color - first).max() > tol:
            break
        n += 1
    return (None, 0) if first is None else (first, n)


def estimate_background(arr: np.ndarray, flat_tol: int = 24) -> np.ndarray:
    """Background colour of the strip.

    Strips almost always begin and end with a margin, so the colour of the flat rows at the very top
    (or bottom) is the background. Solid-fill panels cannot fool this. If there is no margin at all,
    fall back to the most common colour among flat rows, then to the edge-column mode.
    """
    h, w = arr.shape[:2]
    cols = arr[:, :: max(1, w // 200)].astype(np.int16)
    spread = cols.max(axis=1) - cols.min(axis=1)
    flat = spread.max(axis=1) <= flat_tol
    top_color, top_n = _margin_run(cols, flat, flat_tol, from_top=True)
    bot_color, bot_n = _margin_run(cols, flat, flat_tol, from_top=False)
    if top_color is not None and bot_color is not None:
        if np.abs(top_color - bot_color).max() <= flat_tol:
            return ((top_color + bot_color) / 2).round().astype(np.int16)
        return (top_color if top_n >= bot_n else bot_color).round().astype(np.int16)
    if top_color is not None:
        return top_color.round().astype(np.int16)
    if bot_color is not None:
        return bot_color.round().astype(np.int16)
    idx = np.flatnonzero(flat)
    if idx.size:
        return _mode_color(cols[idx].reshape(-1, 3))
    edge = max(2, int(w * 0.03))
    sample = np.concatenate([arr[:, :edge].reshape(-1, 3), arr[:, -edge:].reshape(-1, 3)], axis=0)
    return _mode_color(sample.astype(np.int16))


def row_uniformity(arr: np.ndarray, bg: np.ndarray, tolerance: int, chunk: int = 2048) -> np.ndarray:
    h = arr.shape[0]
    out = np.empty(h, dtype=np.float32)
    for y in range(0, h, chunk):
        block = arr[y : y + chunk].astype(np.int16)
        diff = np.abs(block - bg).max(axis=2)
        out[y : y + chunk] = (diff <= tolerance).mean(axis=1)
    return out


def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return [start, end) runs of True."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]])
    d = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def gutter_cuts(uni: np.ndarray, thr: float, min_gutter_px: int) -> list[int]:
    h = uni.shape[0]
    cuts: list[int] = []
    for s, e in find_runs(uni > thr):
        if e - s < min_gutter_px:
            continue
        if s == 0 or e == h:
            continue  # top / bottom margin, not a gutter between panels
        cuts.append((s + e) // 2)
    return cuts


def _trim(y0: int, y1: int, uni: np.ndarray, thr: float) -> tuple[int, int]:
    while y0 < y1 and uni[y0] > thr:
        y0 += 1
    while y1 > y0 and uni[y1 - 1] > thr:
        y1 -= 1
    return y0, y1


def build_intervals(
    h: int, cuts: list[int], uni: np.ndarray, cfg: SegmentConfig, trim: bool = True
) -> list[Interval]:
    bounds = [0, *sorted(set(c for c in cuts if 0 < c < h)), h]
    ivs: list[Interval] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        y0, y1 = (_trim(a, b, uni, cfg.uniformity) if trim else (a, b))
        if y1 - y0 <= 0:
            continue
        ivs.append(Interval(y0, y1))

    # merge panels that are too short into a neighbour
    merged: list[Interval] = []
    for iv in ivs:
        if iv.h < cfg.min_panel_h and merged:
            prev = merged[-1]
            prev.y1 = iv.y1
            prev.flags = sorted(set(prev.flags + iv.flags + ["merged"]))
        else:
            merged.append(iv)
    if len(merged) >= 2 and merged[0].h < cfg.min_panel_h:
        first = merged.pop(0)
        merged[0].y0 = first.y0
        merged[0].flags = sorted(set(merged[0].flags + ["merged"]))

    # soft-split panels that are too tall at the least-inky row of their middle band
    out: list[Interval] = []
    stack = list(merged)
    while stack:
        iv = stack.pop(0)
        if iv.h <= cfg.max_panel_h:
            out.append(iv)
            continue
        lo = iv.y0 + int(iv.h * 0.3)
        hi = iv.y0 + int(iv.h * 0.7)
        band = uni[lo:hi]
        cut = lo + int(band.argmax())
        top = Interval(iv.y0, cut, sorted(set(iv.flags + ["forced_split"])))
        bottom = Interval(cut, iv.y1, sorted(set(iv.flags + ["forced_split"])))
        out.append(top)
        stack.insert(0, bottom)
    return out


def apply_overrides(cuts: list[int], overrides: dict | None, h: int) -> list[int]:
    if not overrides:
        return cuts
    cuts = list(cuts)
    for y in overrides.get("remove_cuts", []):
        if cuts:
            nearest = min(cuts, key=lambda c: abs(c - y))
            if abs(nearest - y) <= 60:
                cuts.remove(nearest)
    for y in overrides.get("split_rows", []):
        if 0 < int(y) < h:
            cuts.append(int(y))
    return sorted(set(cuts))


# ------------------------------------------------------------------ main entry


def segment_strip(
    strip_path: Path, out_dir: Path, chapter: str, cfg: SegmentConfig, overrides: dict | None = None
) -> PanelsDoc:
    with Image.open(strip_path) as im:
        im = im.convert("RGB")
        arr = np.asarray(im)
        h, w = arr.shape[:2]
        bg = estimate_background(arr)
        uni = row_uniformity(arr, bg, cfg.tolerance)

        if overrides and overrides.get("panels"):
            ivs = [Interval(int(a), int(b), ["manual"]) for a, b in overrides["panels"]]
        else:
            cuts = apply_overrides(gutter_cuts(uni, cfg.uniformity, cfg.min_gutter_px), overrides, h)
            ivs = build_intervals(h, cuts, uni, cfg, trim=cfg.trim)
            if overrides and (overrides.get("split_rows") or overrides.get("remove_cuts")):
                for iv in ivs:
                    iv.flags = sorted(set(iv.flags + ["overridden"]))

        skip = set(overrides.get("skip", [])) if overrides else set()
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob(f"{chapter}_p*.png"):
            old.unlink()
        panels: list[Panel] = []
        for i, iv in enumerate(ivs, start=1):
            pid = f"{chapter}_p{i:03d}"
            fname = f"{pid}.png"
            crop = im.crop((0, iv.y0, w, iv.y1))
            crop.save(out_dir / fname, format="PNG", compress_level=1)
            flags = list(iv.flags)
            if pid in skip:
                flags.append("skip")
            panels.append(
                Panel(
                    id=pid,
                    y0=iv.y0,
                    y1=iv.y1,
                    w=w,
                    h=iv.h,
                    file=fname,
                    sha256=sha256_file(out_dir / fname),
                    ink=round(float(1.0 - uni[iv.y0 : iv.y1].mean()), 4) if iv.h else 0.0,
                    flags=sorted(set(flags)),
                )
            )
    doc = PanelsDoc(
        chapter=chapter,
        strip={"path": strip_path.name, "w": w, "h": h, "sha256": sha256_file(strip_path)},
        bg_rgb=[int(x) for x in bg],
        params=cfg.model_dump(),
        panels=panels,
    )
    (out_dir / "panels.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return doc


def load_panels(path: Path) -> PanelsDoc:
    return PanelsDoc.model_validate_json(path.read_text(encoding="utf-8"))


def run_chapter(ctx: Context, ch: str) -> str:
    project = ctx.project
    strip = project.strip_path(ch)
    if not strip.exists():
        raise FileNotFoundError(f"{ch}: run stage 0 first ({strip} missing)")
    ov_path = project.panels_overrides(ch)
    overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else None
    fp = fingerprint(
        stage=STAGE,
        version=VERSION,
        strip=sha256_file(strip),
        config=ctx.config.subset_for(STAGE),
        overrides=sha256_text(json.dumps(overrides, sort_keys=True)) if overrides else None,
    )
    out_json = project.panels_json(ch)

    def fn() -> dict:
        doc = segment_strip(strip, project.panels_dir(ch), ch, ctx.config.segment, overrides)
        flags = sorted({f for p in doc.panels for f in p.flags if f in ("forced_split", "manual", "overridden")})
        return {"flags": flags, "panels": len(doc.panels)}

    return run_stage(ctx.manifest, ch, STAGE, fp, [out_json], fn, force=ctx.force)
