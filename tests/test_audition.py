from typer.testing import CliRunner

from mawha_recap.cli import app
from mawha_recap.pipeline import make_context


def test_audition_is_cached_and_keeps_project_voice(project_factory):
    root = project_factory()
    cfg = (root / "config.yaml").read_bytes()
    args = [
        "audition",
        str(root),
        "--voice",
        "sample-voice",
        "--text",
        "A short voice sample for the story.",
        "--stub",
    ]
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(list((root / "work/auditions").glob("*.wav"))) == 1
    assert len(make_context(root).manifest.data["costs"]) == 1
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(make_context(root).manifest.data["costs"]) == 1
    assert (root / "config.yaml").read_bytes() == cfg


def test_init_defaults_to_whispered_sleep_channel(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_SLEEP_VOICE_ID", raising=False)
    root = tmp_path / "new-project"
    result = CliRunner().invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    cfg = make_context(root).config
    assert cfg.variant == "sleep" and cfg.target_runtime_min == 90
    assert cfg.opening.enabled and not cfg.render.chapter_cards
    assert cfg.style.line_gap_s == 0
    assert cfg.tts.line_separator == " " and not cfg.tts.segment_on_beat
    assert cfg.voice.id == "80POrI92mU3QZbdwHl4r"
    assert cfg.tts.delivery == "whisper"
    assert cfg.channel.tagline == "Manhwa's to fall asleep to"
