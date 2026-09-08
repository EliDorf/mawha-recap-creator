import json

import pytest
import yaml

from mawha_recap.media import ffmpeg as ff
from mawha_recap.models import Script, ScriptLine
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.review import approve_chapters, script_hash
from mawha_recap.stages.tts import load_timeline, segment_lines


def test_connected_passage_does_not_break_on_beat():
    lines = [ScriptLine(id=str(i), beat=str(i), panel_ids=["p"], text="a" * 30) for i in range(3)]
    assert len(segment_lines(lines, 100)) == 2
    assert len(segment_lines(lines, 100, segment_on_beat=False)) == 1


def test_no_cards_and_cropped_opening_render(project_factory):
    root = project_factory({"ch001": [500, 600]}, variant="recap")
    path = root / "config.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["render"].update(chapter_cards=False, width=320, height=180, oversample=1, encoder="libx264")
    cfg["style"] = {"subtitles": {"burn": False}, "line_gap_s": 0}
    cfg["tts"] = {"line_separator": " ", "segment_on_beat": False}
    path.write_text(yaml.safe_dump(cfg))
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1])
    script = Script(
        chapter="ch001",
        model="test",
        prompt_version="test",
        budget_words=20,
        lines=[
            ScriptLine(
                id="hook",
                beat="hook",
                panel_ids=["ch001_p001"],
                text="The door opens.",
                framing="cover",
                visual_crop=(10, 20, 400, 250),
            ),
            ScriptLine(id="story", beat="story", panel_ids=["ch001_p002"], text="He steps into the room."),
        ],
    )
    p = ctx.project.script_yaml("ch001")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(script.model_dump()))
    original = script_hash(p)
    script.lines[0].visual_crop = (20, 20, 400, 250)
    p.write_text(yaml.safe_dump(script.model_dump()))
    assert script_hash(p) != original
    valid_crop = script.lines[0].visual_crop
    script.lines[0].visual_crop = (0, 0, 99999, 250)
    p.write_text(yaml.safe_dump(script.model_dump()))
    with pytest.raises(ValueError, match="crop outside"):
        approve_chapters(ctx, ["ch001"])
    script.lines[0].visual_crop = valid_crop
    p.write_text(yaml.safe_dump(script.model_dump()))
    approve_chapters(ctx, ["ch001"])
    run_pipeline(ctx, [3, 4])
    tl = load_timeline(ctx.project.timeline_json("ch001"))
    assert tl.lead_in == 0
    assert len(tl.segments) == 1
    assert tl.lines[0].visual_crop == (20, 20, 400, 250)
    plan = json.loads((ctx.project.render_dir("ch001") / "ch001.plan.json").read_text())
    assert plan[0]["mode"] == "cover"
    assert all(s["mode"] != "card" for s in plan)
    assert abs(ff.duration(ctx.project.chapter_mp4("ch001")) - tl.duration) < 0.1
