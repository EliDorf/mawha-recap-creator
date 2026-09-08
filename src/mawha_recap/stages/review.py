"""review.html (the 5-minute human skim) and the approval gate."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from ..config import chapter_number
from ..manifest import canonical_json, sha256_text
from ..models import Panel
from .beats import load_beats
from .script import load_script_for_video

if TYPE_CHECKING:
    from ..pipeline import Context

THUMB_W = 160


def script_hash(path: Path) -> str:
    """Hash of the script's *content* (ids, panels, text), independent of YAML formatting."""
    script = load_script_for_video(path)
    canon = [
        {
            "id": line.id,
            "beat": line.beat,
            "panel_ids": line.panel_ids,
            "text": line.text,
            "tts_text": line.tts_text,
            "pause_after": line.pause_after,
            **({"framing": line.framing} if line.framing is not None else {}),
            **({"visual_crop": line.visual_crop} if line.visual_crop is not None else {}),
        }
        for line in script.lines
    ]
    return sha256_text(canonical_json(canon))


def thumb_for(ctx: Context, ch: str, panel: Panel, crop: tuple[int, int, int, int] | None = None) -> Path:
    thumbs = ctx.project.review_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    crop_key = "-" + "_".join(map(str, crop)) if crop else ""
    out = thumbs / f"{panel.id}-{panel.sha256[-8:]}{crop_key}.jpg"
    if not out.exists():
        with Image.open(ctx.project.panels_dir(ch) / panel.file) as im:
            im = im.convert("RGB")
            if crop:
                x, y, w, h = crop
                im = im.crop((x, y, x + w, y + h))
            h = max(1, round(im.height * THUMB_W / im.width))
            im.resize((THUMB_W, h), Image.LANCZOS).save(out, quality=80)
    return out


CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f5f7;color:#1c1c1e}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 20px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
header b{font-size:18px} nav a{margin-right:10px;text-decoration:none;color:#0a58ca}
section{margin:24px 20px} h2{margin:0 0 6px} .meta{color:#555;font-size:14px;margin-bottom:10px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #ddd}
td{vertical-align:top;padding:8px;border-top:1px solid #eee}
td.id{width:90px;color:#888;font-size:12px;font-family:ui-monospace,monospace}
td.thumbs{width:360px} td.thumbs img{height:110px;margin:0 4px 4px 0;border:1px solid #ccc;background:#fff}
td.text{font-size:16px;line-height:1.45} .tts{color:#777;font-size:13px;margin-top:4px}
.flag{background:#fff3cd;color:#664d03;padding:1px 6px;border-radius:4px;font-size:12px;margin-left:6px}
.warn{background:#f8d7da;color:#58151c;padding:1px 6px;border-radius:4px;font-size:12px;margin-left:6px}
.ok{background:#d1e7dd;color:#0a3622;padding:1px 6px;border-radius:4px;font-size:12px;margin-left:6px}
"""


def render_review(ctx: Context, chapters: list[str]) -> Path:
    project, cfg, m = ctx.project, ctx.config, ctx.manifest
    project.review_dir.mkdir(parents=True, exist_ok=True)
    total_words = 0
    sections: list[str] = []
    nav: list[str] = []
    for ch in chapters:
        sp = project.script_yaml(ch)
        n = chapter_number(ch)
        nav.append(f'<a href="#{ch}">Ch {n}</a>')
        if not sp.exists():
            sections.append(f'<section id="{ch}"><h2>Chapter {n}</h2><div class="meta">no script yet</div></section>')
            continue
        script = load_script_for_video(sp)
        panels = project.referenced_panels([pid for line in script.lines for pid in line.panel_ids])
        beats = load_beats(project.beats_json(ch)) if project.beats_json(ch).exists() else None
        beat_text = {b.id: b.what_happens for b in beats.beats} if beats else {}
        words = script.word_count()
        total_words += words
        approved = m.approval(ch)
        state = (
            '<span class="ok">approved</span>'
            if approved and approved == script_hash(sp)
            else ('<span class="warn">changed since approval</span>' if approved else '<span class="flag">not approved</span>')
        )
        flags = "".join(f'<span class="flag">{html.escape(f)}</span>' for f in m.stage(ch, "01_panels").get("flags", []))
        stale = '<span class="warn">context stale</span>' if m.stage(ch, "02_script").get("stale") or m.stage(ch, "02_beats").get("stale") else ""
        rows = []
        for line in script.lines:
            thumbs = []
            for pid in line.panel_ids:
                p = panels.get(pid)
                if p is None:
                    thumbs.append(f'<span class="warn">{html.escape(pid)}?</span>')
                    continue
                source_ch, _ = project.panel_source(pid)
                t = thumb_for(ctx, source_ch, p, line.visual_crop)
                full = (project.panels_dir(source_ch) / p.file).resolve().as_uri()
                thumbs.append(f'<a href="{full}"><img src="thumbs/{t.name}" title="{pid}"></a>')
            tts = f'<div class="tts">voice: {html.escape(line.tts_text)}</div>' if line.tts_text else ""
            if line.visual_crop:
                tts += f'<div class="tts">Shot: {line.framing}; crop (x, y, w, h): {line.visual_crop}</div>'
            beat = html.escape(beat_text.get(line.beat, ""))
            rows.append(
                f'<tr><td class="id">{line.id}<br><span title="{beat}">{html.escape(line.beat)}</span></td>'
                f'<td class="thumbs">{"".join(thumbs)}</td><td class="text">{html.escape(line.text)}{tts}</td></tr>'
            )
        rel = sp.relative_to(project.root).as_posix()
        sections.append(
            f'<section id="{ch}"><h2>Chapter {n} {state}{stale}{flags}</h2>'
            f'<div class="meta">{words} words (budget {script.budget_words}) · {len(script.lines)} lines · edit <code>{rel}</code></div>'
            f'<table>{"".join(rows)}</table></section>'
        )
    est_min = total_words / max(1, cfg.style.wpm)
    budget = m.budget.get("total_words", "?")
    head = (
        f"<header><b>{html.escape(cfg.series)}</b> <span>{html.escape(cfg.slug)} · {cfg.variant}</span>"
        f"<span>{total_words} words (budget {budget}) ≈ {est_min:.0f} min at {cfg.style.wpm} wpm</span>"
        f"<nav>{' '.join(nav)}</nav></header>"
    )
    page = f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(cfg.slug)} script review</title><style>{CSS}</style></head><body>{head}{''.join(sections)}</body></html>"
    project.review_html.write_text(page, encoding="utf-8")
    return project.review_html


def approve_chapters(ctx: Context, chapters: list[str]) -> list[str]:
    approved: list[str] = []
    for ch in chapters:
        sp = ctx.project.script_yaml(ch)
        if not sp.exists():
            ctx.log(f"  [yellow]{ch}: no script to approve[/]")
            continue
        script = load_script_for_video(sp)
        if not script.lines:
            ctx.log(f"  [red]{ch}: script has no lines[/]")
            continue
        panels = ctx.project.referenced_panels([pid for line in script.lines for pid in line.panel_ids])
        for line in script.lines:
            if line.visual_crop:
                x, y, w, h = line.visual_crop
                for pid in line.panel_ids:
                    panel = panels[pid]
                    if min(x, y) < 0 or min(w, h) <= 0 or x + w > panel.w or y + h > panel.h:
                        raise ValueError(f"{line.id}: crop outside source image {pid}; fix before approving")
        ctx.manifest.set_approval(ch, script_hash(sp))
        approved.append(ch)
        ctx.log(f"  [green]{ch}: approved[/] ({script.word_count()} words, {len(script.lines)} lines)")
    ctx.manifest.save()
    return approved
