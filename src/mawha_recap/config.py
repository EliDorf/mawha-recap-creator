"""Configuration models for a video project (config.yaml) and a series (series.yaml).

Variant presets (recap | sleep) are merged *under* the user's values, so a config only
needs to state what differs from the preset.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Variant = Literal["recap", "sleep"]

ID_RE = re.compile(r"^[a-z0-9_-]+$")


# --------------------------------------------------------------------------- sub-models


class VoiceSettings(BaseModel):
    stability: float | None = 0.5
    similarity_boost: float | None = None
    style: float | None = None
    use_speaker_boost: bool | None = None
    speed: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class VoiceConfig(BaseModel):
    id: str = ""
    model: str = "eleven_v3"
    settings: VoiceSettings = Field(default_factory=VoiceSettings)
    output_format: str = "mp3_44100_128"
    language_code: str | None = None


DEFAULT_PRICES: dict[str, dict[str, float]] = {
    # USD per 1M tokens
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
}


class LLMConfig(BaseModel):
    beats_model: str = "claude-opus-5"
    script_model: str = "claude-opus-5"
    meta_model: str = "claude-opus-5"
    fallbacks: bool = True
    effort: str | None = None  # low | medium | high | xhigh | max (None = API default)
    max_tokens: int = 16000
    prices: dict[str, dict[str, float]] = Field(default_factory=lambda: copy.deepcopy(DEFAULT_PRICES))


class VisionConfig(BaseModel):
    max_w: int = 1000
    max_h: int = 2000
    jpeg_quality: int = 85
    max_panels_per_request: int = 150


class SegmentConfig(BaseModel):
    uniformity: float = 0.985
    tolerance: int = 18
    min_gutter_px: int = 12
    min_panel_h: int = 200
    max_panel_h: int = 3200
    trim: bool = True


class PanConfig(BaseModel):
    max_px_s: float = 90.0
    hold_in_s: float = 0.3
    hold_out_s: float = 0.3
    zoom_short_panels: float = 0.06


class SubtitleConfig(BaseModel):
    burn: bool = True
    max_chars_per_line: int = 42
    font_size: int = 44
    outline: int = 2
    margin_v: int = 90
    alpha: float = 0.0  # 0 = opaque text, 0.35 = softer (sleep preset)


class StyleConfig(BaseModel):
    voice_guide: str = "Conversational storytelling to one listener. Natural contractions, varied sentence lengths, and scene-specific emotion. Avoid an announcer delivery."
    tone_notes: str = ""
    wpm: int = 150
    line_gap_s: float = 0.0
    loudness_lufs: float = -16.0
    framing: Literal["fill", "blur"] = "fill"
    pan: PanConfig = Field(default_factory=PanConfig)
    min_panel_s: float = 1.5
    crossfade_s: float = 0.125
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)


class TTSConfig(BaseModel):
    max_chars: int = 4500
    segment_on_beat: bool = False
    line_separator: str = " "
    tempo: float = 1.0  # <1 slows the assembled VO (atempo); alignment times are rescaled
    chapter_tail_s: float = 0.3
    prefer_timestamps: bool = True
    usd_per_1k_chars: float = 0.30  # rough estimate for the cost log


class RenderConfig(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    oversample: int = 2
    encoder: str = "auto"  # auto | libx264 | h264_videotoolbox | h264_nvenc | h264_qsv | h264_amf
    crf: int = 20
    preset: str = "medium"
    clip_crf: int = 16
    clip_preset: str = "faster"
    chapter_cards: bool = False
    title_card_s: float = 2.0
    workers: int = 4
    max_clips_per_graph: int = 120
    audio_bitrate: str = "160k"


class ThumbnailConfig(BaseModel):
    lockup: str = "{series}\nCh. {first}-{last}"
    font_size: int = 96
    fill: str = "#FFFFFF"
    stroke: str = "#000000"
    stroke_width: int = 6


class PackageConfig(BaseModel):
    title_template: str = "{series} Chapters {first}-{last} | Full Recap"
    description_template: str = "{hook}\n\nChapters:\n{chapters}\n\n{series}, chapters {first}-{last}."
    chapter_title_template: str = "Chapter {n}"
    tags: list[str] = Field(default_factory=list)
    thumbnail: ThumbnailConfig = Field(default_factory=ThumbnailConfig)


class MusicConfig(BaseModel):
    tracks: list[str] = Field(default_factory=list)
    gain_db: float = -18.0


class OpeningConfig(BaseModel):
    enabled: bool = False
    max_source_chapters: int = Field(default=3, ge=1, le=10)
    max_panels: int = Field(default=24, ge=4, le=60)
    target_words: int = Field(default=40, ge=20, le=80)


class GateConfig(BaseModel):
    require_approval: bool = True


# --------------------------------------------------------------------------- top-level


class VideoConfig(BaseModel):
    series: str
    slug: str
    variant: Variant = "recap"
    chapters: list[str] | str | None = None  # explicit ids, "ch012-ch045" range, or None = all in input/
    target_runtime_min: float = 30.0
    source_language: str = "en"
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    package: PackageConfig = Field(default_factory=PackageConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    gate: GateConfig = Field(default_factory=GateConfig)
    opening: OpeningConfig = Field(default_factory=OpeningConfig)

    @field_validator("slug")
    @classmethod
    def _slug_ok(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"slug {v!r} must match [a-z0-9_-]+ (it is used inside ffmpeg paths)")
        return v

    # Which parts of the config each stage depends on (feeds the stage fingerprint).
    def subset_for(self, stage: str) -> dict[str, Any]:
        d = self.model_dump()
        s = d["style"]
        match stage:
            case "00_ingest":
                return {}
            case "01_panels":
                return {"segment": d["segment"]}
            case "02_beats":
                return {
                    "model": d["llm"]["beats_model"],
                    "effort": d["llm"]["effort"],
                    "vision": d["vision"],
                    "series": d["series"],
                    "source_language": d["source_language"],
                }
            case "02_script":
                return {
                    "model": d["llm"]["script_model"],
                    "effort": d["llm"]["effort"],
                    "voice_guide": s["voice_guide"],
                    "tone_notes": s["tone_notes"],
                    "variant": d["variant"],
                    "series": d["series"],
                    "source_language": d["source_language"],
                }
            case "02_opening":
                return {"opening": d["opening"], "model": d["llm"]["script_model"], "vision": d["vision"], "series": d["series"], "voice_guide": s["voice_guide"]}
            case "03_tts":
                return {
                    "voice": d["voice"],
                    "tts": d["tts"],
                    "line_gap_s": s["line_gap_s"],
                    "loudness_lufs": s["loudness_lufs"],
                    "lead_in": d["render"]["title_card_s"] if d["render"]["chapter_cards"] else 0.0,
                }
            case "04_render":
                return {
                    "render": d["render"],
                    "framing": s["framing"],
                    "pan": s["pan"],
                    "min_panel_s": s["min_panel_s"],
                    "crossfade_s": s["crossfade_s"],
                    "subtitles": s["subtitles"],
                    "series": d["series"],
                }
            case "05_assemble":
                return {
                    "package": d["package"],
                    "music": d["music"],
                    "meta_model": d["llm"]["meta_model"],
                    "series": d["series"],
                    "slug": d["slug"],
                    "variant": d["variant"],
                }
        raise KeyError(stage)


class Character(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    notes: str = ""


class SeriesConfig(BaseModel):
    name: str = ""
    characters: list[Character] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    story_so_far: str = ""  # carry-over from previous videos of the same series

    def glossary_text(self) -> str:
        parts: list[str] = []
        if self.characters:
            parts.append("Characters:")
            for c in self.characters:
                alias = f" (also: {', '.join(c.aliases)})" if c.aliases else ""
                note = f" - {c.notes}" if c.notes else ""
                parts.append(f"- {c.name}{alias}{note}")
        if self.glossary:
            parts.append("Terms:")
            parts.extend(f"- {k}: {v}" for k, v in self.glossary.items())
        if self.notes:
            parts.append("Notes:\n" + self.notes)
        return "\n".join(parts)


# --------------------------------------------------------------------------- presets

PRESETS: dict[str, dict[str, Any]] = {
    "recap": {
        "opening": {"enabled": True},
        "style": {
            "wpm": 150,
            "line_gap_s": 0.0,
            "loudness_lufs": -16.0,
            "pan": {"max_px_s": 90.0, "hold_in_s": 0.3, "hold_out_s": 0.3},
            "min_panel_s": 1.5,
            "crossfade_s": 0.125,
            "subtitles": {"alpha": 0.0},
        },
        "package": {"title_template": "{series} Chapters {first}-{last} | Full Recap"},
    },
    "sleep": {
        "tts": {"segment_on_beat": True, "line_separator": "\n\n", "chapter_tail_s": 1.5},
        "style": {
            "voice_guide": "Warm, unhurried storytelling. Gentle delivery and natural pauses, without exaggerated emphasis.",
            "wpm": 130,
            "line_gap_s": 0.9,
            "loudness_lufs": -20.0,
            "pan": {"max_px_s": 45.0, "hold_in_s": 0.6, "hold_out_s": 0.6},
            "min_panel_s": 2.5,
            "crossfade_s": 0.8,
            "subtitles": {"alpha": 0.35},
        },
        "package": {"title_template": "{series} Chapters {first}-{last} | Sleep Recap"},
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_video_config(path: Path) -> VideoConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    variant = raw.get("variant", "recap")
    if variant not in PRESETS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(PRESETS)}")
    merged = deep_merge(PRESETS[variant], raw)
    return VideoConfig.model_validate(merged)


def load_series_config(path: Path | None) -> SeriesConfig:
    if path is None or not path.exists():
        return SeriesConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SeriesConfig.model_validate(raw)


# --------------------------------------------------------------------------- chapter ids

_NUM_RE = re.compile(r"(\d+)")


def chapter_id(value: str | int) -> str:
    """Normalize 'ch12', 'Chapter 12', '012', 12 -> 'ch012'."""
    if isinstance(value, int):
        n = value
    else:
        m = _NUM_RE.search(str(value))
        if not m:
            raise ValueError(f"cannot find a chapter number in {value!r}")
        n = int(m.group(1))
    return f"ch{n:03d}"


def chapter_number(ch: str) -> int:
    m = _NUM_RE.search(ch)
    if not m:
        raise ValueError(f"not a chapter id: {ch!r}")
    return int(m.group(1))


def select_chapters(spec: list[str] | str | None, available: list[str]) -> list[str]:
    avail = sorted(set(available), key=chapter_number)
    if spec is None:
        return avail
    if isinstance(spec, str):
        parts = spec.replace("..", "-").split("-")
        if len(parts) != 2:
            raise ValueError(f"chapter range must look like 'ch012-ch045', got {spec!r}")
        lo, hi = chapter_number(chapter_id(parts[0])), chapter_number(chapter_id(parts[1]))
        return [c for c in avail if lo <= chapter_number(c) <= hi]
    wanted = [chapter_id(x) for x in spec]
    missing = [c for c in wanted if c not in avail]
    if missing:
        raise ValueError(f"chapters not found in input/: {', '.join(missing)}")
    return sorted(wanted, key=chapter_number)
