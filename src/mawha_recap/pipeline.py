"""Orchestrates stages over chapters. Each stage module exposes run_chapter(ctx, ch) or run(ctx, chapters)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from .config import SeriesConfig, VideoConfig, chapter_id, select_chapters
from .manifest import STAGE_NUMBERS, Manifest
from .project import Project


@dataclass
class Context:
    project: Project
    config: VideoConfig
    series: SeriesConfig
    manifest: Manifest
    console: Console
    force: bool = False
    stub: bool = False
    reallocate: bool = False
    pin_context: bool = False
    skip_gate: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    _llm: Any = None
    _tts: Any = None

    @property
    def llm(self) -> Any:
        if self._llm is None:
            from .providers.claude import make_llm

            self._llm = make_llm(self.config, stub=self.stub)
        return self._llm

    @property
    def tts(self) -> Any:
        if self._tts is None:
            from .providers.elevenlabs import make_tts

            self._tts = make_tts(self.config, stub=self.stub)
        return self._tts

    def log(self, msg: str) -> None:
        self.console.print(msg)


def make_context(project_root: Path, console: Console | None = None, **flags: Any) -> Context:
    project = Project(project_root)
    cfg = project.config
    manifest = Manifest.load(project.manifest_path, project.root)
    manifest.data["video"] = {"slug": cfg.slug, "variant": cfg.variant, "series": cfg.series}
    return Context(
        project=project,
        config=cfg,
        series=project.series,
        manifest=manifest,
        console=console or Console(),
        **flags,
    )


def resolve_chapters(ctx: Context, only: list[str] | None) -> list[str]:
    from .stages.ingest import discover_chapters

    available = list(discover_chapters(ctx.project.input_dir).keys())
    selected = select_chapters(ctx.config.chapters, available)
    if only:
        wanted = {chapter_id(c) for c in only}
        missing = sorted(wanted - set(selected))
        if missing:
            raise ValueError(f"chapters not in this video's selection: {', '.join(missing)}")
        selected = [c for c in selected if c in wanted]
    if not selected:
        raise ValueError(f"no chapters found under {ctx.project.input_dir}")
    return selected


def run_pipeline(ctx: Context, stage_numbers: list[int], only: list[str] | None = None) -> None:
    from .stages import assemble, beats, ingest, render, script, segment, tts

    all_selected = resolve_chapters(ctx, None)
    selected = resolve_chapters(ctx, only)
    ctx.project.ensure_fonts()

    for n in sorted(set(stage_numbers)):
        for stage_id in STAGE_NUMBERS[n]:
            ctx.log(f"[bold cyan]== stage {stage_id}[/]")
            if stage_id == "00_ingest":
                for ch in selected:
                    _report(ctx, ch, stage_id, ingest.run_chapter(ctx, ch))
            elif stage_id == "01_panels":
                for ch in selected:
                    _report(ctx, ch, stage_id, segment.run_chapter(ctx, ch))
            elif stage_id == "02_beats":
                for ch in selected:
                    _report(ctx, ch, stage_id, beats.run_chapter(ctx, ch, all_chapters=all_selected))
            elif stage_id == "02_script":
                script.allocate_budget(ctx, all_selected)
                for ch in selected:
                    _report(ctx, ch, stage_id, script.run_chapter(ctx, ch, all_chapters=all_selected))
                script.write_review(ctx, all_selected)
            elif stage_id == "03_tts":
                for ch in selected:
                    _report(ctx, ch, stage_id, tts.run_chapter(ctx, ch))
            elif stage_id == "04_render":
                for ch in selected:
                    _report(ctx, ch, stage_id, render.run_chapter(ctx, ch))
            elif stage_id == "05_assemble":
                _report(ctx, "video", stage_id, assemble.run(ctx, all_selected))
    ctx.manifest.save()


def _report(ctx: Context, ch: str, stage_id: str, result: str) -> None:
    color = {"ok": "green", "skipped": "dim"}.get(result, "yellow")
    ctx.log(f"  {ch}  {stage_id}  [{color}]{result}[/]")
