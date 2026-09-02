from pathlib import Path

from mawha_recap.manifest import Manifest, fingerprint, run_stage, sha256_text


def test_fingerprint_is_order_independent():
    a = fingerprint(x=1, y={"b": 2, "a": [1, 2]})
    b = fingerprint(y={"a": [1, 2], "b": 2}, x=1)
    assert a == b
    assert a != fingerprint(x=2, y={"b": 2, "a": [1, 2]})


def test_needs_run_and_mark_ok(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json", tmp_path)
    out = tmp_path / "work" / "x.txt"
    fp = fingerprint(v=1)
    assert m.needs_run("ch001", "00_ingest", fp, [out])

    def fn():
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("hi", encoding="utf-8")
        return {"flags": ["a"]}

    assert run_stage(m, "ch001", "00_ingest", fp, [out], fn) == "ok"
    assert run_stage(m, "ch001", "00_ingest", fp, [out], fn) == "skipped"
    assert m.stage("ch001", "00_ingest")["flags"] == ["a"]
    assert m.stage("ch001", "00_ingest")["outputs"] == ["work/x.txt"]
    # changed fingerprint -> rerun; missing output -> rerun; force -> rerun
    assert m.needs_run("ch001", "00_ingest", fingerprint(v=2), [out])
    out.unlink()
    assert m.needs_run("ch001", "00_ingest", fp, [out])
    assert m.needs_run("ch001", "00_ingest", fp, [], force=True)
    assert not m.needs_run("ch001", "00_ingest", fp, [])
    # persisted and reloaded
    m.save()
    m2 = Manifest.load(tmp_path / "manifest.json", tmp_path)
    assert m2.stage("ch001", "00_ingest")["status"] == "ok"


def test_error_is_recorded(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json", tmp_path)

    def boom():
        raise RuntimeError("nope")

    try:
        run_stage(m, "ch001", "01_panels", "fp", [], boom)
    except RuntimeError:
        pass
    assert m.stage("ch001", "01_panels")["status"] == "error"
    assert "nope" in m.stage("ch001", "01_panels")["error"]


def test_approvals_and_costs(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.json", tmp_path)
    m.set_approval("ch001", sha256_text("abc"))
    assert m.approval("ch001") == sha256_text("abc")
    assert m.approval("ch002") is None
    m.add_cost(stage="02_beats", chapter="ch001", usd=0.5)
    m.add_cost(stage="03_tts", chapter="ch001", usd=0.25)
    assert m.total_usd() == 0.75
