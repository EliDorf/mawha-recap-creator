"""Pydantic schemas for every artifact the pipeline writes, plus the LLM output schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- Stage 1: panels


class Panel(BaseModel):
    id: str
    y0: int
    y1: int
    w: int
    h: int
    file: str
    sha256: str
    ink: float
    flags: list[str] = Field(default_factory=list)


class PanelsDoc(BaseModel):
    chapter: str
    strip: dict
    bg_rgb: list[int]
    params: dict
    panels: list[Panel]

    def by_id(self) -> dict[str, Panel]:
        return {p.id: p for p in self.panels}


# ---------------------------------------------------------------- Stage 2a: beats


class BeatOut(BaseModel):
    """One narrative beat as emitted by the vision model."""

    panel_ids: list[str] = Field(description="Panel ids covered by this beat, in reading order")
    what_happens: str = Field(description="What visibly happens, 1-3 sentences, factual")
    dialogue_gist: str = Field(description="Paraphrase of the dialogue or text in these panels; empty if none")
    emotional_beat: str = Field(description="The emotional note of the beat, 1-4 words")
    importance: int = Field(description="1 = minor, 2 = normal, 3 = major plot point")


class NewCharacter(BaseModel):
    name: str
    notes: str


class BeatSheetOut(BaseModel):
    beats: list[BeatOut]
    chapter_summary: str = Field(description="120-200 word summary of what happened in this chapter")
    new_characters: list[NewCharacter] = Field(description="Characters introduced or first named in this chapter")
    open_threads: list[str] = Field(description="Unresolved questions or cliffhangers after this chapter")
    skip_panels: list[str] = Field(description="Panel ids that are credits, ads, blank or otherwise not story")


class Beat(BaseModel):
    id: str
    panel_ids: list[str]
    what_happens: str
    dialogue_gist: str = ""
    emotional_beat: str = ""
    importance: int = 2


class BeatSheet(BaseModel):
    chapter: str
    model: str
    prompt_version: str
    context_hash: str
    beats: list[Beat]
    chapter_summary: str
    new_characters: list[NewCharacter] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)
    skip_panels: list[str] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)


# ---------------------------------------------------------------- Stage 2b: script


class ScriptLineOut(BaseModel):
    beat: str = Field(description="Id of the beat this line narrates, e.g. b03")
    panel_ids: list[str] = Field(description="Panels shown while this line is spoken, in order")
    text: str = Field(description="The narration line as it should appear in subtitles (no audio tags)")
    tts_text: str = Field(
        description="Optional variant for the voice with pacing devices (ellipses, [pause]); empty string to reuse text"
    )


class ScriptOut(BaseModel):
    lines: list[ScriptLineOut]


class ScriptLine(BaseModel):
    id: str
    beat: str
    panel_ids: list[str]
    text: str
    tts_text: str | None = None
    pause_after: float | None = None
    framing: Literal["fill", "blur", "cover"] | None = None
    visual_crop: tuple[int, int, int, int] | None = None  # x, y, width, height in source pixels


class Script(BaseModel):
    chapter: str
    prompt_version: str
    model: str
    budget_words: int
    lines: list[ScriptLine]

    def word_count(self) -> int:
        return sum(len(line.text.split()) for line in self.lines)


class MetaOut(BaseModel):
    hook: str = Field(description="Two sentences that make someone want to watch, no spoilers of the ending")
    title_suffix: str = Field(description="A 3-7 word teaser suitable for the end of a YouTube title")


class OpeningShotOut(BaseModel):
    panel_id: str
    text: str = Field(description="Brief spoken narration for this shot, grounded in the supplied beats")
    crop: tuple[int, int, int, int] = Field(description="Artwork crop x,y,width,height in normalized 0-1000 source coordinates; avoid gutters and text")


class OpeningOut(BaseModel):
    shots: list[OpeningShotOut] = Field(min_length=3, max_length=5)


# ---------------------------------------------------------------- Stage 3: timeline


class Word(BaseModel):
    w: str
    s: float
    e: float


class TimelineLine(BaseModel):
    id: str
    start: float
    end: float
    panel_ids: list[str]
    text: str
    words: list[Word] = Field(default_factory=list)
    framing: Literal["fill", "blur", "cover"] | None = None
    visual_crop: tuple[int, int, int, int] | None = None


class TimelineSegment(BaseModel):
    id: str
    line_ids: list[str]
    audio: str
    chars: int
    timing_source: str
    request_id: str | None = None


class Timeline(BaseModel):
    chapter: str
    sample_rate: int
    duration: float
    lead_in: float
    loudness: dict = Field(default_factory=dict)
    segments: list[TimelineSegment] = Field(default_factory=list)
    lines: list[TimelineLine] = Field(default_factory=list)
