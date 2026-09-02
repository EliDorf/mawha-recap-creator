"""`recap` command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .manifest import ALL_STAGES, Manifest

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turn webtoon chapter images into narrated recap / sleep videos.",
)
console = Console()


def _version_cb(value: bool) -> None:
    if value:
        console.print(f"recap {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool], typer.Option("--version", callback=_version_cb, is_eager=True, help="Show version")
    ] = None,
) -> None:
    pass


CONFIG_TEMPLATE = """# mawha-recap-creator video config
series: "{series}"
slug: "{slug}"
variant: {variant}            # recap | sleep  (selects preset pacing / loudness defaults)
chapters: null                # null = every chapter in input/, or "ch012-ch045", or [ch012, ch013]
target_runtime_min: {runtime}
source_language: en

voice:
  id: "{voice}"
  model: eleven_v3
  settings: {{stability: 0.5}}
  output_format: mp3_44100_128

llm:
  beats_model: claude-opus-5
  script_model: claude-opus-5
  meta_model: claude-opus-5
  fallbacks: true

style:
  voice_guide: |
    Warm, steady narrator. Present tense. Plain language, no hype. Refer to characters by name.
  # wpm / line_gap_s / loudness_lufs / pan / crossfade_s come from the variant preset; override here if needed.
  subtitles: {{burn: true}}

render:
  chapter_cards: true
  encoder: auto              # auto | libx264 | h264_videotoolbox | h264_nvenc | h264_qsv | h264_amf
  workers: 4

package:
  tags: []

gate:
  require_approval: true
"""

SERIES_TEMPLATE = """# Series-level notes shared by every video of this series.
name: "{series}"
characters: []
#  - name: Sung Jin-Woo
#    aliases: [Jinwoo, the Shadow Monarch]
#    notes: protagonist, E-rank hunter turned player
glossary: {{}}
notes: ""
story_so_far: ""   # optional carry-over summary from earlier videos
"""


@app.command()
def init(
    project_dir: Annotated[Path, typer.Argument(help="Project directory to create")],
    series: Annotated[str, typer.Option(help="Series display name")] = "My Series",
    slug: Annotated[Optional[str], typer.Option(help="Output slug [a-z0-9_-]")] = None,
    variant: Annotated[str, typer.Option(help="recap | sleep")] = "sleep",
    runtime: Annotated[float, typer.Option(help="Target runtime in minutes")] = 90,
    voice: Annotated[str, typer.Option(help="ElevenLabs voice id")] = "",
) -> None:
    """Create a project directory with config.yaml, input/ and a series.yaml next to it."""
    import re

    project_dir = project_dir.resolve()
    if slug is None:
        slug = re.sub(r"[^a-z0-9]+", "-", project_dir.name.lower()).strip("-") or "video"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "input").mkdir(exist_ok=True)
    cfg = project_dir / "config.yaml"
    if cfg.exists():
        console.print(f"[yellow]{cfg} already exists, leaving it alone[/]")
    else:
        cfg.write_text(
            CONFIG_TEMPLATE.format(series=series, slug=slug, variant=variant, runtime=runtime, voice=voice),
            encoding="utf-8",
        )
    series_path = project_dir.parent / "series.yaml"
    if not series_path.exists():
        series_path.write_text(SERIES_TEMPLATE.format(series=series), encoding="utf-8")
    console.print(f"[green]created[/] {project_dir}")
    console.print("next: drop chapter images into input/ (ch012.png or ch012/001.jpg ...), then `recap run <dir> --all`")


@app.command()
def doctor(
    project_dir: Annotated[Optional[Path], typer.Argument(help="Optional project to check")] = None,
    probe_tts: Annotated[bool, typer.Option(help="Make one tiny ElevenLabs request to find the timing path")] = False,
) -> None:
    """Check ffmpeg, filters, encoders, fonts and API keys."""
    from .doctor import run_checks

    report = run_checks(project_dir, probe_tts=probe_tts)
    table = Table(title="recap doctor", show_lines=False)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail")
    for c in report.checks:
        status = "[green]ok[/]" if c.ok else ("[yellow]warn[/]" if c.warn else "[red]FAIL[/]")
        table.add_row(c.name, status, c.detail)
    console.print(table)
    raise typer.Exit(code=0 if report.ok else 1)


def _stage_numbers(stage: Optional[int], all_: bool) -> list[int]:
    if all_:
        return [0, 1, 2, 3, 4, 5]
    if stage is None:
        raise typer.BadParameter("pass --stage N or --all")
    if stage not in range(6):
        raise typer.BadParameter("stage must be 0..5")
    return [stage]


@app.command()
def run(
    project_dir: Annotated[Path, typer.Argument(help="Project directory")],
    stage: Annotated[Optional[int], typer.Option("--stage", "-s", help="Run one stage (0-5)")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Run every stage in order")] = False,
    chapter: Annotated[Optional[list[str]], typer.Option("--chapter", "-c", help="Limit to chapter(s)")] = None,
    force: Annotated[bool, typer.Option(help="Re-run even if the manifest says it is up to date")] = False,
    stub: Annotated[bool, typer.Option(help="Use offline stub providers (no API calls)")] = False,
    reallocate: Annotated[bool, typer.Option(help="Recompute the per-chapter word budget")] = False,
    pin_context: Annotated[bool, typer.Option(help="Ignore upstream story-context changes")] = False,
    refresh_stale: Annotated[bool, typer.Option(help="Re-run stages whose story context changed upstream")] = False,
    no_gate: Annotated[bool, typer.Option(help="Skip the script approval gate for this run")] = False,
) -> None:
    """Run pipeline stages for the selected chapters."""
    from .pipeline import make_context, run_pipeline

    stages = _stage_numbers(stage, all_)
    ctx = make_context(
        project_dir, console, force=force,
        stub=stub,
        reallocate=reallocate,
        pin_context=pin_context,
        refresh_stale=refresh_stale,
        skip_gate=no_gate,
    )
    try:
        run_pipeline(ctx, stages, only=chapter)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]error:[/] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def approve(
    project_dir: Annotated[Path, typer.Argument(help="Project directory")],
    chapter: Annotated[Optional[list[str]], typer.Option("--chapter", "-c", help="Limit to chapter(s)")] = None,
) -> None:
    """Approve the current script.yaml files (hashes them into the manifest) so TTS may run."""
    from .pipeline import make_context, resolve_chapters
    from .stages.review import approve_chapters

    ctx = make_context(project_dir, console)
    chapters = resolve_chapters(ctx, chapter)
    approve_chapters(ctx, chapters)


@app.command()
def status(project_dir: Annotated[Path, typer.Argument(help="Project directory")]) -> None:
    """Show the chapter x stage table, flags, approvals and cost log."""
    from .pipeline import make_context, resolve_chapters

    ctx = make_context(project_dir, console)
    try:
        chapters = resolve_chapters(ctx, None)
    except ValueError:
        chapters = ctx.manifest.chapters()
    m: Manifest = ctx.manifest
    table = Table(title=f"{ctx.config.series} / {ctx.config.slug} ({ctx.config.variant})")
    table.add_column("chapter")
    for s in ALL_STAGES[:-1]:
        table.add_column(s.split("_", 1)[1], justify="center")
    table.add_column("approved", justify="center")
    table.add_column("flags")
    glyph = {"ok": "[green]ok[/]", "stale": "[yellow]stale[/]", "error": "[red]err[/]", "pending": "[dim]-[/]"}
    for ch in chapters:
        row = [ch]
        flags: list[str] = []
        for s in ALL_STAGES[:-1]:
            rec = m.stage(ch, s)
            row.append(glyph.get(rec.get("status", "pending"), rec.get("status", "?")))
            flags.extend(rec.get("flags", []))
        approved = m.approval(ch)
        script_path = ctx.project.script_yaml(ch)
        if approved and script_path.exists():
            from .stages.review import script_hash

            row.append("[green]yes[/]" if script_hash(script_path) == approved else "[yellow]changed[/]")
        else:
            row.append("[dim]no[/]")
        row.append(", ".join(flags))
        table.add_row(*row)
    console.print(table)
    v = m.stage(None, "05_assemble")
    console.print(f"assemble: {v.get('status', 'pending')}   total spend: ${m.total_usd():.2f}")
    b = m.budget
    if b:
        console.print(f"budget: {b.get('total_words')} words total ({b.get('wpm')} wpm), per chapter: {b.get('per_chapter')}")


if __name__ == "__main__":
    app()
