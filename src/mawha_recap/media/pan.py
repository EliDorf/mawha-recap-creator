"""Pure functions for the automated edit: panel durations, Ken Burns expressions, clip/xfade arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, replace


def allocate_durations(
    total_s: float, heights_scaled: list[int], out_h: int, min_panel_s: float, max_px_s: float
) -> list[float]:
    """Split a line's seconds across its panels.

    Each panel needs at least `min_panel_s`, and a tall panel needs `travel / max_px_s` so the pan never
    exceeds the speed cap. Spare time is shared by height. If the line is too short for all panels at
    the minimum, trailing panels are dropped (caller shows fewer panels). If the minimums cannot be met
    even for one panel the durations are scaled down proportionally.
    """
    n = len(heights_scaled)
    if n == 0 or total_s <= 0:
        return []
    keep = n
    while keep > 1 and keep * min_panel_s > total_s:
        keep -= 1
    heights = heights_scaled[:keep]
    need = [max(min_panel_s, max(0, h - out_h) / max(1e-6, max_px_s)) for h in heights]
    weights = [max(1.0, h / out_h) for h in heights]
    s = sum(need)
    if s <= total_s:
        extra = total_s - s
        tw = sum(weights)
        return [b + extra * w / tw for b, w in zip(need, weights, strict=True)]
    return [total_s * b / s for b in need]


def frames_for(durations: list[float], fps: int, total_frames: int | None = None) -> list[int]:
    """Round seconds to whole frames; with `total_frames` the rounding is corrected to sum exactly."""
    raw = [d * fps for d in durations]
    frames = [max(1, int(round(x))) for x in raw]
    if total_frames is None:
        return frames
    diff = total_frames - sum(frames)
    order = sorted(range(len(frames)), key=lambda i: (raw[i] - int(raw[i])), reverse=(diff > 0))
    k = 0
    while diff != 0 and frames:
        i = order[k % len(order)]
        if diff > 0:
            frames[i] += 1
            diff -= 1
        elif frames[i] > 1:
            frames[i] -= 1
            diff += 1
        k += 1
        if k > 10 * len(frames) + 10:
            break
    return frames


def smoothstep(p_expr: str) -> str:
    return f"pow({p_expr},2)*(3-2*({p_expr}))"


def crop_y_expr(travel: int, hold_in: float, window: float) -> str:
    """crop `y` expression: eased vertical pan from 0 to `travel` px between hold_in and hold_in+window."""
    if travel <= 0 or window <= 0:
        return "0"
    p = f"clip((t-{hold_in:.3f})/{window:.3f},0,1)"
    return f"{travel}*{smoothstep(p)}"


def pan_window(duration_s: float, hold_in: float, hold_out: float) -> tuple[float, float]:
    """(hold_in, window) so that the pan fits inside the clip even when the clip is very short."""
    window = duration_s - hold_in - hold_out
    if window < 0.5:
        hold_in = min(hold_in, max(0.0, duration_s * 0.15))
        window = max(0.1, duration_s - 2 * hold_in)
    return hold_in, window


@dataclass
class ClipSpec:
    panel_id: str
    src: str  # path relative to the project root, posix
    frames: int  # intended frames (the clip is padded by `pad` extra frames for the crossfade)
    pad: int
    mode: str  # pan | fit | blur | card
    src_w: int
    src_h: int
    crop: tuple[int, int, int, int] | None = None

    @property
    def total_frames(self) -> int:
        return self.frames + self.pad


def xfade_offsets(intended_seconds: list[float]) -> list[float]:
    """offset_k = sum(d_1..d_k) when every non-final clip is padded by the fade length."""
    out: list[float] = []
    acc = 0.0
    for d in intended_seconds[:-1]:
        acc += d
        out.append(acc)
    return out


def scaled_height(src_w: int, src_h: int, target_w: int) -> int:
    h = round(src_h * target_w / src_w)
    return h + (h % 2)  # keep even for yuv420p downstream


def clip_filter(spec: ClipSpec, **kwargs) -> str:
    """Apply an optional shot crop before scaling and camera movement."""
    if spec.crop is None:
        return _clip_filter(spec, **kwargs)
    x, y, w, h = spec.crop
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > spec.src_w or y + h > spec.src_h:
        raise ValueError(f"crop outside source image for {spec.panel_id}: {spec.crop}")
    graph = _clip_filter(replace(spec, src_w=w, src_h=h, crop=None), **kwargs)
    return f"[0:v]crop={w}:{h}:{x}:{y}[shot];" + graph.replace("[0:v]", "[shot]")


def _clip_filter(
    spec: ClipSpec,
    *,
    fps: int,
    out_w: int,
    out_h: int,
    oversample: int,
    hold_in: float,
    hold_out: float,
    zoom: float,
    fg_frac: float = 0.62,
) -> str:
    """Level-1 filtergraph for one panel clip. Input is a single image; output is CFR yuv420p out_w x out_h."""
    n = spec.total_frames
    ow, oh = out_w * oversample, out_h * oversample
    loop = f"loop=loop={n - 1}:size=1:start=0,setpts=N/({fps}*TB)"
    finish = f"scale={out_w}:{out_h}:flags=lanczos,fps={fps},format=yuv420p"
    duration = n / fps
    if spec.mode == "card":
        return f"[0:v]scale={out_w}:{out_h}:flags=lanczos,format=yuv420p,{loop},fps={fps}[v]"
    if spec.mode == "cover":
        z = f"zoompan=z='1+{zoom:.3f}*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={ow}x{oh}:fps={fps}"
        return f"[0:v]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},format=gbrp,{z},{finish}[v]"
    if spec.mode == "pan":
        sh = scaled_height(spec.src_w, spec.src_h, ow)
        travel = max(0, sh - oh)
        h_in, window = pan_window(duration, hold_in, hold_out)
        y = crop_y_expr(travel, h_in, window)
        return f"[0:v]scale={ow}:-2:flags=lanczos,format=gbrp,{loop},crop={ow}:{oh}:0:'{y}',{finish}[v]"
    if spec.mode == "blur":
        fg_w = int(ow * fg_frac) // 2 * 2
        sh = scaled_height(spec.src_w, spec.src_h, fg_w)
        travel = max(0, sh - oh)
        h_in, window = pan_window(duration, hold_in, hold_out)
        y = crop_y_expr(travel, h_in, window)
        bg = (
            f"[0:v]split[a][b];[a]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
            f"gblur=sigma=40,eq=brightness=-0.2,format=gbrp,{loop}[bg];"
        )
        fg = f"[b]scale={fg_w}:-2:flags=lanczos,format=gbrp,{loop}[fg];"
        return f"{bg}{fg}[bg][fg]overlay=x=(W-w)/2:y='-({y})':eval=frame,{finish}[v]"
    # fit: short panel fills the width over a blurred backdrop, with a gentle zoom
    bg = (
        f"[0:v]split[a][b];[a]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
        f"gblur=sigma=40,eq=brightness=-0.2[bg];"
    )
    fg = f"[b]scale={ow}:-2:flags=lanczos[fg];"
    z = f"zoompan=z='1+{zoom:.3f}*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={ow}x{oh}:fps={fps}"
    return f"{bg}{fg}[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,format=gbrp,{z},{finish}[v]"
