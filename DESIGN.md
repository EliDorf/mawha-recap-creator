# Design notes

## Idempotency model
- `manifest.json` keeps one record per chapter × stage (`status`, `fingerprint`, `outputs`, `updated_at`, stage extras) and one for the video-level assembly. `run_stage()` in `manifest.py` is the only place that decides skip/run.
- Fingerprints hash: stage code version, prompt template version, `config.subset_for(stage)`, input artifact hashes. Downstream invalidation is automatic because inputs include the previous stage's outputs.
- Soft dependencies (story-so-far, previous chapter's last lines) are tracked via `context_hash` and produce a `stale` flag rather than a re-run.
- Approval hashes canonicalised script content; TTS refuses when the approval hash does not match the file.

## Stage 2 (Claude)
- Vision request per chapter: cached system block (rules + glossary) + a story-so-far block, then labelled JPEGs (fit within 1000×2000 so bubble text survives and the >20-image size rule holds). Structured output via `messages.stream(output_format=BeatSheetOut)`; the beta path adds server-side refusal fallbacks and degrades to the plain path if the API rejects the combination.
- `normalize_beats` makes every panel end up in exactly one beat or in `skip_panels`; `normalize_script` makes every story panel appear in exactly one line and every line show at least one panel. Ids are assigned deterministically after normalisation.

## Stage 3 (ElevenLabs)
- Lines are grouped into segments (≤ `tts.max_chars`, prefer breaks at beat changes) for prosody; each segment is cached by text+voice hash.
- Timing: `convert_with_timestamps` when the model returns an alignment, else `forced_alignment.create` on the produced audio with the tag-stripped text. `text/alignment.py` maps characters → words → lines, masking `[tags]` and tolerating small differences via `difflib`.
- Per-line slices (cut at the midpoints of inter-line silence) are re-assembled with the variant's pauses and the title-card lead-in, so gap control does not depend on the model. Two-pass linear `loudnorm` on the assembled chapter.

## Stage 4 (ffmpeg)
- Level 1: one clip per panel from a single decoded frame (`loop` filter), pan via `crop` with a smoothstep `t` expression on a 2× oversampled `gbrp` frame (both `crop` and `zoompan` snap to integer pixels; yuv420p snaps to even rows), then Lanczos downscale. Short panels: blurred backdrop + `zoompan`. Clips are padded by the crossfade length except the last, so `xfade` offsets are cumulative sums of intended durations and the chapter length equals the VO length.
- Level 2: `-filter_complex_script` file with `settb=AVTB` on every input, chained `xfade`, `subtitles` (ASS from word timings, `fontsdir` relative), VO muxed, `-t` = timeline duration. Graphs above `render.max_clips_per_graph` clips are split at line boundaries (hard cut), concatenated, and subtitled in a final pass.
- `pick_encoder("auto")` test-encodes each hardware encoder before choosing it; builds often list NVENC/QSV without the hardware.

## Stage 5
- Concat demuxer stream copy (all chapters share encode parameters), optional looped music bed via `amix`, SRT with chapter offsets, YouTube chapter rules (first at 00:00, ≥ 10 s apart), thumbnail = highest Laplacian-variance panel, best 16:9 window, text lockup.
