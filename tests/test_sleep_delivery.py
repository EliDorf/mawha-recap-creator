import json

import pytest

from mawha_recap.config import VideoConfig
from mawha_recap.stages.tts import delivery_text


def test_whisper_direction_is_explicit_and_not_duplicated():
    cfg = VideoConfig(series="Sleep", slug="sleep", tts={"delivery": "whisper"})
    assert delivery_text("A quiet story.", cfg) == "[whispering] A quiet story."
    assert delivery_text("[whispering] A quiet story.", cfg) == "[whispering] A quiet story."
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
    assert alignment["text"].startswith("[whispering] ")


def test_whisper_refreshes_sentences_without_changing_spoken_words():
    from mawha_recap.text.alignment import strip_tags

    cfg = VideoConfig(series="Sleep", slug="sleep", tts={"delivery": "whisper"})
    prose = 'The door opens. “Who is there?” Nobody answers! He waits... Then steps inside.'
    tagged = delivery_text(prose, cfg)
    assert tagged.count("[whispering]") == 5
    assert strip_tags(tagged) == prose
    assert delivery_text(tagged, cfg) == tagged
    assert delivery_text("[whispers] A quiet story.", cfg) == "[whispering] A quiet story."


def test_whisper_stage_reinforces_later_lines_and_counts_tags(project_factory):
    import yaml

    from mawha_recap.pipeline import make_context, run_pipeline
    from mawha_recap.stages.review import approve_chapters
    from mawha_recap.stages.tts import load_timeline

    root = project_factory({"ch001": [500, 600]}, variant="sleep")
    config_path = root / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    cfg["tts"] = {"max_chars": 4500, "whisper_segment_chars": 160}
    config_path.write_text(yaml.safe_dump(cfg))
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    script_path = ctx.project.script_yaml("ch001")
    script = yaml.safe_load(script_path.read_text())
    script["lines"] = [
        {"id": f"l{i}", "beat": "story", "panel_ids": ["ch001_p001"],
         "text": "The door opens. He steps quietly into the room."}
        for i in range(6)
    ]
    script_path.write_text(yaml.safe_dump(script))
    approve_chapters(ctx, ["ch001"])
    run_pipeline(ctx, [3])
    alignments = list(ctx.project.tts_dir("ch001").glob("*.align.json"))
    assert len(alignments) > 1  # old implementation sent a single opening-tagged request
    for path in alignments:
        text = json.loads(path.read_text())["text"]
        assert len(text) <= 160
        assert text.startswith("[whispering]")
        assert ". [whispering] " in text or text.count(".") <= 1
    timeline = load_timeline(ctx.project.timeline_json("ch001"))
    assert all("[whisper" not in line.text for line in timeline.lines)
    assert all("[whisper" not in word.w for line in timeline.lines for word in line.words)
    assert all(b.start >= a.end for a, b in zip(timeline.lines, timeline.lines[1:], strict=False))


def test_tagged_line_exceeding_budget_fails_before_synthesis():
    from mawha_recap.models import ScriptLine
    from mawha_recap.stages.tts import segment_lines

    line = ScriptLine(id="long", beat="story", panel_ids=["p"], text="A quiet story.")
    with pytest.raises(ValueError, match="split this script line"):
        segment_lines([line], 20, line_texts=["[whispering] A quiet story."])


def test_long_line_stays_intact_within_hard_provider_limit():
    from mawha_recap.models import ScriptLine
    from mawha_recap.stages.tts import segment_lines

    lines = [ScriptLine(id=str(i), beat="story", panel_ids=["p"], text="x" * 800) for i in range(2)]
    assert [len(g) for g in segment_lines(lines, 4500, target_chars=700)] == [1, 1]


def test_init_reuses_channel_voice_without_overriding_explicit_choice(tmp_path, monkeypatch):
    import yaml
    from typer.testing import CliRunner

    from mawha_recap.cli import app

    monkeypatch.setenv("ELEVENLABS_SLEEP_VOICE_ID", "channel-whisper-voice")
    for name, args, expected in [
        ("sleep", [], "channel-whisper-voice"),
        ("explicit", ["--voice", "chosen"], "chosen"),
        ("recap", ["--variant", "recap"], "CwhRBWXzGAHq8TQ4Fs17"),
    ]:
        root = tmp_path / name
        result = CliRunner().invoke(app, ["init", str(root), *args])
        assert result.exit_code == 0, result.output
        assert yaml.safe_load((root / "config.yaml").read_text())["voice"]["id"] == expected
