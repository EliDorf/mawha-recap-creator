from types import SimpleNamespace

import pytest

from mawha_recap.config import VideoConfig
from mawha_recap.providers.elevenlabs import ElevenLabsTTS


@pytest.mark.parametrize("model,has_context", [("eleven_v3", False), ("eleven_multilingual_v2", True)])
def test_context_only_sent_to_supported_models(tmp_path, model, has_context):
    calls = []

    def convert(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            audio_base_64="YQ==",
            alignment=SimpleNamespace(
                characters=["a"], character_start_times_seconds=[0], character_end_times_seconds=[1]
            ),
        )

    tts = ElevenLabsTTS.__new__(ElevenLabsTTS)
    tts.cfg = VideoConfig(series="Test", slug="test", voice={"id": "test", "model": model})
    tts.client = SimpleNamespace(text_to_speech=SimpleNamespace(convert_with_timestamps=convert))
    tts.timing_ok = None
    tts.warnings = []
    tts.synthesize("a", tmp_path / "audio.mp3", align_text="a", prev_text="before", next_text="after")
    assert ("previous_text" in calls[0]) is has_context
    assert ("next_text" in calls[0]) is has_context
