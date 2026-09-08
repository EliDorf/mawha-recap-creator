"""manifest.json: per-chapter x per-stage bookkeeping that makes every stage idempotent."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHAPTER_STAGES = ["00_ingest", "01_panels", "02_beats", "02_script", "03_tts", "04_render"]
VIDEO_STAGE = "05_assemble"
ALL_STAGES = CHAPTER_STAGES + [VIDEO_STAGE]

# CLI stage numbers -> manifest stage ids
STAGE_NUMBERS: dict[int, list[str]] = {
    0: ["00_ingest"],
    1: ["01_panels"],
    2: ["02_beats", "02_script", "02_opening"],
    3: ["03_tts"],
    4: ["04_render"],
    5: ["05_assemble"],
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return "sha256:" + h.hexdigest()


def fingerprint(**parts: Any) -> str:
    """Stable hash of arbitrary JSON-able parts (paths must already be posix-relative)."""
    return sha256_text(canonical_json(parts))


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


class Manifest:
    def __init__(self, path: Path, root: Path, data: dict[str, Any] | None = None):
        self.path = path
        self.root = root
        self.data = data or {
            "schema": 1,
            "video": {},
            "config_hash": None,
            "budget": {},
            "approvals": {},
            "chapters": {},
            "video_stages": {},
            "costs": [],
        }

    # ---- persistence -----------------------------------------------------
    @classmethod
    def load(cls, path: Path, root: Path) -> Manifest:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path, root, data)
        return cls(path, root)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data, indent=2, ensure_ascii=False, sort_keys=False)
        fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ---- stage records ------------------------------------------------------
    def chapter(self, ch: str) -> dict[str, Any]:
        return self.data["chapters"].setdefault(ch, {})

    def chapters(self) -> list[str]:
        return list(self.data["chapters"].keys())

    def stage(self, ch: str | None, stage: str) -> dict[str, Any]:
        if ch is None:
            return self.data["video_stages"].setdefault(stage, {"status": "pending"})
        return self.chapter(ch).setdefault(stage, {"status": "pending"})

    def needs_run(self, ch: str | None, stage: str, fp: str, outputs: Iterable[Path], force: bool = False) -> bool:
        if force:
            return True
        rec = self.stage(ch, stage)
        if rec.get("status") != "ok" or rec.get("fingerprint") != fp:
            return True
        for out in outputs:
            if not Path(out).exists():
                return True
        return False

    def mark_ok(self, ch: str | None, stage: str, fp: str, outputs: Iterable[Path], **extra: Any) -> None:
        rec = self.stage(ch, stage)
        rec.clear()
        rec.update(
            {
                "status": "ok",
                "fingerprint": fp,
                "outputs": [self._rel(p) for p in outputs],
                "updated_at": now_iso(),
                "error": None,
            }
        )
        rec.update(extra)

    def mark_error(self, ch: str | None, stage: str, error: str) -> None:
        rec = self.stage(ch, stage)
        rec["status"] = "error"
        rec["error"] = error[-2000:]
        rec["updated_at"] = now_iso()

    def mark_stale(self, ch: str | None, stage: str) -> None:
        rec = self.stage(ch, stage)
        if rec.get("status") == "ok":
            rec["status"] = "stale"

    def _rel(self, p: Path | str) -> str:
        p = Path(p)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()

    # ---- approvals ----------------------------------------------------------
    def set_approval(self, ch: str, script_hash: str) -> None:
        self.data["approvals"][ch] = {"script_hash": script_hash, "approved_at": now_iso()}

    def approval(self, ch: str) -> str | None:
        rec = self.data["approvals"].get(ch)
        return rec["script_hash"] if rec else None

    # ---- budget --------------------------------------------------------------
    @property
    def budget(self) -> dict[str, Any]:
        return self.data.setdefault("budget", {})

    @budget.setter
    def budget(self, value: dict[str, Any]) -> None:
        self.data["budget"] = value

    # ---- costs ---------------------------------------------------------------
    def add_cost(self, **entry: Any) -> None:
        entry.setdefault("at", now_iso())
        self.data.setdefault("costs", []).append(entry)

    def total_usd(self) -> float:
        return round(sum(float(c.get("usd", 0.0)) for c in self.data.get("costs", [])), 4)


def run_stage(
    manifest: Manifest,
    ch: str | None,
    stage: str,
    fp: str,
    outputs: list[Path],
    fn: Callable[[], dict[str, Any] | None],
    force: bool = False,
) -> str:
    """Run `fn` unless the manifest says this stage is up to date. Returns 'skipped' or 'ok'."""
    if not manifest.needs_run(ch, stage, fp, outputs, force=force):
        return "skipped"
    try:
        extra = fn() or {}
    except Exception as e:  # noqa: BLE001 - we record then re-raise
        manifest.mark_error(ch, stage, f"{type(e).__name__}: {e}")
        manifest.save()
        raise
    manifest.mark_ok(ch, stage, fp, outputs, **extra)
    manifest.save()
    return "ok"
