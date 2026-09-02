"""Stage 2b: turn a chapter's beat sheet into narration lines that keep their panel references."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..config import chapter_number
from ..manifest import fingerprint, now_iso, run_stage, sha256_file, sha256_text
from ..models import BeatSheet, Script, ScriptLine, ScriptOut
from ..prompts import load_prompt, render
from ..providers.claude import cache_block, usage_usd
from .beats import VARIANT_LABEL, load_beats, story_so_far, update_stale

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "02_script"
PROMPT_VERSION = "writer-v1"
CODE_VERSION = "script-code-v1"
BUDGET_STEP = 25
MIN_CHAPTER_WORDS = 60
TOLERANCE = 0.15


# ------------------------------------------------------------------ budget


def round_to(n: float, step: int = BUDGET_STEP) -> int:
    return int(round(n / step) * step)


def allocate_budget(ctx: Context, all_chapters: list[str]) -> dict[str, Any]:
    """Split the video's word budget across chapters by beat count; stored once in the manifest."""
    cfg, m = ctx.config, ctx.manifest
    counts: dict[str, int | None] = {}
    for ch in all_chapters:
        p = ctx.project.beats_json(ch)
        counts[ch] = len(load_beats(p).beats) if p.exists() else None
    complete = all(v is not None for v in counts.values())
    b = m.budget
    same = (
        bool(b)
        and b.get("chapters") == all_chapters
        and b.get("wpm") == cfg.style.wpm
        and b.get("runtime_min") == cfg.target_runtime_min
    )
    if same and not ctx.reallocate and not (b.get("partial") and complete):
        return b
    total = round(cfg.target_runtime_min * cfg.style.wpm)
    known = [v for v in counts.values() if v]
    mean = (sum(known) / len(known)) if known else 1.0
    weights = {ch: float(v if v else mean) for ch, v in counts.items()}
    tw = sum(weights.values()) or 1.0
    per = {ch: max(MIN_CHAPTER_WORDS, round_to(total * w / tw)) for ch, w in weights.items()}
    b = {
        "wpm": cfg.style.wpm,
        "runtime_min": cfg.target_runtime_min,
        "total_words": total,
        "basis": "beats",
        "chapters": all_chapters,
        "per_chapter": per,
        "partial": not complete,
        "allocated_at": now_iso(),
    }
    m.budget = b
    m.save()
    ctx.log(f"  budget: {total} words over {len(all_chapters)} chapters" + (" (partial: some beats missing)" if not complete else ""))
    return b


# ------------------------------------------------------------------ yaml i/o


def script_to_dict(script: Script) -> dict[str, Any]:
    lines = []
    for line in script.lines:
        d: dict[str, Any] = {"id": line.id, "beat": line.beat, "panel_ids": list(line.panel_ids), "text": line.text}
        if line.tts_text:
            d["tts_text"] = line.tts_text
        if line.pause_after is not None:
            d["pause_after"] = line.pause_after
        lines.append(d)
    return {
        "chapter": script.chapter,
        "prompt_version": script.prompt_version,
        "model": script.model,
        "budget_words": script.budget_words,
        "lines": lines,
    }


def save_script_yaml(script: Script, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(script_to_dict(script), sort_keys=False, allow_unicode=True, width=1000, default_flow_style=None)
    path.write_text("# Edit `text` (subtitles) and optionally `tts_text` (voice). Keep panel_ids. Then `recap approve`.\n" + text, encoding="utf-8")


def load_script(path: Path) -> Script:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for i, line in enumerate(data.get("lines", []), start=1):
        line.setdefault("id", f"{data.get('chapter', 'ch000')}_l{i:03d}")
        line.setdefault("beat", "")
        line.setdefault("panel_ids", [])
    return Script.model_validate(data)


# ------------------------------------------------------------------ validation


def normalize_script(out: ScriptOut, sheet: BeatSheet, ch: str) -> tuple[list[ScriptLine], list[str]]:
    story_ids = [pid for b in sheet.beats for pid in b.panel_ids]
    order = {pid: i for i, pid in enumerate(story_ids)}
    beat_ids = {b.id for b in sheet.beats}
    beat_of_panel = {pid: b.id for b in sheet.beats for pid in b.panel_ids}
    warnings: list[str] = []
    used: set[str] = set()
    lines: list[ScriptLine] = []
    for raw in out.lines:
        text = " ".join(raw.text.split())
        if not text:
            continue
        ids: list[str] = []
        for pid in raw.panel_ids:
            if pid not in order:
                warnings.append(f"line referenced unknown/skipped panel {pid!r}; dropped")
                continue
            if pid in used:
                continue
            used.add(pid)
            ids.append(pid)
        ids.sort(key=order.__getitem__)
        beat = raw.beat if raw.beat in beat_ids else (beat_of_panel.get(ids[0], "") if ids else "")
        tts = " ".join(raw.tts_text.split()) or None
        if tts == text:
            tts = None
        lines.append(ScriptLine(id="", beat=beat, panel_ids=ids, text=text, tts_text=tts))
    if not lines:
        raise ValueError("the model returned no narration lines")
    uncovered = [pid for pid in story_ids if pid not in used]
    if uncovered:
        warnings.append(f"{len(uncovered)} story panel(s) not referenced by any line; attached to the nearest line")
        for pid in uncovered:
            target: ScriptLine | None = None
            for line in lines:
                if line.panel_ids and order[line.panel_ids[-1]] < order[pid]:
                    target = line
                elif line.panel_ids and order[line.panel_ids[0]] > order[pid]:
                    break
            if target is None:
                target = next((line for line in lines if line.panel_ids), lines[0])
            target.panel_ids.append(pid)
            target.panel_ids.sort(key=order.__getitem__)
    for i, line in enumerate(lines):
        if not line.panel_ids:
            src = lines[i - 1] if i > 0 else next((x for x in lines if x.panel_ids), None)
            if src is not None and src.panel_ids:
                line.panel_ids = [src.panel_ids[-1]]
                warnings.append(f"line {i + 1} listed no panels; it will hold on {src.panel_ids[-1]}")
    last = -1
    for line in lines:
        if line.panel_ids and order[line.panel_ids[0]] < last:
            warnings.append("panel order is not monotone across lines; the video will follow line order")
            break
        if line.panel_ids:
            last = order[line.panel_ids[-1]]
    for i, line in enumerate(lines, start=1):
        line.id = f"{ch}_l{i:03d}"
    return lines, warnings


# ------------------------------------------------------------------ context


def previous_lines(ctx: Context, ch: str, all_chapters: list[str], n: int = 5) -> tuple[str, str]:
    prev = [c for c in all_chapters if chapter_number(c) < chapter_number(ch)]
    if not prev:
        return "", sha256_text("")
    p = ctx.project.script_yaml(prev[-1])
    if not p.exists():
        return "", sha256_text("")
    tail = [line.text for line in load_script(p).lines[-n:]]
    text = "\n".join(tail)
    return text, sha256_text(text)


# ------------------------------------------------------------------ stage


def run_chapter(ctx: Context, ch: str, all_chapters: list[str]) -> str:
    project, cfg = ctx.project, ctx.config
    beats_path = project.beats_json(ch)
    if not beats_path.exists():
        raise FileNotFoundError(f"{ch}: run the beats step first ({beats_path} missing)")
    sheet = load_beats(beats_path)
    per = ctx.manifest.budget.get("per_chapter", {})
    if ch not in per:
        allocate_budget(ctx, all_chapters)
        per = ctx.manifest.budget.get("per_chapter", {})
    budget = int(per[ch])
    story, story_hash = story_so_far(ctx, ch, all_chapters)
    prev_text, prev_hash = previous_lines(ctx, ch, all_chapters)
    context_hash = sha256_text(story_hash + prev_hash)
    fp = fingerprint(
        stage=STAGE,
        version=[PROMPT_VERSION, CODE_VERSION],
        config=cfg.subset_for(STAGE),
        beats=sha256_file(beats_path),
        budget=budget,
    )
    out_path = project.script_yaml(ch)
    rec = ctx.manifest.stage(ch, STAGE)
    force = ctx.force or bool(ctx.refresh_stale and rec.get("stale"))

    def fn() -> dict[str, Any]:
        system = [
            cache_block(
                render(
                    load_prompt("writer"),
                    series=cfg.series,
                    variant_label=VARIANT_LABEL[cfg.variant],
                    voice_guide=cfg.style.voice_guide.strip() or "Clear, warm, unhurried narrator.",
                    tone_notes=cfg.style.tone_notes.strip(),
                )
            )
        ]
        beats_json = json.dumps([b.model_dump() for b in sheet.beats], ensure_ascii=False, indent=1)
        user = render(
            load_prompt("writer_user"),
            story_so_far=story,
            previous_lines=prev_text or "(this is the first chapter of the video)",
            chapter_num=chapter_number(ch),
            beats_json=beats_json,
            skip_panels=", ".join(sheet.skip_panels) or "none",
            budget_words=budget,
        )
        hint = {"beats": [{"id": b.id, "panel_ids": b.panel_ids} for b in sheet.beats], "budget_words": budget}
        usages: list[dict[str, Any]] = []
        lines: list[ScriptLine] = []
        warnings: list[str] = []
        for attempt in range(2):
            out, usage = ctx.llm.parse(
                model=cfg.llm.script_model, system=system, user_content=user, schema=ScriptOut, stub_hint=hint
            )
            usages.append(usage)
            lines, warnings = normalize_script(out, sheet, ch)
            wc = sum(len(line.text.split()) for line in lines)
            if abs(wc - budget) <= TOLERANCE * budget or attempt == 1 or ctx.llm.name == "stub":
                break
            user += f"\n\nYour previous draft had {wc} words; the target is {budget}. Rewrite the whole chapter to hit the target."
        wc = sum(len(line.text.split()) for line in lines)
        if abs(wc - budget) > TOLERANCE * budget:
            warnings.append(f"word count {wc} is outside the budget {budget} +/- {int(TOLERANCE * 100)}%")
        script = Script(
            chapter=ch,
            prompt_version=PROMPT_VERSION,
            model=str(usages[-1].get("model") or cfg.llm.script_model),
            budget_words=budget,
            lines=lines,
        )
        save_script_yaml(script, out_path)
        usd = round(sum(usage_usd(u, cfg.llm.prices) for u in usages), 5)
        tokens = {k: sum(int(u.get(k, 0) or 0) for u in usages) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        ctx.manifest.add_cost(stage=STAGE, chapter=ch, usd=usd, model=script.model, **tokens)
        for w in warnings + list(ctx.llm.warnings):
            ctx.log(f"  [yellow]{ch}: {w}[/]")
        ctx.llm.warnings.clear()
        return {"context_hash": context_hash, "words": wc, "budget": budget, "usd": usd}

    result = run_stage(ctx.manifest, ch, STAGE, fp, [out_path], fn, force=force)
    if result == "skipped" and update_stale(ctx, ch, STAGE, context_hash):
        return "stale"
    return result


def write_review(ctx: Context, chapters: list[str]) -> Path:
    from .review import render_review

    return render_review(ctx, chapters)
