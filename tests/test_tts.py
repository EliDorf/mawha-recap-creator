import json

import pytest

from mawha_recap.media import ffmpeg as ff
from mawha_recap.models import ScriptLine
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.review import approve_chapters
from mawha_recap.stages.tts import load_timeline, segment_lines


def _line(i, beat, n=40):
    return ScriptLine(id=f"l{i}", beat=beat, panel_ids=[f"p{i}"], text="x" * n)


def test_segment_lines_respects_budget_and_beats():
    lines = [_line(i, "b1" if i < 3 else "b2", n=20) for i in range(6)]
    # 22 chars per line: a size-only rule would give [4, 2]; the beat change after 60% of the budget breaks earlier
    assert [len(g) for g in segment_lines(lines, max_chars=100)] == [3, 3]
    assert [len(g) for g in segment_lines([_line(i, "b1", n=20) for i in range(6)], max_chars=100)] == [4, 2]
    groups = segment_lines(lines, max_chars=10_000)
    assert len(groups) == 1
    assert segment_lines([], 100) == []


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_tts_stage_gate_and_timeline(project_factory):
    root = project_factory()
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    with pytest.raises(RuntimeError, match="not approved"):
        run_pipeline(ctx, [3], only=["ch001"])
    assert ctx.manifest.stage("ch001", "03_tts")["status"] == "error"
    approve_chapters(ctx, ["ch001", "ch002"])
    run_pipeline(ctx, [3])
    for ch in ("ch001", "ch002"):
        tl = load_timeline(ctx.project.timeline_json(ch))
        vo = ctx.project.vo_wav(ch)
        assert abs(ff.duration(vo) - tl.duration) < 0.05
        assert tl.lead_in == 1.0
        assert tl.lines[0].start >= tl.lead_in
        prev_end = 0.0
        for line in tl.lines:
            assert line.start >= prev_end
            assert line.end > line.start
            assert line.words and line.words[0].s >= line.start - 1e-6 and line.words[-1].e <= line.end + 1e-6
            prev_end = line.end
        gaps = [b.start - a.end for a, b in zip(tl.lines, tl.lines[1:], strict=False)]
        assert all(g >= -0.02 for g in gaps)  # preserve natural pauses without added silence
        assert tl.duration >= tl.lines[-1].end + 0.6 - 0.01  # chapter tail
        assert abs(float(tl.loudness["output_i"]) - (-22.0)) < 1.5
        assert tl.segments and all(s.timing_source == "stub" for s in tl.segments)
    # idempotent
    before = ctx.manifest.stage("ch001", "03_tts")["updated_at"]
    run_pipeline(make_context(root, stub=True), [3])
    assert make_context(root).manifest.stage("ch001", "03_tts")["updated_at"] == before
    # editing one line re-synthesises only that line's segment and re-approval is required
    sp = ctx.project.script_yaml("ch001")
    text = sp.read_text(encoding="utf-8")
    sp.write_text(text.replace("The hunter steps", "The hunter strides", 1), encoding="utf-8")
    ctx2 = make_context(root, stub=True)
    with pytest.raises(RuntimeError, match="not approved"):
        run_pipeline(ctx2, [3], only=["ch001"])
    approve_chapters(ctx2, ["ch001"])
    run_pipeline(ctx2, [3], only=["ch001"])
    assert ctx2.manifest.stage("ch001", "03_tts")["status"] == "ok"
    # --no-gate bypasses approval
    sp.write_text(text, encoding="utf-8")
    ctx3 = make_context(root, stub=True, skip_gate=True)
    run_pipeline(ctx3, [3], only=["ch001"])
    assert ctx3.manifest.stage("ch001", "03_tts")["status"] == "ok"
    tl_json = json.loads(ctx.project.timeline_json("ch001").read_text(encoding="utf-8"))
    assert set(tl_json) >= {"chapter", "duration", "lead_in", "lines", "segments", "loudness"}
