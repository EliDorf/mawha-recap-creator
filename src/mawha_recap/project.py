"""Project layout: every path the pipeline reads or writes comes from here."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import SeriesConfig, VideoConfig, load_series_config, load_video_config

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_FONTS = PACKAGE_DIR / "assets" / "fonts"
SUBTITLE_FONT_FILE = "Inter-SemiBold.ttf"
SUBTITLE_FONT_NAME = "Inter SemiBold"
TITLE_FONT_FILE = "Inter-Bold.ttf"


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.config_path = self.root / "config.yaml"
        self.manifest_path = self.root / "manifest.json"
        self.input_dir = self.root / "input"
        self.work = self.root / "work"
        self.out = self.root / "out"
        self.review_dir = self.root / "review"
        self.fonts_dir = self.root / "assets" / "fonts"
        self._config: VideoConfig | None = None
        self._series: SeriesConfig | None = None

    # ---- config ----------------------------------------------------------
    @property
    def config(self) -> VideoConfig:
        if self._config is None:
            if not self.config_path.exists():
                raise FileNotFoundError(f"no config.yaml in {self.root}")
            self._config = load_video_config(self.config_path)
        return self._config

    @property
    def series_path(self) -> Path | None:
        for cand in (self.root / "series.yaml", self.root.parent / "series.yaml"):
            if cand.exists():
                return cand
        return None

    @property
    def series(self) -> SeriesConfig:
        if self._series is None:
            self._series = load_series_config(self.series_path)
            if not self._series.name:
                self._series.name = self.config.series
        return self._series

    # ---- helpers ---------------------------------------------------------
    def rel(self, path: Path | str) -> str:
        """Posix-style path relative to the project root (stable across OSes)."""
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def ensure_fonts(self) -> Path:
        """Copy the bundled fonts next to the project so ffmpeg's fontsdir is a relative path."""
        self.fonts_dir.mkdir(parents=True, exist_ok=True)
        for f in PACKAGE_FONTS.glob("*.ttf"):
            dst = self.fonts_dir / f.name
            if not dst.exists() or dst.stat().st_size != f.stat().st_size:
                shutil.copyfile(f, dst)
        return self.fonts_dir

    # ---- per-chapter paths -------------------------------------------------
    def ingest_dir(self, ch: str) -> Path:
        return self.work / "00_ingest" / ch

    def strip_path(self, ch: str) -> Path:
        return self.ingest_dir(ch) / "strip.png"

    def ingest_meta(self, ch: str) -> Path:
        return self.ingest_dir(ch) / "meta.json"

    def panels_dir(self, ch: str) -> Path:
        return self.work / "01_panels" / ch

    def panels_json(self, ch: str) -> Path:
        return self.panels_dir(ch) / "panels.json"

    def panels_overrides(self, ch: str) -> Path:
        return self.panels_dir(ch) / "overrides.json"

    def beats_json(self, ch: str) -> Path:
        return self.work / "02_beats" / f"{ch}.beats.json"

    def script_yaml(self, ch: str) -> Path:
        return self.work / "02_script" / f"{ch}.script.yaml"

    def tts_dir(self, ch: str) -> Path:
        return self.work / "03_tts" / ch

    def vo_wav(self, ch: str) -> Path:
        return self.work / "03_tts" / f"{ch}.vo.wav"

    def timeline_json(self, ch: str) -> Path:
        return self.work / "03_tts" / f"{ch}.timeline.json"

    def render_dir(self, ch: str) -> Path:
        return self.work / "04_render" / ch

    def chapter_mp4(self, ch: str) -> Path:
        return self.work / "04_render" / f"{ch}.mp4"

    def chapter_ass(self, ch: str) -> Path:
        return self.work / "04_render" / f"{ch}.ass"

    # ---- video-level outputs -------------------------------------------------
    @property
    def review_html(self) -> Path:
        return self.review_dir / "review.html"

    @property
    def final_mp4(self) -> Path:
        return self.out / f"{self.config.slug}.mp4"

    @property
    def final_srt(self) -> Path:
        return self.out / f"{self.config.slug}.srt"

    @property
    def chapters_txt(self) -> Path:
        return self.out / "chapters.txt"

    @property
    def description_md(self) -> Path:
        return self.out / "description.md"

    @property
    def metadata_json(self) -> Path:
        return self.out / "metadata.json"

    @property
    def thumbnail_jpg(self) -> Path:
        return self.out / "thumbnail.jpg"
