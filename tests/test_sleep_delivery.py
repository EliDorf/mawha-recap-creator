import json

import pytest

from mawha_recap.config import VideoConfig
from mawha_recap.stages.tts import delivery_text


def test_whisper_direction_is_explicit_and_not_duplicated():
    cfg = VideoConfig(series="Sleep", slug="sleep", tts={"delivery": "whisper"})
    assert delivery_text("A quiet story.", cfg) == "[whispers] A quiet story."
    assert delivery_text("[whispers] A quiet story.", cfg) == "[whispers] A quiet story."
    cfg.voice.model = "eleven_multilingual_v2"
    with pytest.raises(ValueError, match="requires"):
        delivery_text("A quiet story.", cfg)
    cfg.tts.delivery = "neutral"
    assert delivery_text("A quiet story.", cfg) == "A quiet story."


def test_sleep_audition_uses_whisper_tag(project_factory):
    from typer.testing import CliRunner

    from mawha_recap.cli import app

    root = project_factory(variant="sleep")
    result = CliRunner().invoke(app, ["audition", str(root), "--text", "A quiet story.", "--stub"])
    assert result.exit_code == 0, result.output
    alignment = json.loads(next((root / "work/auditions").glob("*.json")).read_text())
    assert alignment["text"].startswith("[whispers] ")
