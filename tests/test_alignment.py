from mawha_recap.text.alignment import CharAlignment, build_segment_text, line_timing, map_alignment, strip_tags, word_spans


def test_strip_tags_and_spans():
    assert strip_tags("Hello... [pause] there [softly] friend.") == "Hello... there friend."
    text, spans = build_segment_text(["One two.", "Three."])
    assert text == "One two.\n\nThree."
    assert [text[a:b] for a, b in spans] == ["One two.", "Three."]
    assert [w for w, _, _ in word_spans("A [pause] b[c] d", (0, 16))] == ["A", "b", "d"]


def _uniform(text: str, per: float = 0.1) -> CharAlignment:
    return CharAlignment(list(text), [i * per for i in range(len(text))], [(i + 1) * per for i in range(len(text))])


def test_map_alignment_exact_and_fuzzy():
    text = "ab cd"
    s, e = map_alignment(text, _uniform(text))
    assert all(abs(a - b) < 1e-9 for a, b in zip(s, [0.0, 0.1, 0.2, 0.3, 0.4])) and abs(e[-1] - 0.5) < 1e-9
    # aligner dropped a character and normalised nothing else
    al = CharAlignment(list("abcd"), [0, 1, 2, 3], [1, 2, 3, 4])
    s, e = map_alignment("ab cd", al)
    assert s[0] == 0 and s[1] == 1 and s[2] is None and s[3] == 2 and s[4] == 3


def test_line_timing_words_and_interpolation():
    text, spans = build_segment_text(["Alpha beta [pause] gamma.", "Delta."])
    al = _uniform(text, 0.1)
    s, e, words = line_timing(text, spans[0], *map_alignment(text, al), seg_end=len(text) * 0.1)
    assert [w.w for w in words] == ["Alpha", "beta", "gamma."]
    assert words[0].s == 0.0 and abs(words[0].e - 0.5) < 1e-6
    assert s == words[0].s and e == words[-1].e
    assert all(w1.s <= w2.s for w1, w2 in zip(words, words[1:]))
    # second line starts after the separator
    s2, _, w2 = line_timing(text, spans[1], *map_alignment(text, al), seg_end=len(text) * 0.1)
    assert s2 > e and w2[0].w == "Delta."
    # missing timing for a middle word gets interpolated
    starts, ends = map_alignment(text, al)
    for i in range(6, 10):
        starts[i] = None
        ends[i] = None
    _, _, w3 = line_timing(text, spans[0], starts, ends, seg_end=3.0)
    assert w3[0].s < w3[1].s < w3[2].s and w3[1].e <= w3[2].s + 1e-9
