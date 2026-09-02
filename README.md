# mawha-recap-creator

Turn a folder of webtoon / manhwa chapter images into a narrated YouTube video: a 20-30 minute **recap** or a 1-3 hour **sleep** retelling. Every chapter is tracked through every stage in a `manifest.json`, so you can re-run one stage for one chapter without redoing the video, and all edit timing comes from the narration audio, so nothing is hand-synced.

```
input/ch012.png or ch012/001.jpg…   ─►  0 ingest  ─►  1 panels  ─►  2 beats + script  ─►  [you skim & approve]
                                                                                              │
out/<slug>.mp4 + .srt + chapters.txt + description.md + metadata.json + thumbnail.jpg  ◄─  5 assemble  ◄─  4 render  ◄─  3 TTS
```

| Stage | What it does | Reads | Writes |
|---|---|---|---|
| 0 ingest | finds chapters, stitches tile folders into one tall strip | `input/` | `work/00_ingest/<ch>/strip.png` |
| 1 panels | projection-profile gutter detection, min/max panel rules, manual overrides | strip | `work/01_panels/<ch>/<ch>_pNNN.png`, `panels.json` |
| 2 beats | Claude looks at the chapter's panels and emits a structured beat sheet | panels, `series.yaml`, earlier summaries | `work/02_beats/<ch>.beats.json` |
| 2 script | Claude writes narration lines that keep their panel references, under a word budget | beats | `work/02_script/<ch>.script.yaml`, `review/review.html` |
| 3 TTS | ElevenLabs per segment, word timestamps, per-line assembly with pauses, loudnorm | approved script | `work/03_tts/<ch>.vo.wav`, `<ch>.timeline.json` |
| 4 render | Ken Burns pan per panel, crossfades, burned subtitles, chapter title card, VO mux | timeline, panels | `work/04_render/<ch>.mp4` |
| 5 assemble | concat, optional music bed, SRT, chapter markers, title/description, thumbnail | chapter renders | `out/` |

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- FFmpeg 6+ built with libass (`brew install ffmpeg` on macOS, `winget install Gyan.FFmpeg` on Windows, `apt install ffmpeg` on Debian/Ubuntu)
- `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` in the environment for real runs (`--stub` runs the whole pipeline offline with fake providers)

```bash
git clone <this repo> && cd mawha-recap-creator
uv sync
uv run recap doctor          # checks ffmpeg, filters, encoders, fonts, keys
```

`uv run recap …` works from the repo. To install the command globally: `uv tool install .`

## Quickstart

```bash
recap init projects/solo-leveling/ch012-045-sleep --series "Solo Leveling" --variant sleep --runtime 90 --voice <elevenlabs voice id>
# drop chapter images into projects/solo-leveling/ch012-045-sleep/input/
#   either one tall image per chapter:   ch012.png
#   or a folder of tiles per chapter:    ch012/001.jpg 002.jpg …
# edit projects/solo-leveling/series.yaml (character names, glossary) – it is shared by every video of the series

recap run projects/solo-leveling/ch012-045-sleep --stage 0
recap run projects/solo-leveling/ch012-045-sleep --stage 1     # check `recap status` for forced_split flags
recap run projects/solo-leveling/ch012-045-sleep --stage 2     # beats + script, writes review/review.html
open projects/solo-leveling/ch012-045-sleep/review/review.html # 5-minute skim; edit work/02_script/*.script.yaml as needed
recap approve projects/solo-leveling/ch012-045-sleep
recap run projects/solo-leveling/ch012-045-sleep --all          # TTS → render → package (earlier stages are skipped)
```

The package lands in `out/`: the MP4, a sidecar `.srt`, `chapters.txt` to paste into the description, `description.md`, `metadata.json` (title, tags, chapter offsets) and `thumbnail.jpg`. Upload is manual in v1.

Try it offline first:

```bash
recap demo demo && recap run demo --stage 0 --stub && recap run demo --stage 1 --stub && recap run demo --stage 2 --stub
recap approve demo && recap run demo --all --stub
```

## Commands

| Command | Purpose |
|---|---|
| `recap init <dir>` | create `config.yaml`, `input/` and a `series.yaml` one level up |
| `recap run <dir> --stage N` / `--all` | run one stage (0-5) or all, for `--chapter ch012` only if given |
| `recap approve <dir>` | hash the current scripts into the manifest; stage 3 refuses unapproved or edited scripts |
| `recap status <dir>` | chapter × stage table, flags, approval state, spend so far |
| `recap doctor [<dir>]` | environment check; `--probe-tts` makes one tiny ElevenLabs request to learn the timing path |
| `recap demo <dir>` | synthetic project for an offline dry run |

`run` flags: `--force` (ignore the manifest), `--stub` (offline providers), `--no-gate` (skip approval for this run), `--reallocate` (recompute the word budget), `--refresh-stale` / `--pin-context` (see below).

## How re-runs work

Each stage stores a fingerprint of everything it read (stage code version, prompt version, the relevant config subset, input file hashes). A stage runs only when that fingerprint changed, an output is missing, or you pass `--force`. Because stage N's inputs include stage N-1's outputs, editing one script line re-synthesises only that line's TTS segment, re-renders that chapter, and re-assembles the video (a stream copy).

Two things are deliberately *soft* dependencies: the story-so-far that later chapters receive, and the previous chapter's last lines the writer sees for continuity. When those change upstream (say you regenerated chapter 12's beats), later chapters are marked **stale** in `recap status` and `review.html` instead of silently re-spending API calls. `--refresh-stale` re-runs them; `--pin-context` suppresses the flag.

The word budget (`target_runtime_min × wpm`, split across chapters by beat count) is allocated once and stored in the manifest, so regenerating one chapter does not shift every other chapter's budget. `--reallocate` recomputes it.

Approval hashes the *content* of `script.yaml` (ids, panel references, text), so reformatting the YAML does not un-approve it, while changing a word does.

## Configuration

See `examples/config.example.yaml` for every key with comments. The important ones:

| Key | recap preset | sleep preset | Notes |
|---|---|---|---|
| `style.wpm` | 150 | 130 | drives the word budget |
| `style.line_gap_s` | 0.35 | 0.9 | silence between narration lines |
| `style.loudness_lufs` | -16 | -20 | two-pass loudnorm target (TP -1.5, LRA 11) |
| `style.pan.max_px_s` | 90 | 45 | pan speed cap at 1080p; slower pans get more time per panel |
| `style.min_panel_s` / `crossfade_s` | 1.5 / 0.4 | 2.5 / 0.8 | panel hold floor, fade length |
| `style.framing` | fill | fill | `fill` = crop to 16:9 and pan; `blur` = panel over a blurred backdrop |
| `style.subtitles.burn` | true | true | sidecar `.srt` is always written |
| `render.encoder` | auto | auto | `auto` probes VideoToolbox / NVENC / QSV / AMF with a test encode, else libx264 |
| `render.chapter_cards` | true | true | 2-second "Chapter N" card; it is also the chapter-marker anchor |
| `tts.tempo` | 1.0 | 1.0 | `eleven_v3` has no speed setting; 0.93 slows the voice via atempo |
| `vision.max_w` / `max_h` | 1000 / 2000 | | panels are downscaled to fit before being sent to Claude |

`series.yaml` (shared by all videos of a series) holds character names/aliases, a glossary and an optional carry-over `story_so_far`. It is cached as part of the system prompt.

### The sleep variant

Same pipeline, different knobs: slower voice pacing (longer pauses, optional `tts.tempo`, pacing devices in `tts_text` such as `…` and `[pause]`), -20 LUFS, slower pans with longer holds, softer subtitle colour. The writing prompt is shared; put any tone differences in `style.tone_notes`.

## Manual fixes

- **Panel splits**: chapters with `forced_split` flags (a panel taller than `segment.max_panel_h` was cut at its least-inky row) are listed by `recap status`. To fix by hand, write `work/01_panels/<ch>/overrides.json` and re-run stage 1:
  ```json
  {"split_rows": [13000], "remove_cuts": [8200], "skip": ["ch012_p001"], "panels": null}
  ```
  `split_rows` adds cuts (strip row numbers), `remove_cuts` deletes the nearest detected cut, `skip` hides credit/ad panels from the story, and `panels: [[y0, y1], …]` replaces detection entirely.
- **Script**: edit `text` (what subtitles show) and optionally `tts_text` (what the voice reads, tags allowed) or `pause_after` in `script.yaml`, then `recap approve`.
- **Beats**: `beats.json` is plain JSON; editing `chapter_summary` changes what later chapters are told.

## Costs (rough)

- Beats: Claude bills `ceil(w/28)·ceil(h/28)` tokens per image. An 800×2000 panel ≈ 2,100 tokens; 60 panels ≈ 125k tokens ≈ $0.63 per chapter on claude-opus-5. Lower `vision.max_h` to trade legibility for cost.
- Script: a few thousand tokens per chapter.
- TTS: ElevenLabs charges per character; a 90-minute sleep video is ≈ 12k words ≈ 65k characters. Per-segment caching means an edited line only re-bills its own segment.
- `recap status` shows the running spend from the manifest's cost log (`llm.prices` and `tts.usd_per_1k_chars` in the config).

Refusal fallbacks are enabled by default (`llm.fallbacks: true`): if Claude declines a request on safety grounds, the API re-runs it on a fallback model inside the same call. Set it to `false` to opt out.

## Windows notes

Everything is plain Python plus the ffmpeg binary. Paths handed to ffmpeg are always relative to the project directory and use forward slashes, the filtergraph is passed as a file (command-line length limits), and the subtitle font is copied into `<project>/assets/fonts` so `fontsdir` needs no escaping. Keep project paths free of exotic characters; ids (`slug`, chapter ids) are restricted to `[a-z0-9_-]`. If ffmpeg is not on `PATH`, set `FFMPEG_BINARY` and `FFPROBE_BINARY`.

## Troubleshooting

- *script is not approved* — run `recap approve <dir>` after reviewing, or `--no-gate` for a quick test.
- *with-timestamps unavailable … using forced alignment* — the configured ElevenLabs model does not return alignment; the pipeline transparently uses the forced-alignment endpoint. `recap doctor --probe-tts` reports which path your model gets.
- *loudnorm fell back to dynamic mode* — the chapter's loudness range was wider than `LRA=11`; output is still normalised.
- *rendered duration differs from timeline* — check the chapter's `timeline.json` vs `ffprobe`; a mismatch larger than a frame usually means the VO file changed after render (re-run stage 3 then 4).
- Slow renders: use a hardware encoder (`render.encoder`), lower `render.oversample` to 1 for previews, or raise `render.workers`.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest -q          # synthetic strips + stub providers; ffmpeg required for the audio/video tests
```

Layout: `src/mawha_recap/stages/*` (one module per stage), `providers/` (Claude + ElevenLabs + offline stubs), `media/` (ffmpeg wrapper, pan math, subtitles), `text/alignment.py` (character alignment → words/lines), `prompts/` (versioned prompt templates). See `DESIGN.md` for the decisions behind the render graph and the idempotency model.
