"""Stage 3: narration audio. Lines are grouped into TTS segments (better prosody, fewer requests), each
segment's character alignment is mapped back to lines and words, the segment audio is sliced per line
and re-assembled with the variant's pauses, then normalised (two-pass loudnorm). timeline.json is the
single source of truth for the video, the burned subtitles and the sidecar SRT.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..manifest import canonical_json, fingerprint, run_stage, short_hash
from ..media import ffmpeg as ff
from ..models import ScriptLine, Timeline, TimelineLine, TimelineSegment, Word
from ..providers.elevenlabs import TTSResult, load_alignment, save_alignment
from ..text.alignment import CharAlignment, build_segment_text, line_timing, map_alignment, strip_tags
from .review import script_hash
from .script import load_script

if TYPE_CHECKING:
    from ..pipeline import Context

STAGE = "03_tts"
CODE_VERSION = "tts-code-v1"
SR = 48000
LEAD_KEEP_S = 0.15  # leading silence kept before the first word of a segment
TAIL_KEEP_S = 0.25


# ------------------------------------------------------------------ audio helpers


def decode_pcm(path: Path) -> np.ndarray:
    data = ff.run_raw(
        [ff.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"]
    )
    return np.frombuffer(data, dtype=np.int16)


def apply_tempo(pcm: np.ndarray, tempo: float) -> np.ndarray:
    if abs(tempo - 1.0) < 1e-6 or pcm.size == 0:
        return pcm
    data = ff.run_raw(
        [ff.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
         "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "pipe:0",
         "-af", f"atempo={tempo}", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        input_bytes=pcm.tobytes(),
    )
    return np.frombuffer(data, dtype=np.int16)


def write_wav(path: Path, pcm: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.astype("<i2").tobytes())


def silence(seconds: float) -> np.ndarray:
    return np.zeros(max(0, int(round(seconds * SR))), dtype=np.int16)


# ------------------------------------------------------------------ segmenting


def segment_lines(lines: list[ScriptLine], max_chars: int) -> list[list[ScriptLine]]:
    """Group consecutive lines up to max_chars; prefer to break where the beat changes."""
    groups: list[list[ScriptLine]] = []
    cur: list[ScriptLine] = []
    cur_chars = 0
    for line in lines:
        t = line.tts_text or line.text
        n = len(t) + 2
        beat_change = bool(cur) and line.beat != cur[-1].beat
        if cur and (cur_chars + n > max_chars or (beat_change and cur_chars >= 0.6 * max_chars)):
            groups.append(cur)
            cur, cur_chars = [], 0
        cur.append(line)
        cur_chars += n
    if cur:
        groups.append(cur)
    return groups


# ------------------------------------------------------------------ stage


def run_chapter(ctx: Context, ch: str) -> str:
    project, cfg = ctx.project, ctx.config
    script_path = project.script_yaml(ch)
    if not script_path.exists():
        raise FileNotFoundError(f"{ch}: run stage 2 first ({script_path} missing)")
    s_hash = script_hash(script_path)
    fp = fingerprint(stage=STAGE, version=CODE_VERSION, config=cfg.subset_for(STAGE), script=s_hash, provider=ctx.tts.name)
    vo, tl = project.vo_wav(ch), project.timeline_json(ch)

    def fn() -> dict[str, Any]:
        if cfg.gate.require_approval and not ctx.skip_gate and ctx.manifest.approval(ch) != s_hash:
            raise RuntimeError(
                f"{ch}: script is not approved (or changed since approval). Review review/review.html, then run "
                "`recap approve`, or pass --no-gate."
            )
        script = load_script(script_path)
        if not script.lines:
            raise ValueError(f"{ch}: script has no lines")
        tdir = project.tts_dir(ch)
        tdir.mkdir(parents=True, exist_ok=True)
        lead_in = cfg.render.title_card_s if cfg.render.chapter_cards else 0.0
        max_chars = min(cfg.tts.max_chars, ctx.tts.max_chars())
        groups = segment_lines(script.lines, max_chars)
        voice_key = canonical_json({"voice": cfg.voice.model_dump(), "provider": ctx.tts.name})

        # 1. synthesize (or reuse cached) segments
        seg_texts: list[tuple[str, list[str]]] = []
        results: list[tuple[TTSResult, CharAlignment, str, int, bool]] = []
        for group in groups:
            texts = [line.tts_text or line.text for line in group]
            seg_texts.append((build_segment_text(texts)[0], texts))
        for gi, (_group, (text, texts)) in enumerate(zip(groups, seg_texts, strict=True), start=1):
            key = short_hash(voice_key + "\n" + text, 12)
            seg_id = f"seg_{gi:03d}"
            align_path = tdir / f"{seg_id}-{key}.align.json"
            existing = sorted(tdir.glob(f"{seg_id}-{key}.*")) if align_path.exists() else []
            audio_files = [p for p in existing if p.suffix != ".json"]
            align_text = build_segment_text([strip_tags(t) for t in texts])[0]
            if audio_files:
                alignment, aligned_text, source, chars = load_alignment(align_path)
                res = TTSResult(audio_files[0], alignment, aligned_text, source, chars)
                fresh = False
            else:
                for old in tdir.glob(f"{seg_id}-*"):
                    old.unlink()
                ext = "wav" if ctx.tts.name == "stub" else ("mp3" if "mp3" in cfg.voice.output_format else "bin")
                prev_text = seg_texts[gi - 2][0] if gi >= 2 else None
                next_text = seg_texts[gi][0] if gi < len(groups) else None
                res = ctx.tts.synthesize(
                    text, tdir / f"{seg_id}-{key}.{ext}", align_text=align_text, prev_text=prev_text, next_text=next_text
                )
                save_alignment(align_path, res, text)
                fresh = True
            results.append((res, res.alignment, res.aligned_text, res.chars, fresh))

        # 2. slice per line, assemble with pauses
        parts: list[np.ndarray] = [silence(lead_in)]
        cursor = parts[0].size
        tl_lines: list[TimelineLine] = []
        tl_segments: list[TimelineSegment] = []
        new_chars = 0
        for (group, (text, texts)), (res, alignment, aligned_text, chars, fresh) in zip(
            zip(groups, seg_texts, strict=True), results, strict=True
        ):
            if fresh:
                new_chars += chars
            pcm = decode_pcm(res.audio_path)
            seg_len = pcm.size / SR
            if aligned_text == text:
                used_text, spans = build_segment_text(texts)
            else:
                used_text, spans = build_segment_text([strip_tags(t) for t in texts])
            starts, ends = map_alignment(used_text, alignment)
            timings = [line_timing(used_text, span, starts, ends, seg_len) for span in spans]
            for i, line in enumerate(group):
                s, e, words = timings[i]
                cut0 = max(0.0, s - LEAD_KEEP_S) if i == 0 else (timings[i - 1][1] + s) / 2
                cut1 = min(seg_len, e + TAIL_KEEP_S) if i == len(group) - 1 else (e + timings[i + 1][0]) / 2
                cut1 = max(cut1, cut0 + 0.05)
                chunk = pcm[int(cut0 * SR) : int(cut1 * SR)]
                chunk = apply_tempo(chunk, cfg.tts.tempo)
                scale = 1.0 / cfg.tts.tempo
                line_start = cursor / SR
                parts.append(chunk)
                cursor += chunk.size
                gap = line.pause_after if line.pause_after is not None else cfg.style.line_gap_s
                g = silence(gap)
                parts.append(g)
                cursor += g.size
                tl_lines.append(
                    TimelineLine(
                        id=line.id,
                        start=round(line_start + (s - cut0) * scale, 3),
                        end=round(line_start + (e - cut0) * scale, 3),
                        panel_ids=list(line.panel_ids),
                        text=line.text,
                        words=[
                            Word(w=w.w, s=round(line_start + (w.s - cut0) * scale, 3), e=round(line_start + (w.e - cut0) * scale, 3))
                            for w in words
                        ],
                    )
                )
            tl_segments.append(
                TimelineSegment(
                    id=res.audio_path.name.split("-")[0],
                    line_ids=[line.id for line in group],
                    audio=res.audio_path.name,
                    chars=chars,
                    timing_source=res.timing_source,
                    request_id=res.request_id,
                )
            )
        parts.pop()  # last inter-line gap -> chapter tail
        cursor -= 0  # (cursor is recomputed below)
        parts.append(silence(cfg.tts.chapter_tail_s))
        raw = np.concatenate(parts)
        duration = raw.size / SR
        raw_path = tdir / f"{ch}.raw.wav"
        write_wav(raw_path, raw)

        # 3. loudness normalisation (two-pass, linear)
        target = cfg.style.loudness_lufs
        measured = ff.loudnorm_measure(raw_path.name, target, cwd=tdir)
        applied = ff.loudnorm_apply(raw_path.name, str(vo.resolve()), target, measured, cwd=tdir)
        vo_dur = ff.duration(vo)
        if abs(vo_dur - duration) > 0.05:
            ctx.log(f"  [yellow]{ch}: normalised VO is {vo_dur:.3f}s but timeline expects {duration:.3f}s[/]")
        loud = {
            "target_i": target,
            "input_i": float(measured.get("input_i", 0)),
            "output_i": float(applied.get("output_i", 0)) if applied else None,
            "mode": applied.get("normalization_type") if applied else None,
        }
        if applied and applied.get("normalization_type") not in (None, "linear"):
            ctx.log(f"  [yellow]{ch}: loudnorm fell back to {applied.get('normalization_type')} mode[/]")

        timeline = Timeline(
            chapter=ch, sample_rate=SR, duration=round(duration, 3), lead_in=lead_in, loudness=loud,
            segments=tl_segments, lines=tl_lines,
        )
        tl.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        usd = round(new_chars / 1000 * cfg.tts.usd_per_1k_chars, 4) if ctx.tts.name != "stub" else 0.0
        if new_chars:
            ctx.manifest.add_cost(stage=STAGE, chapter=ch, chars=new_chars, usd=usd, provider=ctx.tts.name)
        for w in list(ctx.tts.warnings):
            ctx.log(f"  [yellow]{ch}: {w}[/]")
        ctx.tts.warnings.clear()
        return {
            "segments": len(groups),
            "duration": round(duration, 3),
            "timing_sources": sorted({s.timing_source for s in tl_segments}),
            "usd": usd,
        }

    return run_stage(ctx.manifest, ch, STAGE, fp, [vo, tl], fn, force=ctx.force)


def load_timeline(path: Path) -> Timeline:
    return Timeline.model_validate_json(path.read_text(encoding="utf-8"))
