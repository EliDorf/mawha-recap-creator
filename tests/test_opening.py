import json

import pytest
import yaml

from mawha_recap.models import OpeningOut, OpeningShotOut, Script, ScriptLine
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.opening import normalize_opening
from mawha_recap.stages.review import approve_chapters, script_hash
from mawha_recap.stages.script import load_script, load_script_for_video, save_script_yaml


def test_opening_cached_once_and_reviewed_with_body(project_factory):
    root = project_factory({"ch001": [500, 600], "ch002": [500, 600]}, variant="recap", runtime=0.2)
    cfg = yaml.safe_load((root / "config.yaml").read_text())
    cfg["opening"] = {"enabled": True, "max_panels": 6}
    cfg["render"].update(chapter_cards=False, width=320, height=180, oversample=1, encoder="libx264")
    cfg["style"] = {"subtitles": {"burn": False}}
    (root / "config.yaml").write_text(yaml.safe_dump(cfg))
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    opening = load_script(ctx.project.opening_yaml)
    assert 3 <= len(opening.lines) <= 5
    assert all(line.framing == "cover" and line.visual_crop for line in opening.lines)
    p = ctx.project.script_yaml("ch001")
    assert len(load_script_for_video(p).lines) == len(load_script(p).lines) + len(opening.lines)
    q = ctx.project.script_yaml("ch002")
    assert load_script_for_video(q) == load_script(q)
    before = ctx.project.opening_yaml.read_bytes()
    count = len(ctx.manifest.data["costs"])
    run_pipeline(make_context(root, stub=True), [2])
    assert ctx.project.opening_yaml.read_bytes() == before
    assert len(make_context(root).manifest.data["costs"]) == count
    approve_chapters(ctx, ["ch001"])
    approved = script_hash(p)
    opening.lines[0].text += " Something changes."
    save_script_yaml(opening, ctx.project.opening_yaml)
    assert script_hash(p) != approved
    assert "cover" in ctx.project.review_html.read_text()
    approve_chapters(ctx, ["ch001", "ch002"])
    run_pipeline(ctx, [3, 4, 5])
    assert ctx.project.final_mp4.exists()
    assert ctx.manifest.stage(None, "05_assemble")["status"] == "ok"
    cfg["opening"]["enabled"] = False
    (root / "config.yaml").write_text(yaml.safe_dump(cfg))
    assert load_script_for_video(p) == load_script(p)


def test_opening_rejects_unavailable_panels_and_bad_crops():
    pool = [{"panel_id": "ch001_p001", "w": 700, "h": 600}, {"panel_id": "ch001_p002", "w": 700, "h": 600}]
    out = OpeningOut(
        shots=[
            OpeningShotOut(
                panel_id=pool[i % 2]["panel_id"],
                text="The traveler must face a dangerous obstacle to find the answer.",
                crop=(0, 0, 1000, 1000),
            )
            for i in range(3)
        ]
    )
    assert len(normalize_opening(out, pool, "ch001", 40)) == 3
    out.shots[0].crop = (900, 0, 200, 500)
    with pytest.raises(ValueError, match="out of bounds"):
        normalize_opening(out, pool, "ch001", 40)
    out.shots[0].crop = (0, 0, 1000, 1000)
    out.shots[0].panel_id = "ch001_p999"
    with pytest.raises(ValueError, match="unknown/skipped"):
        normalize_opening(out, pool, "ch001", 40)


def test_opening_can_render_a_later_chapters_panel(project_factory):
    root = project_factory({"ch001": [500], "ch002": [600]}, variant="recap")
    cfg = yaml.safe_load((root / "config.yaml").read_text())
    cfg.update(opening={"enabled": True}, style={"subtitles": {"burn": False}, "line_gap_s": 0})
    cfg["render"].update(chapter_cards=False, width=320, height=180, oversample=1, encoder="libx264")
    (root / "config.yaml").write_text(yaml.safe_dump(cfg))
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1])
    save_script_yaml(
        Script(
            chapter="ch001",
            prompt_version="test",
            model="test",
            budget_words=10,
            lines=[ScriptLine(id="body", beat="b1", panel_ids=["ch001_p001"], text="The story starts here.")],
        ),
        ctx.project.script_yaml("ch001"),
    )
    save_script_yaml(
        Script(
            chapter="ch001",
            prompt_version="test",
            model="test",
            budget_words=10,
            lines=[
                ScriptLine(
                    id="hook",
                    beat="opening",
                    panel_ids=["ch002_p001"],
                    text="A dangerous test is waiting.",
                    framing="cover",
                    visual_crop=(0, 0, 400, 300),
                )
            ],
        ),
        ctx.project.opening_yaml,
    )
    approve_chapters(ctx, ["ch001"])
    run_pipeline(ctx, [3, 4], only=["ch001"])
    plan = json.loads((ctx.project.render_dir("ch001") / "ch001.plan.json").read_text())
    assert plan[0]["src"].startswith("work/01_panels/ch002/")
    assert plan[0]["mode"] == "cover"
    assert all(s["mode"] != "card" for s in plan)
