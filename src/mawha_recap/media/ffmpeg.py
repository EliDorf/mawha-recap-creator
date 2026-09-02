"""Thin, cross-platform wrapper around the ffmpeg / ffprobe binaries."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


class FFmpegNotFound(FFmpegError):
    pass


def find_binary(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_BINARY")
    if env and Path(env).exists():
        return env
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegNotFound(
        f"{name} not found on PATH. Install it (macOS: `brew install ffmpeg`, Windows: `winget install Gyan.FFmpeg`) "
        f"or set {name.upper()}_BINARY to the executable."
    )


def ffmpeg() -> str:
    return find_binary("ffmpeg")


def ffprobe() -> str:
    return find_binary("ffprobe")


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg/ffprobe with list args (never a shell). Raises FFmpegError with the stderr tail."""
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"command failed ({proc.returncode}): {' '.join(args[:6])} ...\n{tail}")
    return proc


def run_raw(args: list[str], input_bytes: bytes | None = None, cwd: Path | None = None) -> bytes:
    """Run ffmpeg and return raw stdout bytes (for PCM piping)."""
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=None if input_bytes is not None else subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.decode("utf-8", "replace").strip().splitlines()[-25:])
        raise FFmpegError(f"command failed ({proc.returncode}): {' '.join(args[:6])} ...\n{tail}")
    return proc.stdout


def ffmpeg_cmd(*extra: str) -> list[str]:
    return [ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error", *extra]


@lru_cache(maxsize=1)
def version() -> str:
    out = run([ffmpeg(), "-version"]).stdout
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else "unknown"


def major_version() -> int | None:
    m = re.match(r"n?(\d+)", version())
    return int(m.group(1)) if m else None


@lru_cache(maxsize=1)
def filters() -> set[str]:
    out = run([ffmpeg(), "-hide_banner", "-filters"]).stdout
    names: set[str] = set()
    for line in out.splitlines():
        m = re.match(r"\s*[A-Z.]{3,4}\s+(\S+)\s+", line)
        if m:
            names.add(m.group(1))
    return names


@lru_cache(maxsize=1)
def encoders() -> set[str]:
    out = run([ffmpeg(), "-hide_banner", "-encoders"]).stdout
    names: set[str] = set()
    for line in out.splitlines():
        m = re.match(r"\s*[A-Z.]{6}\s+(\S+)\s+", line)
        if m:
            names.add(m.group(1))
    return names


def has_filter(name: str) -> bool:
    return name in filters()


def probe(path: Path | str, cwd: Path | None = None) -> dict:
    proc = run(
        [ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        cwd=cwd,
    )
    return json.loads(proc.stdout)


def duration(path: Path | str, cwd: Path | None = None) -> float:
    info = probe(path, cwd=cwd)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    if d is None:
        raise FFmpegError(f"could not read duration of {path}")
    return float(d)


HW_ENCODERS = ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]


def _encoder_works(name: str) -> bool:
    """A build can list a hardware encoder without the hardware being present; try a tiny encode."""
    args = [
        ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=128x128:r=30:d=0.2",
        "-frames:v", "3", *encoder_args(name, 20, "fast"), "-f", "null", "-",
    ]
    try:
        return run(args, check=False).returncode == 0
    except FFmpegError:
        return False


@lru_cache(maxsize=8)
def pick_encoder(setting: str) -> str:
    """Resolve the `render.encoder` knob to an encoder that exists *and works* in this environment."""
    avail = encoders()
    if setting != "auto":
        if setting not in avail:
            raise FFmpegError(f"encoder {setting!r} not available in this ffmpeg build")
        return setting
    for name in HW_ENCODERS:
        if name in avail and _encoder_works(name):
            return name
    if "libx264" in avail:
        return "libx264"
    raise FFmpegError("no H.264 encoder available (need libx264 or a working hardware encoder)")


def encoder_args(encoder: str, crf: int, preset: str) -> list[str]:
    """Quality flags per encoder family. CRF-like quality is mapped for hardware encoders."""
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-q:v", str(max(1, min(100, 100 - crf * 2)))]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf)]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-c:v", encoder]


# ---------------------------------------------------------------- loudnorm two-pass

_JSON_TAIL_RE = re.compile(r"\{[^{}]*\}\s*$", re.S)


def loudnorm_measure(
    input_path: str, target_i: float, lra: float = 11.0, tp: float = -1.5, cwd: Path | None = None
) -> dict:
    af = f"aformat=channel_layouts=stereo,loudnorm=I={target_i}:LRA={lra}:TP={tp}:print_format=json"
    proc = run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-i", input_path, "-af", af, "-f", "null", "-"],
        cwd=cwd,
    )
    m = _JSON_TAIL_RE.search(proc.stderr)
    if not m:
        raise FFmpegError("loudnorm measurement did not print JSON stats")
    return json.loads(m.group(0))


def loudnorm_apply(
    input_path: str,
    output_path: str,
    target_i: float,
    measured: dict,
    lra: float = 11.0,
    tp: float = -1.5,
    sample_rate: int = 48000,
    cwd: Path | None = None,
) -> dict:
    af = (
        "aformat=channel_layouts=stereo,"
        f"loudnorm=I={target_i}:LRA={lra}:TP={tp}"
        f":measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}"
        f":measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured.get('target_offset', 0)}:linear=true:print_format=json"
    )
    proc = run(
        [
            ffmpeg(),
            "-y",
            "-hide_banner",
            "-nostdin",
            "-i",
            input_path,
            "-af",
            af,
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            output_path,
        ],
        cwd=cwd,
    )
    m = _JSON_TAIL_RE.search(proc.stderr)
    return json.loads(m.group(0)) if m else {}
