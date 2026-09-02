"""Claude provider (structured outputs via the official SDK) and an offline stub with the same interface."""

from __future__ import annotations

import os
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import VideoConfig
from ..models import BeatOut, BeatSheetOut, MetaOut, NewCharacter, ScriptLineOut, ScriptOut

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMRefusal(RuntimeError):
    pass


def cache_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def usage_dict(msg: Any, model: str) -> dict[str, Any]:
    u = getattr(msg, "usage", None)
    return {
        "model": getattr(msg, "model", None) or model,
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        "request_id": getattr(msg, "_request_id", None),
    }


def usage_usd(usage: dict[str, Any], prices: dict[str, dict[str, float]]) -> float:
    model = str(usage.get("model") or "")
    table = prices.get(model)
    if table is None:
        # fall back to the first price table whose key is a prefix of the served model id
        for key, val in prices.items():
            if model.startswith(key):
                table = val
                break
    if table is None:
        return 0.0
    usd = (
        usage.get("input_tokens", 0) * table.get("input", 0)
        + usage.get("output_tokens", 0) * table.get("output", 0)
        + usage.get("cache_read_input_tokens", 0) * table.get("cache_read", 0)
        + usage.get("cache_creation_input_tokens", 0) * table.get("cache_write", 0)
    ) / 1_000_000
    return round(usd, 5)


class ClaudeLLM:
    name = "claude"

    def __init__(self, cfg: VideoConfig):
        import anthropic

        self._anthropic = anthropic
        self.cfg = cfg
        self.client = anthropic.Anthropic(max_retries=4)
        self.use_fallbacks = cfg.llm.fallbacks
        self.warnings: list[str] = []

    def parse(
        self,
        *,
        model: str,
        system: list[dict[str, Any]],
        user_content: list[dict[str, Any]] | str,
        schema: type[T],
        max_tokens: int | None = None,
        stub_hint: dict[str, Any] | None = None,
    ) -> tuple[T, dict[str, Any]]:
        del stub_hint  # only the stub uses it
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self.cfg.llm.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": schema,
        }
        if self.cfg.llm.effort:
            kwargs["output_config"] = {"effort": self.cfg.llm.effort}
        msg = None
        if self.use_fallbacks:
            try:
                with self.client.beta.messages.stream(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                ) as stream:
                    msg = stream.get_final_message()
            except (self._anthropic.BadRequestError, TypeError) as e:
                self.use_fallbacks = False
                self.warnings.append(f"refusal fallbacks unavailable, continuing without them: {e}")
        if msg is None:
            with self.client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise LLMRefusal(f"Claude declined this request ({getattr(details, 'category', None)})")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("model output was cut off; raise llm.max_tokens")
        parsed = getattr(msg, "parsed_output", None)
        if parsed is None:
            text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
            parsed = schema.model_validate_json(text)
        return parsed, usage_dict(msg, model)


# ----------------------------------------------------------------- stub

_WORDS = (
    "the hunter steps into the corridor and the torchlight bends around him while the others wait "
    "silence settles over the stone hall as the door swings shut behind them and a cold wind rises "
    "she studies the map again then folds it away without a word and looks toward the distant tower "
    "nobody speaks for a long moment until the youngest of them finally laughs and breaks the spell"
).split()

_EMOTIONS = ["dread", "quiet resolve", "relief", "tension", "wonder", "grief", "amusement"]


class StubLLM:
    """Deterministic offline stand-in. Uses `stub_hint` (panel ids, beats, budget) to shape its output."""

    name = "stub"

    def __init__(self, cfg: VideoConfig):
        self.cfg = cfg
        self.warnings: list[str] = []

    def parse(
        self,
        *,
        model: str,
        system: list[dict[str, Any]],
        user_content: list[dict[str, Any]] | str,
        schema: type[T],
        max_tokens: int | None = None,
        stub_hint: dict[str, Any] | None = None,
    ) -> tuple[T, dict[str, Any]]:
        hint = stub_hint or {}
        usage = {"model": "stub", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0, "request_id": None}
        if schema is BeatSheetOut:
            return self._beats(hint), usage  # type: ignore[return-value]
        if schema is ScriptOut:
            return self._script(hint), usage  # type: ignore[return-value]
        if schema is MetaOut:
            return MetaOut(hook="A quiet retelling of the chapters, beat by beat. Settle in.", title_suffix="the story so far"), usage  # type: ignore[return-value]
        raise NotImplementedError(f"stub has no answer for {schema.__name__}")

    def _beats(self, hint: dict[str, Any]) -> BeatSheetOut:
        ids: list[str] = list(hint.get("panel_ids", []))
        chapter = hint.get("chapter", "ch000")
        beats: list[BeatOut] = []
        i = 0
        k = 0
        while i < len(ids):
            n = 2 if k % 2 == 0 else 3
            group = ids[i : i + n]
            beats.append(
                BeatOut(
                    panel_ids=group,
                    what_happens=f"Beat {k + 1}: the characters move through the scene shown in {', '.join(group)}.",
                    dialogue_gist="They exchange a few tense words." if k % 3 else "",
                    emotional_beat=_EMOTIONS[k % len(_EMOTIONS)],
                    importance=3 if k % 4 == 0 else 2,
                )
            )
            i += n
            k += 1
        return BeatSheetOut(
            beats=beats,
            chapter_summary=f"In {chapter} the party pressed deeper into the dungeon across {len(beats)} beats, and an old rivalry resurfaced.",
            new_characters=[NewCharacter(name="Stub Hero", notes="the narrator's focus")] if hint.get("first_chapter") else [],
            open_threads=["Who opened the sealed door?"],
            skip_panels=[],
        )

    def _script(self, hint: dict[str, Any]) -> ScriptOut:
        beats: list[dict[str, Any]] = list(hint.get("beats", []))
        budget = int(hint.get("budget_words", 200))
        per_line = max(8, round(budget / max(1, len(beats))))
        lines: list[ScriptLineOut] = []
        cursor = 0
        for j, b in enumerate(beats):
            words = [_WORDS[(cursor + t) % len(_WORDS)] for t in range(per_line)]
            cursor += per_line
            sentence = " ".join(words)
            sentence = sentence[0].upper() + sentence[1:] + "."
            lines.append(
                ScriptLineOut(
                    beat=b["id"],
                    panel_ids=list(b["panel_ids"]),
                    text=sentence,
                    tts_text=sentence.replace(" and ", "... and ", 1) + " [pause]" if j % 3 == 2 else "",
                )
            )
        return ScriptOut(lines=lines)


def make_llm(cfg: VideoConfig, stub: bool = False) -> ClaudeLLM | StubLLM:
    if stub:
        return StubLLM(cfg)
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        # The SDK can also use an `ant auth login` profile; try, but fail with a clear message.
        try:
            return ClaudeLLM(cfg)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "no Anthropic credentials: set ANTHROPIC_API_KEY (or run `ant auth login`), or use --stub"
            ) from e
    return ClaudeLLM(cfg)
