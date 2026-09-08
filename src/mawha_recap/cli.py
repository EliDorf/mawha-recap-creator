"""`recap` command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import SLEEP_VOICE_ID
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
        bool | None, typer.Option("--version", callback=_version_cb, is_eager=True, help="Show version")
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

channel:
  tagline: "Manhwa's to fall asleep to"

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
    {voice_guide}
  # wpm / line_gap_s / loudness_lufs / pan / crossfade_s come from the variant preset; override here if needed.
  subtitles: {{burn: true}}

render:
  chapter_cards: false       # continuous video; chapter tracking remains internal
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
    slug: Annotated[str | None, typer.Option(help="Output slug [a-z0-9_-]")] = None,
    variant: Annotated[str, typer.Option(help="recap | sleep")] = "sleep",
    runtime: Annotated[float, typer.Option(help="Target runtime in minutes")] = 90,
    voice: Annotated[str | None, typer.Option(help="ElevenLabs voice id; overrides the saved channel voice")] = None,
) -> None:
    """Create a project directory with config.yaml, input/ and a series.yaml next to it."""
    import os
    import re

    default_voice = (os.environ.get("ELEVENLABS_SLEEP_VOICE_ID") or SLEEP_VOICE_ID) if variant == "sleep" else "CwhRBWXzGAHq8TQ4Fs17"
    voice = voice or default_voice

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
            CONFIG_TEMPLATE.format(series=series, slug=slug, variant=variant, runtime=runtime, voice=voice, voice_guide=("Quiet, intimate storytelling for listeners falling asleep. Natural contractions and connected phrasing. No shouting, promotional delivery, or exaggerated dramatic pauses." if variant == "sleep" else "Conversational storytelling to one listener. Natural contractions and varied sentence lengths. Avoid an announcer delivery.")),
            encoding="utf-8",
        )
    series_path = project_dir.parent / "series.yaml"
    if not series_path.exists():
        series_path.write_text(SERIES_TEMPLATE.format(series=series), encoding="utf-8")
    console.print(f"[green]created[/] {project_dir}")
    console.print("next: drop chapter images into input/ (ch012.png or ch012/001.jpg ...), then `recap run <dir> --all`")


@app.command()
def demo(
    project_dir: Annotated[Path, typer.Argument(help="Directory to create the demo project in")],
    chapters: Annotated[int, typer.Option(help="Number of synthetic chapters")] = 3,
    variant: Annotated[str, typer.Option(help="recap | sleep")] = "sleep",
) -> None:
    """Create a small synthetic project so the whole pipeline can be tried offline with --stub."""
    from .synthetic import write_demo_project

    root = write_demo_project(project_dir.resolve(), chapters=chapters, variant=variant)
    console.print(f"[green]demo project written to[/] {root}")
    console.print("try:")
    console.print(f"  recap run {root} --stage 0 --stub && recap run {root} --stage 1 --stub && recap run {root} --stage 2 --stub")
    console.print(f"  open {root / 'review' / 'review.html'}   # skim the script")
    console.print(f"  recap approve {root} && recap run {root} --all --stub")


@app.command()
def doctor(
    project_dir: Annotated[Path | None, typer.Argument(help="Optional project to check")] = None,
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


def _stage_numbers(stage: int | None, all_: bool) -> list[int]:
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
    stage: Annotated[int | None, typer.Option("--stage", "-s", help="Run one stage (0-5)")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Run every stage in order")] = False,
    chapter: Annotated[list[str] | None, typer.Option("--chapter", "-c", help="Limit to chapter(s)")] = None,
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
    chapter: Annotated[list[str] | None, typer.Option("--chapter", "-c", help="Limit to chapter(s)")] = None,
) -> None:
    """Approve the current script.yaml files (hashes them into the manifest) so TTS may run."""
    from .pipeline import make_context, resolve_chapters
    from .stages.review import approve_chapters

    ctx = make_context(project_dir, console)
    chapters = resolve_chapters(ctx, chapter)
    approve_chapters(ctx, chapters)


@app.command()
def audition(
    project_dir: Annotated[Path, typer.Argument(help="Project whose narration settings to try")],
    voice: Annotated[str | None, typer.Option(help="Try a different ElevenLabs voice without changing config")] = None,
    text: Annotated[str | None, typer.Option(help="Optional spoken sample, at most 800 characters")] = None,
    stub: Annotated[bool, typer.Option(help="Offline tone for testing, not a voice audition")] = False,
) -> None:
    """Create a cached short voice sample before spending on the whole video."""
    from .manifest import canonical_json, short_hash
    from .pipeline import make_context, resolve_chapters
    from .providers.elevenlabs import save_alignment
    from .stages.script import load_script_for_video
    from .stages.tts import delivery_text
    from .text.alignment import strip_tags

    ctx = make_context(project_dir, stub=stub)
    if voice:
        ctx.config.voice.id = voice
    if text is None:
        first = resolve_chapters(ctx, None)[0]
        script = load_script_for_video(ctx.project.script_yaml(first))
        sample_lines = []
        for line in script.lines:
            candidate = line.tts_text or line.text
            if len(" ".join([*sample_lines, candidate])) > 800:
                break
            sample_lines.append(candidate)
            if len(" ".join(sample_lines)) >= 300:
                break
        text = " ".join(sample_lines)
    if not text or len(text) > 800:
        raise typer.BadParameter("provide 1–800 characters with --text, or shorter script lines")
    text = delivery_text(text, ctx.config)
    key = short_hash(canonical_json({"voice": ctx.config.voice.model_dump(), "text": text, "provider": ctx.tts.name}), 12)
    directory = ctx.project.work / "auditions"
    directory.mkdir(parents=True, exist_ok=True)
    ext = "wav" if stub else ("mp3" if "mp3" in ctx.config.voice.output_format else "bin")
    audio = directory / f"voice-{key}.{ext}"
    alignment = directory / f"voice-{key}.json"
    if not audio.exists() or not alignment.exists():
        result = ctx.tts.synthesize(text, audio, align_text=strip_tags(text))
        save_alignment(alignment, result, text)
        ctx.manifest.add_cost(stage="audition", chapter="video", chars=result.chars,
                              usd=0.0 if stub else result.chars / 1000 * ctx.config.tts.usd_per_1k_chars,
                              provider=ctx.tts.name)
        ctx.manifest.save()
    console.print(f"[green]voice sample:[/] {audio}")


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
    opening = m.stage(None, "02_opening")
    console.print(f"opening: {opening.get('status', 'pending')} ({opening.get('shots', 0)} shots)")
    v = m.stage(None, "05_assemble")
    console.print(f"assemble: {v.get('status', 'pending')}   total spend: ${m.total_usd():.2f}")
    b = m.budget
    if b:
        console.print(f"budget: {b.get('total_words')} words total ({b.get('wpm')} wpm), per chapter: {b.get('per_chapter')}")


if __name__ == "__main__":
    app()
