"""ElevenLabs TTS with two timing paths (with-timestamps, else forced alignment) and an offline stub."""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import VideoConfig
from ..text.alignment import CharAlignment


@dataclass
class TTSResult:
    audio_path: Path
    alignment: CharAlignment
    aligned_text: str  # the text the alignment refers to (tags stripped on the forced-alignment path)
    timing_source: str
    chars: int
    request_id: str | None = None


def _alignment_from_model(al: Any) -> CharAlignment | None:
    chars = getattr(al, "characters", None)
    if not chars:
        return None
    return CharAlignment(
        characters=list(chars),
        starts=[float(x) for x in al.character_start_times_seconds],
        ends=[float(x) for x in al.character_end_times_seconds],
    )


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, cfg: VideoConfig):
        from elevenlabs import ElevenLabs

        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set (use --stub for an offline run)")
        self.cfg = cfg
        self.client = ElevenLabs(api_key=key, timeout=240)
        self.timing_ok: bool | None = None  # None = unknown, decided on first request
        self._max_chars: int | None = None
        self.warnings: list[str] = []

    # ---- limits ----------------------------------------------------------------
    def max_chars(self) -> int:
        if self._max_chars is None:
            limit = self.cfg.tts.max_chars
            try:
                for m in self.client.models.list():
                    if getattr(m, "model_id", None) == self.cfg.voice.model:
                        cap = getattr(m, "maximum_text_length_per_request", None)
                        if cap:
                            limit = min(limit, int(cap) - 100)
            except Exception as e:  # noqa: BLE001
                self.warnings.append(f"could not read model limits: {e}")
            self._max_chars = max(200, limit)
        return self._max_chars

    def _voice_settings(self) -> Any:
        from elevenlabs import VoiceSettings

        d = self.cfg.voice.settings.as_dict()
        return VoiceSettings(**d) if d else None

    # ---- synthesis ---------------------------------------------------------------
    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        align_text: str,
        prev_text: str | None = None,
        next_text: str | None = None,
    ) -> TTSResult:
        voice = self.cfg.voice
        common: dict[str, Any] = {
            "voice_id": voice.id,
            "text": text,
            "model_id": voice.model,
            "output_format": voice.output_format,
        }
        vs = self._voice_settings()
        if vs is not None:
            common["voice_settings"] = vs
        if voice.language_code:
            common["language_code"] = voice.language_code
        # Eleven v3 rejects text-context parameters on both synthesis endpoints.
        if prev_text and voice.model != "eleven_v3":
            common["previous_text"] = prev_text[-600:]
        if next_text and voice.model != "eleven_v3":
            common["next_text"] = next_text[:600]
        out_path.parent.mkdir(parents=True, exist_ok=True)

        audio: bytes | None = None
        if self.cfg.tts.prefer_timestamps and self.timing_ok is not False:
            try:
                resp = self.client.text_to_speech.convert_with_timestamps(**common)
                audio = base64.b64decode(resp.audio_base_64)
                out_path.write_bytes(audio)
                al = _alignment_from_model(getattr(resp, "alignment", None))
                if al is not None:
                    self.timing_ok = True
                    return TTSResult(out_path, al, text, "with_timestamps", len(text))
                self.warnings.append("with-timestamps returned no alignment; using forced alignment")
                self.timing_ok = False
            except Exception as e:  # noqa: BLE001
                self.timing_ok = False
                self.warnings.append(f"with-timestamps unavailable for {voice.model} ({e}); using forced alignment")
        if audio is None:
            audio = b"".join(self.client.text_to_speech.convert(**common))
            out_path.write_bytes(audio)
        fa = self.client.forced_alignment.create(file=(out_path.name, audio, "audio/mpeg"), text=align_text)
        al = CharAlignment(
            characters=[c.text for c in fa.characters],
            starts=[float(c.start) for c in fa.characters],
            ends=[float(c.end) for c in fa.characters],
        )
        return TTSResult(out_path, al, align_text, "forced_alignment", len(text))


def probe_timing_path() -> str:
    """One tiny request to learn which timing path the configured model supports (used by `doctor`)."""
    from elevenlabs import ElevenLabs

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    model = os.environ.get("ELEVENLABS_MODEL", "eleven_v3")
    client = ElevenLabs(api_key=key, timeout=120)
    try:
        resp = client.text_to_speech.convert_with_timestamps(voice_id=voice_id, text="Testing timestamps.", model_id=model)
        if getattr(resp, "alignment", None) and resp.alignment.characters:
            return f"with_timestamps ({model})"
    except Exception as e:  # noqa: BLE001
        return f"forced_alignment ({model}: with-timestamps failed: {str(e)[:120]})"
    return f"forced_alignment ({model}: no alignment returned)"


# ----------------------------------------------------------------- stub


class StubTTS:
    """Offline stand-in: a soft tone whose length follows the text, with uniform character timing."""

    name = "stub"
    CHARS_PER_SECOND = 15.0

    def __init__(self, cfg: VideoConfig):
        self.cfg = cfg
        self.warnings: list[str] = []

    def max_chars(self) -> int:
        return self.cfg.tts.max_chars

    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        align_text: str,
        prev_text: str | None = None,
        next_text: str | None = None,
    ) -> TTSResult:
        del prev_text, next_text
        sr = 24000
        duration = max(0.6, len(align_text) / self.CHARS_PER_SECOND) + 0.25  # a little leading/trailing room
        n = int(duration * sr)
        # a quiet 220 Hz tone with 40 ms fades so loudnorm has something to measure
        frames = bytearray()
        amp = 0.05 * 32767
        fade = int(0.04 * sr)
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade) if fade else 1.0
            v = int(amp * env * math.sin(2 * math.pi * 220 * i / sr))
            frames += struct.pack("<h", v)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(frames))
        chars = list(text)
        per = (duration - 0.2) / max(1, len(chars))
        starts = [round(0.1 + i * per, 4) for i in range(len(chars))]
        ends = [round(0.1 + (i + 1) * per, 4) for i in range(len(chars))]
        return TTSResult(out_path, CharAlignment(chars, starts, ends), text, "stub", len(text))


def make_tts(cfg: VideoConfig, stub: bool = False) -> ElevenLabsTTS | StubTTS:
    return StubTTS(cfg) if stub else ElevenLabsTTS(cfg)


def save_alignment(path: Path, res: TTSResult, text: str) -> None:
    path.write_text(
        json.dumps(
            {
                "text": text,
                "aligned_text": res.aligned_text,
                "characters": res.alignment.characters,
                "starts": res.alignment.starts,
                "ends": res.alignment.ends,
                "timing_source": res.timing_source,
                "chars": res.chars,
                "request_id": res.request_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_alignment(path: Path) -> tuple[CharAlignment, str, str, int]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return CharAlignment(d["characters"], d["starts"], d["ends"]), d["aligned_text"], d["timing_source"], int(d.get("chars", 0))
