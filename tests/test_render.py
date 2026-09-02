import shutil

import pytest

from mawha_recap.media import ffmpeg as ff
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.review import approve_chapters
from mawha_recap.stages.tts import load_timeline

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def test_render_chapter_matches_timeline(project_factory):
    root = project_factory({"ch001": [500, 1400, 300, 900]})
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    approve_chapters(ctx, ["ch001"])
    run_pipeline(ctx, [3, 4])
    rec = ctx.manifest.stage("ch001", "04_render")
    assert rec["status"] == "ok"
    tl = load_timeline(ctx.project.timeline_json("ch001"))
    info = ff.probe(ctx.project.chapter_mp4("ch001"))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (640, 360)
    assert v["r_frame_rate"] == "30/1" and v["pix_fmt"] == "yuv420p"
    assert a["codec_name"] == "aac" and a["channels"] == 2
    assert abs(float(info["format"]["duration"]) - tl.duration) < 0.1
    assert ctx.project.chapter_ass("ch001").exists()
    clips = list((ctx.project.render_dir("ch001") / "clips").glob("*.mp4"))
    assert len(clips) == rec["clips"]
    # every clip is CFR at the planned frame count
    import json

    plan = json.loads((ctx.project.render_dir("ch001") / "ch001.plan.json").read_text())
    assert sum(p["frames"] for p in plan) == round(tl.duration * 30)
    assert plan[0]["mode"] == "card"
    modes = {p["mode"] for p in plan}
    assert "pan" in modes and "fit" in modes
    # idempotent, and clips are reused when only the timeline changes trivially
    before = rec["updated_at"]
    run_pipeline(make_context(root, stub=True), [4])
    assert make_context(root).manifest.stage("ch001", "04_render")["updated_at"] == before
