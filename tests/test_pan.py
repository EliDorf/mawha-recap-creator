from mawha_recap.media.pan import ClipSpec, allocate_durations, clip_filter, crop_y_expr, frames_for, xfade_offsets


def test_allocate_durations_weights_and_minimums():
    d = allocate_durations(10.0, [1080, 3240], 1080, 1.5, 90.0)
    assert abs(sum(d) - 10.0) < 1e-9
    assert d[1] > d[0]  # taller panel gets more time
    # travel 2160 px at 90 px/s needs 24 s -> minimums exceed total -> scaled proportionally, nothing dropped
    d = allocate_durations(10.0, [1080, 3240], 1080, 1.5, 90.0)
    assert len(d) == 2
    # too many panels for the line -> trailing panels dropped
    d = allocate_durations(2.0, [1080, 1080, 1080], 1080, 1.5, 90.0)
    assert len(d) == 1 and abs(d[0] - 2.0) < 1e-9
    assert allocate_durations(5.0, [], 1080, 1.5, 90.0) == []


def test_frames_sum_exactly():
    f = frames_for([1.234, 2.345, 0.421], 30, total_frames=120)
    assert sum(f) == 120 and all(x >= 1 for x in f)
    assert frames_for([0.01], 30) == [1]


def test_xfade_offsets_and_expr():
    assert xfade_offsets([4.0, 5.0, 6.0]) == [4.0, 9.0]
    assert xfade_offsets([3.0]) == []
    assert crop_y_expr(0, 0.3, 4.0) == "0"
    y = crop_y_expr(1200, 0.3, 4.0)
    assert y.startswith("1200*pow(clip((t-0.300)/4.000,0,1),2)")


def test_clip_filter_modes():
    common = dict(fps=30, out_w=1920, out_h=1080, oversample=2, hold_in=0.3, hold_out=0.3, zoom=0.06)
    pan = clip_filter(ClipSpec("p1", "a.png", 90, 15, "pan", 800, 2400), **common)
    assert "loop=loop=104:size=1:start=0" in pan and "crop=3840:2160:0:'" in pan and pan.endswith("format=yuv420p[v]")
    assert "scale=3840:-2" in pan and "format=gbrp" in pan
    fit = clip_filter(ClipSpec("p2", "b.png", 60, 0, "fit", 800, 300), **common)
    assert "zoompan=z='1+0.060*on/60'" in fit and "gblur" in fit
    card = clip_filter(ClipSpec("card", "c.png", 60, 15, "card", 1920, 1080), **common)
    assert "loop=loop=74" in card and "crop" not in card
    blur = clip_filter(ClipSpec("p3", "d.png", 60, 0, "blur", 800, 2400), **common)
    assert "overlay=x=(W-w)/2:y='-(" in blur and "eval=frame" in blur
