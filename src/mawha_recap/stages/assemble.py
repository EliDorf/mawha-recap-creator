"""Stage 5: concatenate chapters, mix the (optional) music bed, and emit the upload package."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import chapter_number
from ..manifest import fingerprint, run_stage, sha256_file, sha256_text
from ..media import ffmpeg as ff
from ..media.subtitles import build_srt
from ..models import MetaOut, Panel, Timeline
from ..project import PACKAGE_FONTS, TITLE_FONT_FILE
from ..prompts import load_prompt, render
from ..providers.claude import cache_block, usage_usd
from .beats import VARIANT_LABEL, load_beats
from .segment import load_panels
from .tts import load_timeline

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "05_assemble"
CODE_VERSION = "assemble-code-v1"
PROMPT_VERSION = "meta-v1"
THUMB_W, THUMB_H = 1280, 720


# ------------------------------------------------------------------ helpers


def fmt_marker(seconds: float, long: bool) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if long else f"{m:02d}:{sec:02d}"


def chapters_txt(entries: list[tuple[float, str]]) -> str:
    """YouTube chapter markers: first at 00:00, every marker at least 10 s apart."""
    long = entries[-1][0] >= 3600 if entries else False
    out: list[str] = []
    last = -10.0
    for i, (offset, title) in enumerate(entries):
        t = 0.0 if i == 0 else offset
        if i and t - last < 10:
            continue
        out.append(f"{fmt_marker(t, long)} {title}")
        last = t
    return "\n".join(out) + "\n"


def laplacian_score(img: Image.Image) -> float:
    g = img.convert("L")
    if g.width > 400:
        g = g.resize((400, max(1, round(g.height * 400 / g.width))))
    arr = np.asarray(g, dtype=np.uint8)
    return float(cv2.Laplacian(arr, cv2.CV_64F).var())


def pick_thumbnail_panel(ctx: Context, chapters: list[str]) -> tuple[str, Panel] | None:
    best: tuple[float, str, Panel] | None = None
    for ch in chapters:
        pj = ctx.project.panels_json(ch)
        if not pj.exists():
            continue
        skip: set[str] = set()
        bj = ctx.project.beats_json(ch)
        if bj.exists():
            skip = set(load_beats(bj).skip_panels)
        for p in load_panels(pj).panels:
            if p.id in skip or "skip" in p.flags or p.h < 200:
                continue
            with Image.open(ctx.project.panels_dir(ch) / p.file) as im:
                score = laplacian_score(im)
            if best is None or score > best[0]:
                best = (score, ch, p)
    return (best[1], best[2]) if best else None


def make_thumbnail(ctx: Context, ch: str, panel: Panel, out: Path, lockup: str) -> None:
    cfg = ctx.config.package.thumbnail
    with Image.open(ctx.project.panels_dir(ch) / panel.file) as im:
        im = im.convert("RGB")
        scale = THUMB_W / im.width
        im = im.resize((THUMB_W, max(THUMB_H, round(im.height * scale))), Image.LANCZOS)
        # pick the 16:9 window with the most detail
        best_y, best_score = 0, -1.0
        step = max(20, (im.height - THUMB_H) // 12) if im.height > THUMB_H else 1
        for y in range(0, max(1, im.height - THUMB_H + 1), step):
            crop = im.crop((0, y, THUMB_W, y + THUMB_H))
            s = laplacian_score(crop)
            if s > best_score:
                best_y, best_score = y, s
        thumb = im.crop((0, best_y, THUMB_W, best_y + THUMB_H))
    d = ImageDraw.Draw(thumb)
    font = ImageFont.truetype(str(PACKAGE_FONTS / TITLE_FONT_FILE), cfg.font_size)
    x, y = 48, THUMB_H - 48
    for line in reversed(lockup.split("\n")):
        bbox = d.textbbox((0, 0), line, font=font)
        h = bbox[3] - bbox[1]
        y -= h + 12
        d.text((x, y), line, font=font, fill=cfg.fill, stroke_width=cfg.stroke_width, stroke_fill=cfg.stroke)
    out.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(out, format="JPEG", quality=90)


def video_meta(ctx: Context, chapters: list[str]) -> tuple[MetaOut, dict[str, Any]]:
    cfg = ctx.config
    summaries = []
    for ch in chapters:
        bj = ctx.project.beats_json(ch)
        if bj.exists():
            summaries.append(f"Chapter {chapter_number(ch)}: {load_beats(bj).chapter_summary}")
    system = [cache_block(render(load_prompt("meta"), series=cfg.series, variant_label=VARIANT_LABEL[cfg.variant]))]
    user = "Chapter summaries:\n" + "\n".join(summaries) if summaries else "No summaries available; write generic but honest metadata."
    out, usage = ctx.llm.parse(model=cfg.llm.meta_model, system=system, user_content=user, schema=MetaOut, max_tokens=2000)
    return out, usage


def mix_music(ctx: Context, video_in: str, out_rel: str) -> bool:
    """Loop the first configured track under the narration. Returns False when no track is configured."""
    cfg = ctx.config
    tracks = [t for t in cfg.music.tracks if Path(t).exists() or (ctx.project.root / t).exists()]
    if not tracks:
        return False
    track = tracks[0]
    track_rel = ctx.project.rel(ctx.project.root / track) if (ctx.project.root / track).exists() else track
    graph = (
        f"[1:a]volume={cfg.music.gain_db}dB,aformat=channel_layouts=stereo:sample_rates=48000[m];"
        "[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )
    ff.run(
        [*ff.ffmpeg_cmd(), "-i", video_in, "-stream_loop", "-1", "-i", track_rel, "-filter_complex", graph,
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", cfg.render.audio_bitrate, "-shortest", out_rel],
        cwd=ctx.project.root,
    )
    return True


# ------------------------------------------------------------------ stage


def run(ctx: Context, chapters: list[str]) -> str:
    project, cfg = ctx.project, ctx.config
    missing = [ch for ch in chapters if not project.chapter_mp4(ch).exists() or not project.timeline_json(ch).exists()]
    if missing:
        raise FileNotFoundError(f"run stage 4 first for: {', '.join(missing)}")
    summaries = [load_beats(project.beats_json(ch)).chapter_summary if project.beats_json(ch).exists() else "" for ch in chapters]
    fp = fingerprint(
        stage=STAGE,
        version=[CODE_VERSION, PROMPT_VERSION],
        config=cfg.subset_for(STAGE),
        chapters=[(ch, sha256_file(project.chapter_mp4(ch)), sha256_file(project.timeline_json(ch))) for ch in chapters],
        summaries=sha256_text("\n".join(summaries)),
        music=[sha256_file(project.root / t) if (project.root / t).exists() else t for t in cfg.music.tracks],
    )
    outputs = [project.final_mp4, project.final_srt, project.chapters_txt, project.description_md, project.metadata_json, project.thumbnail_jpg]

    def fn() -> dict[str, Any]:
        project.out.mkdir(parents=True, exist_ok=True)
        timelines: list[tuple[float, Timeline]] = []
        offsets: list[tuple[str, float, float]] = []
        t = 0.0
        for ch in chapters:
            tl = load_timeline(project.timeline_json(ch))
            timelines.append((t, tl))
            offsets.append((ch, t, tl.duration))
            t += tl.duration
        total = t

        # 1. concat (stream copy)
        concat_list = project.out / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{Path('..') / project.rel(project.chapter_mp4(ch))}'\n".replace("\\", "/") for ch in chapters),
            encoding="utf-8",
        )
        joined_rel = project.rel(project.out / f"{cfg.slug}.video.mp4")
        ff.run([*ff.ffmpeg_cmd(), "-f", "concat", "-safe", "0", "-i", project.rel(concat_list), "-c", "copy", "-movflags", "+faststart", joined_rel], cwd=project.root)
        final_rel = project.rel(project.final_mp4)
        if mix_music(ctx, joined_rel, final_rel):
            (project.root / joined_rel).unlink(missing_ok=True)
        else:
            (project.root / joined_rel).replace(project.final_mp4)
        concat_list.unlink(missing_ok=True)

        # 2. subtitles + chapter markers
        project.final_srt.write_text(build_srt(timelines, cfg.style.subtitles), encoding="utf-8")
        titles = [(off, cfg.package.chapter_title_template.format(n=chapter_number(ch), ch=ch)) for ch, off, _d in offsets]
        project.chapters_txt.write_text(chapters_txt(titles), encoding="utf-8")

        # 3. metadata
        meta, usage = video_meta(ctx, chapters)
        usd = usage_usd(usage, cfg.llm.prices)
        if usage.get("input_tokens"):
            ctx.manifest.add_cost(stage=STAGE, chapter="video", usd=usd, **{k: v for k, v in usage.items() if k != "request_id"})
        first, last = chapter_number(chapters[0]), chapter_number(chapters[-1])
        fmt = {
            "series": cfg.series,
            "first": first,
            "last": last,
            "variant": cfg.variant,
            "variant_label": VARIANT_LABEL[cfg.variant],
            "suffix": meta.title_suffix,
            "hook": meta.hook,
            "chapters": chapters_txt(titles).strip(),
        }
        title = cfg.package.title_template.format(**fmt)
        description = cfg.package.description_template.format(**fmt)
        project.description_md.write_text(f"# {title}\n\n{description}\n", encoding="utf-8")
        metadata = {
            "title": title,
            "description": description,
            "tags": cfg.package.tags,
            "hook": meta.hook,
            "title_suffix": meta.title_suffix,
            "series": cfg.series,
            "variant": cfg.variant,
            "chapters": [{"id": ch, "number": chapter_number(ch), "offset_s": round(off, 3), "duration_s": round(d, 3)} for ch, off, d in offsets],
            "duration_s": round(total, 3),
            "files": {
                "video": project.final_mp4.name,
                "subtitles": project.final_srt.name,
                "chapters": project.chapters_txt.name,
                "thumbnail": project.thumbnail_jpg.name,
            },
            "loudness_lufs": cfg.style.loudness_lufs,
        }
        project.metadata_json.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        # 4. thumbnail
        pick = pick_thumbnail_panel(ctx, chapters)
        if pick is None:
            raise ValueError("no panel available for the thumbnail")
        make_thumbnail(ctx, pick[0], pick[1], project.thumbnail_jpg, cfg.package.thumbnail.lockup.format(**fmt))

        got = ff.duration(project.final_mp4)
        if abs(got - total) > 0.5:
            ctx.log(f"  [yellow]final video is {got:.2f}s, timelines sum to {total:.2f}s[/]")
        ctx.log(f"  [green]package:[/] {project.out}  ({got / 60:.1f} min, title: {title})")
        return {"duration": round(got, 3), "chapters": len(chapters), "thumbnail_panel": pick[1].id, "usd": usd}

    return run_stage(ctx.manifest, None, STAGE, fp, outputs, fn, force=ctx.force)
