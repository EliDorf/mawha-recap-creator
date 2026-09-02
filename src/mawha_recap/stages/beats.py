"""Stage 2a: vision beat sheet per chapter (Claude over the chapter's panels)."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from ..config import chapter_number
from ..manifest import fingerprint, run_stage, sha256_text
from ..models import Beat, BeatSheet, BeatSheetOut, Panel, PanelsDoc
from ..prompts import load_prompt, render
from ..providers.claude import cache_block, text_block, usage_usd
from .segment import load_panels

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "02_beats"
PROMPT_VERSION = "beats-v1"
CODE_VERSION = "beats-code-v1"
VARIANT_LABEL = {"recap": "recap", "sleep": "sleep recap"}
FULL_SUMMARIES = 12  # older chapter summaries are compressed to their first sentences


def encode_panel(path: Path, max_w: int, max_h: int, quality: int) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = min(1.0, max_w / im.width, max_h / im.height)
        if scale < 1.0:
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), im.size


def image_block(b64: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def story_panels(doc: PanelsDoc) -> list[Panel]:
    return [p for p in doc.panels if "skip" not in p.flags]


def load_beats(path: Path) -> BeatSheet:
    return BeatSheet.model_validate_json(path.read_text(encoding="utf-8"))


def compress_summary(text: str, sentences: int = 2) -> str:
    parts = [s.strip() for s in text.replace("? ", "?|").replace("! ", "!|").replace(". ", ".|").split("|") if s.strip()]
    return " ".join(parts[:sentences])


def story_so_far(ctx: Context, ch: str, all_chapters: list[str]) -> tuple[str, str]:
    """Story-so-far text for `ch` plus a hash of everything it was built from."""
    parts: list[str] = []
    hashed: list[str] = []
    carry = ctx.series.story_so_far.strip()
    if carry:
        parts.append(carry)
        hashed.append(carry)
    prev = [c for c in all_chapters if chapter_number(c) < chapter_number(ch)]
    summaries: list[tuple[str, str, list[str]]] = []
    for c in prev:
        p = ctx.project.beats_json(c)
        if p.exists():
            bs = load_beats(p)
            summaries.append((c, bs.chapter_summary, bs.open_threads))
    for i, (c, summary, _threads) in enumerate(summaries):
        full = i >= len(summaries) - FULL_SUMMARIES
        parts.append(f"Chapter {chapter_number(c)}: {summary if full else compress_summary(summary)}")
        hashed.append(f"{c}:{summary}")
    if summaries and summaries[-1][2]:
        parts.append("Open threads: " + "; ".join(summaries[-1][2]))
    text = "\n".join(parts) if parts else "(no earlier chapters)"
    return text, sha256_text("\n".join(hashed))


def merge_outs(outs: list[BeatSheetOut]) -> BeatSheetOut:
    if len(outs) == 1:
        return outs[0]
    seen: set[str] = set()
    chars = []
    for o in outs:
        for c in o.new_characters:
            if c.name not in seen:
                seen.add(c.name)
                chars.append(c)
    return BeatSheetOut(
        beats=[b for o in outs for b in o.beats],
        chapter_summary=" ".join(o.chapter_summary.strip() for o in outs),
        new_characters=chars,
        open_threads=outs[-1].open_threads,
        skip_panels=[p for o in outs for p in o.skip_panels],
    )


def normalize_beats(out: BeatSheetOut, panel_ids: list[str]) -> tuple[list[Beat], list[str], list[str]]:
    """Validate the model's beats against the real panel ids; every panel ends up in exactly one beat or skipped."""
    order = {pid: i for i, pid in enumerate(panel_ids)}
    warnings: list[str] = []
    skip = [p for p in dict.fromkeys(out.skip_panels) if p in order]
    seen: set[str] = set(skip)
    beats: list[Beat] = []
    for b in out.beats:
        ids: list[str] = []
        for pid in b.panel_ids:
            if pid not in order:
                warnings.append(f"unknown panel id {pid!r} dropped")
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
        ids.sort(key=order.__getitem__)
        if not ids:
            continue
        beats.append(
            Beat(
                id="",
                panel_ids=ids,
                what_happens=b.what_happens.strip(),
                dialogue_gist=b.dialogue_gist.strip(),
                emotional_beat=b.emotional_beat.strip(),
                importance=max(1, min(3, int(b.importance))),
            )
        )
    uncovered = [pid for pid in panel_ids if pid not in seen]
    if uncovered:
        warnings.append(f"{len(uncovered)} panel(s) were in no beat; added as visual-only beats")
        groups: list[list[str]] = []
        for pid in uncovered:
            if groups and order[pid] == order[groups[-1][-1]] + 1:
                groups[-1].append(pid)
            else:
                groups.append([pid])
        for g in groups:
            beats.append(
                Beat(
                    id="",
                    panel_ids=g,
                    what_happens="Visual-only panels with no narrated beat (transition or establishing art).",
                    importance=1,
                )
            )
    beats.sort(key=lambda b: order[b.panel_ids[0]])
    for i, b in enumerate(beats, start=1):
        b.id = f"b{i:02d}"
    return beats, skip, warnings


def sum_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    total: dict[str, Any] = {k: sum(int(u.get(k, 0) or 0) for u in usages) for k in keys}
    total["model"] = usages[-1].get("model") if usages else None
    total["request_ids"] = [u.get("request_id") for u in usages if u.get("request_id")]
    return total


def update_stale(ctx: Context, ch: str, stage: str, context_hash: str) -> bool:
    """Soft dependency: upstream story context changed but this stage was not re-run."""
    rec = ctx.manifest.stage(ch, stage)
    stale = (not ctx.pin_context) and rec.get("context_hash") not in (None, context_hash)
    if stale:
        rec["stale"] = True
    else:
        rec.pop("stale", None)
    ctx.manifest.save()
    return stale


def run_chapter(ctx: Context, ch: str, all_chapters: list[str]) -> str:
    project, cfg = ctx.project, ctx.config
    pj = project.panels_json(ch)
    if not pj.exists():
        raise FileNotFoundError(f"{ch}: run stage 1 first ({pj} missing)")
    doc = load_panels(pj)
    panels = story_panels(doc)
    if not panels:
        raise ValueError(f"{ch}: no story panels (all skipped?)")
    story, context_hash = story_so_far(ctx, ch, all_chapters)
    glossary = ctx.series.glossary_text() or "(none yet)"
    fp = fingerprint(
        stage=STAGE,
        version=[PROMPT_VERSION, CODE_VERSION],
        config=cfg.subset_for(STAGE),
        panels=[(p.id, p.sha256) for p in panels],
        glossary=sha256_text(glossary),
    )
    out_path = project.beats_json(ch)
    rec = ctx.manifest.stage(ch, STAGE)
    force = ctx.force or bool(ctx.refresh_stale and rec.get("stale"))

    def fn() -> dict[str, Any]:
        system = [
            cache_block(
                render(load_prompt("beats"), series=cfg.series, source_language=cfg.source_language, glossary=glossary)
            )
        ]
        n = cfg.vision.max_panels_per_request
        chunks = [panels[i : i + n] for i in range(0, len(panels), n)]
        outs: list[BeatSheetOut] = []
        usages: list[dict[str, Any]] = []
        for ci, chunk in enumerate(chunks):
            content: list[dict[str, Any]] = []
            for p in chunk:
                b64, size = encode_panel(
                    project.panels_dir(ch) / p.file, cfg.vision.max_w, cfg.vision.max_h, cfg.vision.jpeg_quality
                )
                content.append(text_block(f"panel {p.id} ({size[0]}x{size[1]})"))
                content.append(image_block(b64))
            note = "" if len(chunks) == 1 else f"\nThis is part {ci + 1} of {len(chunks)} of the chapter."
            content.append(
                text_block(
                    render(
                        load_prompt("beats_user"),
                        story_so_far=story,
                        chapter_num=chapter_number(ch),
                        n_panels=len(chunk),
                        first_id=chunk[0].id,
                        last_id=chunk[-1].id,
                    )
                    + note
                )
            )
            out, usage = ctx.llm.parse(
                model=cfg.llm.beats_model,
                system=system,
                user_content=content,
                schema=BeatSheetOut,
                stub_hint={"chapter": ch, "panel_ids": [p.id for p in chunk], "first_chapter": all_chapters[0] == ch},
            )
            outs.append(out)
            usages.append(usage)
        merged = merge_outs(outs)
        beats, skip, warnings = normalize_beats(merged, [p.id for p in panels])
        usage_total = sum_usage(usages)
        usd = round(sum(usage_usd(u, cfg.llm.prices) for u in usages), 5)
        sheet = BeatSheet(
            chapter=ch,
            model=str(usage_total.get("model") or cfg.llm.beats_model),
            prompt_version=PROMPT_VERSION,
            context_hash=context_hash,
            beats=beats,
            chapter_summary=merged.chapter_summary.strip(),
            new_characters=merged.new_characters,
            open_threads=[t.strip() for t in merged.open_threads if t.strip()],
            skip_panels=skip,
            usage=usage_total,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
        ctx.manifest.add_cost(stage=STAGE, chapter=ch, usd=usd, **{k: v for k, v in usage_total.items() if k != "request_ids"})
        for w in warnings + list(ctx.llm.warnings):
            ctx.log(f"  [yellow]{ch}: {w}[/]")
        ctx.llm.warnings.clear()
        return {"context_hash": context_hash, "usage": usage_total, "beats": len(beats), "usd": usd}

    result = run_stage(ctx.manifest, ch, STAGE, fp, [out_path], fn, force=force)
    if result == "skipped" and update_stale(ctx, ch, STAGE, context_hash):
        return "stale"
    return result
