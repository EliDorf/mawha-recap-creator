from pathlib import Path

import yaml

from mawha_recap.models import Beat, BeatOut, BeatSheet, BeatSheetOut, ScriptLineOut, ScriptOut
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.beats import load_beats, normalize_beats
from mawha_recap.stages.review import approve_chapters, script_hash
from mawha_recap.stages.script import load_script, normalize_script, save_script_yaml
from mawha_recap.stages.segment import load_panels


def test_normalize_beats_covers_every_panel():
    ids = [f"ch001_p{i:03d}" for i in range(1, 8)]
    out = BeatSheetOut(
        beats=[
            BeatOut(panel_ids=["ch001_p002", "ch001_p001", "ch001_p999"], what_happens="a", dialogue_gist="", emotional_beat="x", importance=9),
            BeatOut(panel_ids=["ch001_p004", "ch001_p002"], what_happens="b", dialogue_gist="", emotional_beat="y", importance=0),
        ],
        chapter_summary="s",
        new_characters=[],
        open_threads=[],
        skip_panels=["ch001_p007", "ch001_p007", "nope"],
    )
    beats, skip, warnings = normalize_beats(out, ids)
    assert skip == ["ch001_p007"]
    covered = [pid for b in beats for pid in b.panel_ids]
    assert covered == ids[:-1]  # every non-skipped panel exactly once, in order
    assert [b.id for b in beats] == ["b01", "b02", "b03", "b04"]
    assert beats[0].panel_ids == ["ch001_p001", "ch001_p002"] and beats[0].importance == 3
    assert beats[1].panel_ids == ["ch001_p003"] and beats[1].importance == 1  # visual-only fill
    assert any("unknown" in w for w in warnings) and any("no beat" in w for w in warnings)


def test_normalize_script_coverage_and_ids():
    sheet = BeatSheet(
        chapter="ch001", model="m", prompt_version="v", context_hash="h",
        beats=[Beat(id="b01", panel_ids=["ch001_p001", "ch001_p002"], what_happens="a"),
               Beat(id="b02", panel_ids=["ch001_p003", "ch001_p004", "ch001_p005"], what_happens="b")],
        chapter_summary="s",
    )
    out = ScriptOut(lines=[
        ScriptLineOut(beat="b01", panel_ids=["ch001_p002", "ch001_p001"], text="  One   two. ", tts_text="One two."),
        ScriptLineOut(beat="zzz", panel_ids=["ch001_p004", "ch001_p777"], text="Three.", tts_text="Three... [pause]"),
        ScriptLineOut(beat="b02", panel_ids=[], text="Four.", tts_text=""),
    ])
    lines, warnings = normalize_script(out, sheet, "ch001")
    assert [line.id for line in lines] == ["ch001_l001", "ch001_l002", "ch001_l003"]
    assert lines[0].panel_ids == ["ch001_p001", "ch001_p002", "ch001_p003"]  # p003 attached to the preceding line
    assert lines[0].text == "One two." and lines[0].tts_text is None
    assert lines[1].beat == "b02" and lines[1].tts_text == "Three... [pause]"
    all_ids = [pid for line in lines for pid in line.panel_ids]
    assert set(all_ids) >= {"ch001_p003", "ch001_p004", "ch001_p005"}  # uncovered panels attached
    assert lines[2].panel_ids  # empty line holds on a panel
    assert warnings


def test_stage2_stub_end_to_end(project_factory):
    root = project_factory()
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    for ch in ("ch001", "ch002"):
        sheet = load_beats(ctx.project.beats_json(ch))
        panels = [p.id for p in load_panels(ctx.project.panels_json(ch)).panels]
        assert [pid for b in sheet.beats for pid in b.panel_ids] == panels
        script = load_script(ctx.project.script_yaml(ch))
        assert [pid for line in script.lines for pid in line.panel_ids] == panels
        assert script.budget_words == ctx.manifest.budget["per_chapter"][ch]
        assert script.word_count() > 0
        assert ctx.manifest.stage(ch, "02_beats")["status"] == "ok"
        assert ctx.manifest.stage(ch, "02_script")["status"] == "ok"
    assert ctx.manifest.budget["total_words"] == round(3.0 * 130)
    assert ctx.project.review_html.exists()
    html = ctx.project.review_html.read_text(encoding="utf-8")
    assert "Chapter 1" in html and "Chapter 2" in html and "not approved" in html
    # ch002's context depends on ch001's summary
    assert sheet.context_hash != load_beats(ctx.project.beats_json("ch001")).context_hash

    # idempotent second run
    ctx2 = make_context(root, stub=True)
    before = ctx2.manifest.stage("ch002", "02_script")["updated_at"]
    run_pipeline(ctx2, [2])
    assert ctx2.manifest.stage("ch002", "02_script")["updated_at"] == before
    assert "stale" not in ctx2.manifest.stage("ch002", "02_beats")

    # approve, then edit content -> changed; reformat only -> unchanged
    approve_chapters(ctx2, ["ch001", "ch002"])
    sp = ctx2.project.script_yaml("ch001")
    h = script_hash(sp)
    assert ctx2.manifest.approval("ch001") == h
    data = yaml.safe_load(sp.read_text(encoding="utf-8"))
    sp.write_text(yaml.safe_dump(data, sort_keys=True, width=40, default_flow_style=False), encoding="utf-8")
    assert script_hash(sp) == h
    data["lines"][0]["text"] = "Something else entirely."
    sp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert script_hash(sp) != h


def test_stale_marking_when_upstream_context_changes(project_factory):
    root = project_factory()
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    # simulate an edited chapter-1 summary (as if beats were regenerated with a different result)
    p = ctx.project.beats_json("ch001")
    sheet = load_beats(p)
    sheet.chapter_summary = "A completely different summary."
    p.write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    ctx2 = make_context(root, stub=True)
    run_pipeline(ctx2, [2])
    assert ctx2.manifest.stage("ch002", "02_beats").get("stale") is True
    assert ctx2.manifest.stage("ch002", "02_script").get("stale") is True
    # ch001 script itself re-ran because its beats file changed
    assert ctx2.manifest.stage("ch001", "02_script")["status"] == "ok"
    # --refresh-stale re-runs the stale chapter and clears the flag
    ctx3 = make_context(root, stub=True, refresh_stale=True)
    run_pipeline(ctx3, [2])
    assert "stale" not in ctx3.manifest.stage("ch002", "02_beats")
    assert "stale" not in ctx3.manifest.stage("ch002", "02_script")
    # pin-context: flag suppressed even if context differs
    p.write_text(sheet.model_copy(update={"chapter_summary": "Yet another."}).model_dump_json(indent=2), encoding="utf-8")
    ctx4 = make_context(root, stub=True, pin_context=True)
    run_pipeline(ctx4, [2])
    assert "stale" not in ctx4.manifest.stage("ch002", "02_beats")


def test_yaml_roundtrip(tmp_path: Path):
    from mawha_recap.models import Script, ScriptLine

    s = Script(chapter="ch009", prompt_version="v", model="m", budget_words=100,
               lines=[ScriptLine(id="ch009_l001", beat="b01", panel_ids=["ch009_p001"], text="Hi: there, \"quoted\" — ok.", tts_text="Hi... there. [pause]", pause_after=1.2)])
    save_script_yaml(s, tmp_path / "s.yaml")
    back = load_script(tmp_path / "s.yaml")
    assert back == s
