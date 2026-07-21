# Instagram Reels — craft notes

- Hook lives in the first 1-2s: a bold on-screen title, a pattern interrupt, or a "wait for it"
  promise. Capture the exact hook text and its timing in `virality_formula.hook`.
- Text overlays are dense and central; transcribe them verbatim with timing in `text_overlays`.
  Note the font weight/case (heavy sans, all-caps) — creators reuse a consistent overlay style.
- Split-screen / side-by-side comparisons and before→after reveals are a recurring IG format;
  call them out as `recurring_visual_motifs` and reflect the cut rhythm in `pacing`.
- Audio is usually EITHER a licensed trending track (attach-manually, `audio_is_reusable:false`)
  OR original voiceover. Set `audio_strategy.audio_type` accordingly and carry the hub's
  `audio_*` fields through unchanged — they are the join key for the trending-sound tooling.
- 9:16, 1080x1920, ~30fps is the safe default when the metadata is unstated.
- CTAs are typically "follow for more / save this"; capture the exact wording in
  `virality_formula.cta`.
