"""`recap doctor`: verify the machine can run the pipeline."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .media import ffmpeg as ff
from .project import PACKAGE_FONTS, SUBTITLE_FONT_FILE, TITLE_FONT_FILE


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    warn: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", warn: bool = False) -> None:
        self.checks.append(Check(name, ok, detail, warn))

    @property
    def ok(self) -> bool:
        return all(c.ok or c.warn for c in self.checks)


REQUIRED_FILTERS = ["subtitles", "loudnorm", "xfade", "zoompan", "loop", "gblur", "crop", "scale"]


def run_checks(project_root: Path | None = None, probe_tts: bool = False) -> Report:
    r = Report()
    r.add("python", sys.version_info >= (3, 11), f"{sys.version.split()[0]}")

    try:
        path = ff.ffmpeg()
        ver = ff.version()
        major = ff.major_version()
        r.add("ffmpeg", major is not None and major >= 6, f"{ver} at {path}", warn=(major is None))
        try:
            ff.ffprobe()
            r.add("ffprobe", True, "found")
        except ff.FFmpegNotFound as e:
            r.add("ffprobe", False, str(e))
        flt = ff.filters()
        missing = [f for f in REQUIRED_FILTERS if f not in flt]
        r.add("ffmpeg filters", not missing, "all present" if not missing else f"missing: {', '.join(missing)}")
        enc = ff.encoders()
        hw = [e for e in ff.HW_ENCODERS if e in enc]
        r.add("libx264", "libx264" in enc, "present" if "libx264" in enc else "missing (needed unless a hw encoder is used)")
        r.add("hardware encoders", True, ", ".join(hw) if hw else "none (software encode)", warn=True)
    except ff.FFmpegNotFound as e:
        r.add("ffmpeg", False, str(e))

    for font in (SUBTITLE_FONT_FILE, TITLE_FONT_FILE):
        r.add(f"font {font}", (PACKAGE_FONTS / font).exists(), str(PACKAGE_FONTS))

    for key in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        present = bool(os.environ.get(key))
        r.add(key, present, "set" if present else "not set (needed for real runs; --stub works without)", warn=not present)

    if project_root is not None:
        from .project import Project

        p = Project(project_root)
        r.add("config.yaml", p.config_path.exists(), str(p.config_path))
        if p.config_path.exists():
            try:
                cfg = p.config
                r.add("config valid", True, f"{cfg.series} / {cfg.slug} / {cfg.variant}")
                if not cfg.voice.id:
                    r.add("voice.id", False, "empty (needed for real TTS)", warn=True)
            except Exception as e:  # noqa: BLE001
                r.add("config valid", False, str(e))
        r.add("input/", p.input_dir.exists(), str(p.input_dir))
        if p.input_dir.exists():
            from .stages.ingest import discover_chapters

            found = discover_chapters(p.input_dir)
            r.add("chapters found", bool(found), ", ".join(sorted(found)) or "none", warn=not found)

    if probe_tts:
        from .providers.elevenlabs import probe_timing_path

        try:
            src = probe_timing_path()
            r.add("elevenlabs timing", True, f"timing source: {src}")
        except Exception as e:  # noqa: BLE001
            r.add("elevenlabs timing", False, str(e))
    return r
