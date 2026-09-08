# Repeatable recap production

New recap projects start with a visual story hook and play as one continuous video. Chapter IDs remain internal tracking units. The sleep preset keeps its slower pacing and does not generate a hook unless enabled explicitly.

## Project profile

Create a project with `recap init <dir> --variant recap --series "Series name" --runtime 25`. The default voice is Roger; select a different voice with `--voice` or `voice.id`. Voice availability and the best delivery depend on your ElevenLabs account and source material.

Recommended recap settings (also shown in `examples/recap.config.yaml`):

```yaml
opening:
  enabled: true
  target_words: 40
  max_source_chapters: 3
  max_panels: 24
render:
  chapter_cards: false
style:
  line_gap_s: 0.0
  voice_guide: >-
    Conversational storytelling to one listener. Natural contractions,
    varied sentence lengths, and emotion grounded in the scene.
    Avoid a promotional or solemn announcer delivery.
tts:
  max_chars: 4500
  segment_on_beat: false
  line_separator: " "
  chapter_tail_s: 0.3
  tempo: 1.0
voice:
  model: eleven_v3
  settings: {stability: 0.5}
```

A voice slider alone cannot guarantee natural delivery. This profile removes artificial per-line silence and paragraph breaks; the provider's original pauses are retained. Connected passages let the model shape its phrasing across sentences. V3 Natural stability is a starting point, not a quality guarantee. Audition the chosen voice before a long render. Use sparse delivery tags only when a specific passage needs them.

## Production loop

1. Ingest and segment: `recap run <dir> --stage 0`, then `--stage 1`.
2. Generate draft and hook: `recap run <dir> --stage 2`. This writes the story scripts and `work/02_script/opening.yaml`. The opening generator examines a bounded set of early, important story panels and requests 3–5 artwork crops with short narration. It can select an opponent or reaction from a later input chapter. Crops are applied while rendering; source files remain intact.
3. Review `review/review.html`. Check hook facts, shot subjects, crop coordinates, the transition into the story, skipped credits/ads, narration rhythm, and runtime warnings. The chapter headings in this review page are editor navigation; they are not video cards. The opening artifact is edited independently from chapter scripts. Approvals include its text and crops.
4. Audition: `recap audition <dir>`. To compare a voice, run `recap audition <dir> --voice <id>`. Add `--text "..."` for a fixed comparison passage. Listen for rushed names, announcer cadence, unnatural stress, and excessive dramatic pauses. Auditions are cached by voice settings and text, and the cost estimate is logged. `--voice` changes only the audition; save the winning ID in config.yaml.
5. Approve: `recap approve <dir>`. Then `recap run <dir> --all` reuses the draft and opening and completes TTS, rendering, and packaging. A changed hook invalidates approval for its owning chapter. Rerunning stage 2 with unchanged source beats reuses the hook without another generation charge.
6. Watch the first 30 seconds, a middle segment, every source-chapter boundary, and the ending. Check narration pronunciation and the subtitle file against the audio. Inspect `recap status <dir>` and the final duration before publishing.

The target runtime is a writing budget, not a hard duration guarantee. The writer retries once when the draft is outside tolerance, then reports a warning. Review and shorten an overlong draft before TTS if duration is contractual. This workflow produces reviewable drafts; it does not certify narrative accuracy or voice quality without listening.

## Opening controls

The default hook is about 40 words; the generator validates 3–5 shots, at least two different source panels, allowed panel references, bounded crops, and a word-count tolerance. Invalid output stops stage 2 for correction. Changing source beats, candidate image hashes, voice guidance, or opening settings invalidates its cache. Editing only a story line does not regenerate the opening.

`opening.enabled: false` excludes the opening immediately. It is also useful for old projects with a hand-authored hook already in their chapter script. To use the generated hook in an existing project, enable it and rerun stage 2, then review and approve.

A shot in either the opening or a chapter script can specify:

```yaml
framing: cover
visual_crop: [135, 0, 549, 340]  # x, y, width, height in ORIGINAL panel pixels
```

`cover` fills the frame with a subtle push-in. `fill` pans a tall panel; `blur` retains the normal blurred-background treatment. Leave these fields absent for the project's standard framing. The renderer rejects out-of-bounds crops. Cross-chapter panel IDs must resolve to existing segmented inputs. Visual edits are included in script approval.

## Setup and compatibility

On macOS, use `brew install ffmpeg-full` for subtitle rendering, then put its `bin` directory on PATH or set `FFMPEG_BINARY` and `FFPROBE_BINARY`. The regular Homebrew FFmpeg build can lack libass. `recap doctor` identifies missing filters. A project may explicitly set `style.subtitles.burn: false` to export only the SRT.

API keys belong in environment variables. For local development, the Git-ignored `.env` can be loaded with `.venv/bin/python scripts/recap_with_env.py ...`; see `.env.example`. Never commit keys, inputs, generated videos, or private project artifacts. CI uses synthetic images and stub providers, so it needs no API keys and performs no paid generations.

Existing configs retain explicit settings. To migrate an old project, set `chapter_cards: false`, apply the narration profile above, enable the opening if wanted, rerun stage 2, and reapprove before TTS. Turning off cards changes narration lead-in timing and requires stage 3 before rendering.
