import json
import shutil
import wave

import numpy as np
import pytest
from PIL import Image

from mawha_recap.media import ffmpeg as ff
from mawha_recap.pipeline import make_context, run_pipeline
from mawha_recap.stages.assemble import chapters_txt
from mawha_recap.stages.review import approve_chapters
from mawha_recap.stages.tts import load_timeline

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def test_chapters_txt_rules():
    txt = chapters_txt([(0.0, "Chapter 1"), (5.0, "Chapter 2"), (65.5, "Chapter 3"), (3700.0, "Chapter 4")])
    lines = txt.strip().splitlines()
    assert lines[0].startswith("0:00:00 ") and len(lines) == 3  # 5 s marker dropped (< 10 s apart), long format
    assert chapters_txt([(0.0, "A"), (90.0, "B")]) == "00:00 A\n01:30 B\n"


def _tone_track(path, seconds=3.0, sr=48000):
    t = np.arange(int(seconds * sr)) / sr
    pcm = (0.2 * 32767 * np.sin(2 * np.pi * 110 * t)).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def test_full_pipeline_and_selective_rerun(project_factory):
    root = project_factory({"ch001": [700, 1200], "ch002": [900, 500, 1100]})
    ctx = make_context(root, stub=True)
    run_pipeline(ctx, [0, 1, 2])
    approve_chapters(ctx, ["ch001", "ch002"])
    run_pipeline(ctx, [3, 4, 5])
    p = ctx.project
    for f in (p.final_mp4, p.final_srt, p.chapters_txt, p.description_md, p.metadata_json, p.thumbnail_jpg):
        assert f.exists(), f
    total = sum(load_timeline(p.timeline_json(ch)).duration for ch in ("ch001", "ch002"))
    assert abs(ff.duration(p.final_mp4) - total) < 0.2
    meta = json.loads(p.metadata_json.read_text(encoding="utf-8"))
    assert meta["title"] == "Test Series | Manhwa's to fall asleep to"
    assert meta["channel_tagline"] == "Manhwa's to fall asleep to"
    assert [c["id"] for c in meta["chapters"]] == ["ch001", "ch002"]
    assert abs(meta["chapters"][1]["offset_s"] - load_timeline(p.timeline_json("ch001")).duration) < 1e-6
    assert p.chapters_txt.read_text(encoding="utf-8").startswith("00:00 Chapter 1\n")
    srt = p.final_srt.read_text(encoding="utf-8")
    assert "[whispers]" not in srt
    n_lines = sum(len(load_timeline(p.timeline_json(ch)).lines) for ch in ("ch001", "ch002"))
    assert srt.count("-->") >= n_lines
    with Image.open(p.thumbnail_jpg) as im:
        assert im.size == (1280, 720)
    assert ctx.manifest.stage(None, "05_assemble")["status"] == "ok"

    # second full run is a no-op everywhere
    stamps = {(ch, s): ctx.manifest.stage(ch, s)["updated_at"] for ch in ("ch001", "ch002") for s in ("03_tts", "04_render")}
    stamps[("video", "05_assemble")] = ctx.manifest.stage(None, "05_assemble")["updated_at"]
    ctx2 = make_context(root, stub=True)
    run_pipeline(ctx2, [0, 1, 2, 3, 4, 5])
    m = make_context(root).manifest
    assert all(m.stage(None if ch == "video" else ch, s)["updated_at"] == v for (ch, s), v in stamps.items())

    # edit one line of ch001 -> approve -> only ch001's tts/render and the assembly re-run
    sp = p.script_yaml("ch001")
    sp.write_text(sp.read_text(encoding="utf-8").replace("The hunter steps", "The hunter strides", 1), encoding="utf-8")
    ctx3 = make_context(root, stub=True)
    approve_chapters(ctx3, ["ch001"])
    run_pipeline(ctx3, [0, 1, 2, 3, 4, 5])
    m = make_context(root).manifest
    assert m.stage("ch001", "03_tts")["updated_at"] != stamps[("ch001", "03_tts")]
    assert m.stage("ch001", "04_render")["updated_at"] != stamps[("ch001", "04_render")]
    assert m.stage("ch002", "03_tts")["updated_at"] == stamps[("ch002", "03_tts")]
    assert m.stage("ch002", "04_render")["updated_at"] == stamps[("ch002", "04_render")]
    assert m.stage(None, "05_assemble")["updated_at"] != stamps[("video", "05_assemble")]

    # music bed hook: a configured track gets mixed under the narration
    track = root / "bed.wav"
    _tone_track(track)
    cfg = root / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "music: {tracks: [bed.wav], gain_db: -18}\n", encoding="utf-8")
    ctx4 = make_context(root, stub=True)
    run_pipeline(ctx4, [5])
    assert make_context(root).manifest.stage(None, "05_assemble")["status"] == "ok"
    a = next(s for s in ff.probe(p.final_mp4)["streams"] if s["codec_type"] == "audio")
    assert a["codec_name"] == "aac"
    assert abs(ff.duration(p.final_mp4) - total) < 0.3
