"""Stage 4: the automated edit for one chapter.

Level 1 renders one small cached clip per panel (Ken Burns on a 2x oversampled frame, so the pan moves
in sub-pixel steps). Level 2 chains the clips with xfade, burns the ASS subtitles and muxes the chapter
VO. Every path handed to ffmpeg is relative to the project root, so the same command works on Windows.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont

from ..config import chapter_number
from ..manifest import canonical_json, fingerprint, run_stage, sha256_file, short_hash
from ..media import ffmpeg as ff
from ..media.pan import ClipSpec, allocate_durations, clip_filter, frames_for, scaled_height, xfade_offsets
from ..media.subtitles import build_ass
from ..models import Panel, Timeline
from ..project import PACKAGE_FONTS, SUBTITLE_FONT_NAME, TITLE_FONT_FILE
from .segment import load_panels
from .tts import load_timeline

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "04_render"
CODE_VERSION = "render-code-v1"
CARD_ID = "__card__"


# ------------------------------------------------------------------ title card


def make_title_card(path: Path, series: str, chapter_num: int, w: int, h: int) -> None:
    im = Image.new("RGB", (w, h), (15, 15, 20))
    d = ImageDraw.Draw(im)
    font_path = str(PACKAGE_FONTS / TITLE_FONT_FILE)
    big = ImageFont.truetype(font_path, int(h * 0.12))
    small = ImageFont.truetype(font_path, int(h * 0.045))
    title = f"Chapter {chapter_num}"
    tb = d.textbbox((0, 0), title, font=big)
    sb = d.textbbox((0, 0), series, font=small)
    d.text(((w - (sb[2] - sb[0])) / 2, h * 0.40 - (sb[3] - sb[1]) - h * 0.02), series, font=small, fill=(170, 170, 185))
    d.text(((w - (tb[2] - tb[0])) / 2, h * 0.42), title, font=big, fill=(240, 240, 245))
    d.rectangle([w * 0.45, h * 0.40 - h * 0.005, w * 0.55, h * 0.40 + h * 0.005], fill=(90, 90, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG")


# ------------------------------------------------------------------ clip planning


def plan_clips(ctx: Context, ch: str, tl: Timeline, panels: dict[str, Panel], card: Path | None) -> tuple[list[ClipSpec], list[str]]:
    """Turn the timeline into an ordered list of clip specs whose frames sum to the chapter's frames."""
    cfg = ctx.config
    fps = cfg.render.fps
    os_ = cfg.render.oversample
    ow, oh = cfg.render.width * os_, cfg.render.height * os_
    fade_frames = int(round(cfg.style.crossfade_s * fps))
    warnings: list[str] = []

    boundaries: list[tuple[float, float, list[str]]] = []
    if tl.lead_in > 0 and card is not None:
        boundaries.append((0.0, tl.lead_in, [CARD_ID]))
    for i, line in enumerate(tl.lines):
        start = tl.lead_in if i == 0 else line.start
        end = tl.lines[i + 1].start if i + 1 < len(tl.lines) else tl.duration
        if end <= start:
            continue
        boundaries.append((start, end, list(line.panel_ids)))
    if boundaries and boundaries[0][0] > 0:
        boundaries.insert(0, (0.0, boundaries[0][0], boundaries[0][2][:1]))

    specs: list[ClipSpec] = []
    total_frames = int(round(tl.duration * fps))
    for start, end, ids in boundaries:
        f0, f1 = int(round(start * fps)), int(round(end * fps))
        if f1 <= f0:
            continue
        span_frames = f1 - f0
        if ids == [CARD_ID]:
            specs.append(ClipSpec(CARD_ID, ctx.project.rel(card), span_frames, 0, "card", cfg.render.width, cfg.render.height))  # type: ignore[arg-type]
            continue
        known = [pid for pid in ids if pid in panels]
        if not known:
            warnings.append(f"no known panels for a line starting at {start:.2f}s; holding the previous clip")
            if specs:
                specs[-1].frames += span_frames
            continue
        heights = [scaled_height(panels[p].w, panels[p].h, ow) for p in known]
        durations = allocate_durations(
            span_frames / fps, heights, oh, cfg.style.min_panel_s, cfg.style.pan.max_px_s * os_
        )
        if len(durations) < len(known):
            warnings.append(f"line at {start:.2f}s has {len(known)} panels for {span_frames / fps:.1f}s; showing {len(durations)}")
        frames = frames_for(durations, fps, span_frames)
        for pid, fr, sh in zip(known, frames, heights, strict=False):
            p = panels[pid]
            if cfg.style.framing == "blur":
                mode = "blur" if sh > oh else "fit"
            else:
                mode = "pan" if sh > oh else "fit"
            specs.append(ClipSpec(pid, ctx.project.rel(ctx.project.panels_dir(ch) / p.file), fr, 0, mode, p.w, p.h))
    got = sum(s.frames for s in specs)
    if got != total_frames and specs:
        specs[-1].frames += total_frames - got
    for s in specs[:-1]:
        s.pad = fade_frames
    return specs, warnings


def split_parts(specs: list[ClipSpec], max_per_graph: int) -> list[list[ClipSpec]]:
    if len(specs) <= max_per_graph:
        return [specs]
    parts: list[list[ClipSpec]] = [specs[i : i + max_per_graph] for i in range(0, len(specs), max_per_graph)]
    for part in parts:
        part[-1].pad = 0  # hard cut between parts
    return parts


# ------------------------------------------------------------------ level 1


def clip_params(cfg: Any) -> dict[str, Any]:
    return {
        "fps": cfg.render.fps,
        "w": cfg.render.width,
        "h": cfg.render.height,
        "oversample": cfg.render.oversample,
        "hold_in": cfg.style.pan.hold_in_s,
        "hold_out": cfg.style.pan.hold_out_s,
        "zoom": cfg.style.pan.zoom_short_panels,
        "crf": cfg.render.clip_crf,
        "preset": cfg.render.clip_preset,
    }


def clip_key(spec: ClipSpec, src_sha: str, params: dict[str, Any], encoder: str) -> str:
    return short_hash(canonical_json({"spec": spec.__dict__, "src": src_sha, "params": params, "enc": encoder}), 12)


def render_clip(ctx: Context, spec: ClipSpec, out_rel: str, encoder: str) -> None:
    cfg = ctx.config
    graph = clip_filter(
        spec,
        fps=cfg.render.fps,
        out_w=cfg.render.width,
        out_h=cfg.render.height,
        oversample=cfg.render.oversample,
        hold_in=cfg.style.pan.hold_in_s,
        hold_out=cfg.style.pan.hold_out_s,
        zoom=cfg.style.pan.zoom_short_panels,
    )
    args = [
        *ff.ffmpeg_cmd(),
        "-framerate", str(cfg.render.fps), "-i", spec.src,
        "-filter_complex", graph, "-map", "[v]",
        "-r", str(cfg.render.fps), "-frames:v", str(spec.total_frames),
        *ff.encoder_args(encoder, cfg.render.clip_crf, cfg.render.clip_preset),
        "-pix_fmt", "yuv420p", "-g", str(cfg.render.fps), "-an", "-movflags", "+faststart",
        out_rel,
    ]
    ff.run(args, cwd=ctx.project.root)


# ------------------------------------------------------------------ level 2


def part_filter_script(clips_rel: list[str], specs: list[ClipSpec], fps: int, fade_s: float, ass_rel: str | None, fonts_rel: str | None) -> str:
    lines = []
    for i in range(len(clips_rel)):
        lines.append(f"[{i}:v]settb=AVTB,fps={fps},format=yuv420p[c{i}];")
    last = "c0"
    offsets = xfade_offsets([s.frames / fps for s in specs])
    for i in range(1, len(clips_rel)):
        out = f"x{i}"
        lines.append(f"[{last}][c{i}]xfade=transition=fade:duration={fade_s:.4f}:offset={offsets[i - 1]:.4f}[{out}];")
        last = out
    if ass_rel:
        lines.append(f"[{last}]subtitles=filename={ass_rel}:fontsdir={fonts_rel}[v]")
    else:
        lines.append(f"[{last}]copy[v]")
    return "\n".join(lines) + "\n"


def render_chapter(ctx: Context, ch: str) -> dict[str, Any]:
    project, cfg = ctx.project, ctx.config
    tl = load_timeline(project.timeline_json(ch))
    panels = load_panels(project.panels_json(ch)).by_id()
    fonts_dir = project.ensure_fonts()
    rdir = project.render_dir(ch)
    (rdir / "clips").mkdir(parents=True, exist_ok=True)
    encoder = ff.pick_encoder(cfg.render.encoder)
    fps = cfg.render.fps

    card = None
    if tl.lead_in > 0:
        card = rdir / f"{ch}.card.png"
        make_title_card(card, cfg.series, chapter_number(ch), cfg.render.width, cfg.render.height)

    specs, warnings = plan_clips(ctx, ch, tl, panels, card)
    if not specs:
        raise ValueError(f"{ch}: nothing to render (empty timeline?)")
    parts = split_parts(specs, cfg.render.max_clips_per_graph)
    params = clip_params(cfg)

    # level 1: cached clips
    jobs: list[tuple[ClipSpec, str]] = []
    part_clips: list[list[str]] = []
    wanted: set[str] = set()
    for part in parts:
        rels = []
        for spec in part:
            src_sha = panels[spec.panel_id].sha256 if spec.panel_id in panels else sha256_file(project.abs(spec.src))
            out = rdir / "clips" / f"{spec.panel_id}-{clip_key(spec, src_sha, params, encoder)}.mp4"
            rel = project.rel(out)
            rels.append(rel)
            wanted.add(out.name)
            if not out.exists():
                jobs.append((spec, rel))
        part_clips.append(rels)
    with ThreadPoolExecutor(max_workers=max(1, cfg.render.workers)) as pool:
        list(pool.map(lambda j: render_clip(ctx, j[0], j[1], encoder), jobs))
    for old in (rdir / "clips").glob("*.mp4"):
        if old.name not in wanted:
            old.unlink()

    # subtitles
    ass_rel = None
    if cfg.style.subtitles.burn and tl.lines:
        ass_path = project.chapter_ass(ch)
        ass_path.write_text(
            build_ass(tl, cfg.style.subtitles, SUBTITLE_FONT_NAME, cfg.render.width, cfg.render.height), encoding="utf-8"
        )
        ass_rel = project.rel(ass_path)
    fonts_rel = project.rel(fonts_dir)

    # level 2
    fade_s = specs[0].pad / fps if len(specs) > 1 else 0.0
    vo_rel = project.rel(project.vo_wav(ch))
    out_rel = project.rel(project.chapter_mp4(ch))
    enc_args = ff.encoder_args(encoder, cfg.render.crf, cfg.render.preset)
    audio_args = ["-c:a", "aac", "-b:a", cfg.render.audio_bitrate, "-ar", "48000", "-ac", "2"]
    common_video = ["-r", str(fps), "-pix_fmt", "yuv420p", "-g", str(fps * 2), "-movflags", "+faststart"]

    if len(parts) == 1:
        script = rdir / f"{ch}.filter"
        script.write_text(part_filter_script(part_clips[0], parts[0], fps, fade_s, ass_rel, fonts_rel), encoding="utf-8")
        args = [*ff.ffmpeg_cmd()]
        for rel in part_clips[0]:
            args += ["-i", rel]
        args += ["-i", vo_rel, "-filter_complex_script", project.rel(script), "-map", "[v]", "-map", f"{len(part_clips[0])}:a"]
        args += [*enc_args, *common_video, *audio_args, "-t", f"{tl.duration:.3f}", out_rel]
        ff.run(args, cwd=project.root)
    else:
        part_files: list[str] = []
        for pi, (part, rels) in enumerate(zip(parts, part_clips, strict=True), start=1):
            script = rdir / f"{ch}.part{pi:02d}.filter"
            script.write_text(part_filter_script(rels, part, fps, fade_s, None, None), encoding="utf-8")
            part_out = project.rel(rdir / f"{ch}.part{pi:02d}.mp4")
            args = [*ff.ffmpeg_cmd()]
            for rel in rels:
                args += ["-i", rel]
            args += ["-filter_complex_script", project.rel(script), "-map", "[v]", *enc_args, *common_video, "-an", part_out]
            ff.run(args, cwd=project.root)
            part_files.append(part_out)
        concat_list = rdir / f"{ch}.parts.txt"
        concat_list.write_text("".join(f"file '{Path(p).name}'\n" for p in part_files), encoding="utf-8")
        joined = project.rel(rdir / f"{ch}.video.mp4")
        ff.run([*ff.ffmpeg_cmd(), "-f", "concat", "-safe", "0", "-i", project.rel(concat_list), "-c", "copy", joined], cwd=project.root)
        args = [*ff.ffmpeg_cmd(), "-i", joined, "-i", vo_rel]
        if ass_rel:
            args += ["-vf", f"subtitles=filename={ass_rel}:fontsdir={fonts_rel}", *enc_args, *common_video]
        else:
            args += ["-c:v", "copy"]
        args += ["-map", "0:v", "-map", "1:a", *audio_args, "-t", f"{tl.duration:.3f}", out_rel]
        ff.run(args, cwd=project.root)

    got = ff.duration(project.chapter_mp4(ch))
    if abs(got - tl.duration) > 1.5 / fps:
        warnings.append(f"rendered duration {got:.3f}s differs from timeline {tl.duration:.3f}s")
    (rdir / f"{ch}.plan.json").write_text(
        json.dumps([s.__dict__ for s in specs], indent=1), encoding="utf-8"
    )
    for w in warnings:
        ctx.log(f"  [yellow]{ch}: {w}[/]")
    return {"clips": len(specs), "parts": len(parts), "encoder": encoder, "duration": round(got, 3)}


def run_chapter(ctx: Context, ch: str) -> str:
    project, cfg = ctx.project, ctx.config
    tl_path = project.timeline_json(ch)
    if not tl_path.exists():
        raise FileNotFoundError(f"{ch}: run stage 3 first ({tl_path} missing)")
    used = {pid for line in load_timeline(tl_path).lines for pid in line.panel_ids}
    panels = load_panels(project.panels_json(ch)).by_id()
    fp = fingerprint(
        stage=STAGE,
        version=CODE_VERSION,
        config=cfg.subset_for(STAGE),
        timeline=sha256_file(tl_path),
        vo=sha256_file(project.vo_wav(ch)),
        panels=sorted((pid, panels[pid].sha256) for pid in used if pid in panels),
    )
    out = project.chapter_mp4(ch)
    return run_stage(ctx.manifest, ch, STAGE, fp, [out], lambda: render_chapter(ctx, ch), force=ctx.force)
