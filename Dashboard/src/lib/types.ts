// Types mirror the live hub contract (api/app.py). Verified against real
// responses, not just the OpenAPI schema (which is free-form for writes).

// "analysis-engine" is the AnalysisEngine blueprint stage (the 6th board node);
// the hub exposes it on /api/pipeline/{p}/analysis-engine and streams it in JOBS.
// "auto-search" (manual pass) / "auto-search-beat" (heartbeat tick) are the new
// Discover stage (§11) — the 7th board node, sitting FIRST in the pipeline.
// "render" is the per-item render trigger (POST /api/studio/{p}/{file}/render).
// Its job key is `${platform}:render:${file}` — one job per studio item, so the
// Renders tab can look a card's live status up with a single map read.
// "propose" is the free producer stage the cascade fires (POST /api/pipeline/{p}/propose):
// it reads blueprints and writes studio markdown, and spends nothing — unlike "render",
// which spends image credits per frame and is never automatic.
export type Stage =
  | "scrape"
  | "analyze"
  | "media"
  | "analysis-engine"
  | "auto-search"
  | "auto-search-beat"
  | "propose"
  | "render";
/** `stopped` is a person's own decision, not a failure: the scrapers save after every
    creator, so a stopped stage keeps everything it had already written. It is deliberately
    NOT `error` (which over-alarms) and NOT absent (which would read as "never ran"). */
export type JobStatus = "queued" | "running" | "done" | "error" | "stopped";

/** Whether a stage can do anything useful right now, and what would unblock it.
    `blocked_by` is a stage the user can run to clear the block — the one-click fix. It is
    null when running something else cannot help (empty watchlist, missing API key), and
    `reason` then says what the human has to do instead. */
export interface StageReadiness {
  ready: boolean;
  blocked_by: Stage | null;
  reason: string;
}

/* GET /api/hub — who this hub is.

   Running two niches at once means two clones, each with its own hub on its own port. The
   two Dashboards are otherwise identical on screen, so `niche` is what distinguishes them.
   Every field is optional-by-absence in practice: a hub older than this route 404s, and the
   UI must fall back rather than assert. */
export interface HubIdentity {
  /** absolute path of the ReelScraper directory this hub is serving */
  root: string;
  /** the niche this checkout works on, from niche_config.json — null if unreadable */
  niche: string | null;
  /** the process started before the code now on disk; it is serving the old API from memory */
  stale: boolean;
  source_mtime: number;
  source_mtime_now: number;
}

export interface PlatformSummary {
  platform: string;
  /** a SCORED corpus exists — i.e. analyze has run. Not "a scrape has run"; see `scraped`. */
  has_data: boolean;
  /** raw scrape output is on disk. `scraped && !has_data` means analyze has not run yet. */
  scraped: boolean;
  /** handles in pages.txt. NOT `creators`, which counts the scored corpus and stays 0
      until analyze has run — two stages after the handle was added. */
  watchlist: number;
  /** reels pulled by the last scrape, straight off the raw dump. NOT `items`. */
  scraped_items: number;
  readiness: Record<string, StageReadiness>;
  items: number;
  creators: number;
  viral: number;
  media_ready: number;
  /** corpus rows that already have a blueprint. Polled, so it moves DURING a run —
      unlike the analysis query, which only refetches once the job settles. */
  analyzed: number;
}

export interface Reel {
  platform: string;
  creator: string;
  creator_followers: number | null;
  content_id: string;
  url: string;
  plays: number | null;
  virality_score: number | null;
  tier: string | null;
  reach_multiplier: number | null;
  outlier_score: number | null;
  engagement_rate: number | null;
  velocity: number | null;
  duration_s: number | null;
  caption: string | null;
  posted: string | null;
  video_url: string | null;
  thumb_url: string | null;
  video_local: boolean;

  // audio intelligence (D3) — the sound attached to this reel
  analyzed?: boolean;
  audio_id?: string | null;
  audio_title?: string | null;
  audio_artist?: string | null;
  audio_is_original?: boolean | null;
  audio_is_reusable?: boolean | null;
  sound_page_url?: string | null;
  audio_uses_count?: number | null;

  // client-only: the AnalysisEngine self-eval score, joined from /api/evals so
  // the Corpus grid can sort/filter by blueprint quality (§9.2 QC facet).
  eval_score?: number | null;
}

export interface Tier {
  label: string;
  min_score: number;
}

export interface ViralityConfig {
  weights: Record<string, number>;
  tiers: Tier[];
  top_n: number;
}

export interface Discovery {
  enabled: boolean;
  keywords: string[];
  seeds: string[];
  search_terms: number;
  per_query: number;
  max_candidates: number;
  min_followers: number;
  expand_related: boolean;
}

export interface NicheConfig {
  niche?: string;
  reels_per_creator?: number;
  discovery?: Discovery;
  virality?: ViralityConfig;
  [k: string]: unknown;
}

export interface ConfigResponse {
  config: NicheConfig;
  pages: string[];
}

export interface Factor {
  feature: string;
  bucket: string;
  n: number;
  mean_score: number;
  lift: number;
}

export interface FactorsResponse {
  // null on an empty corpus — corpus.py returns None when there is nothing to average.
  // Declaring it `number` was a type lie that white-screened the Board on a fresh install.
  baseline: number | null;
  all: Factor[];
  winners: Factor[];
  losers: Factor[];
}

export interface Proposal {
  file: string;
  text: string;
  // human-gate fields (§2). Legacy items with no metadata default to "draft".
  status?: string;
  agent?: string | null;
  kind?: string | null;
  created_at?: number;
  updated_at?: number;
  note?: string | null;
}

/* ---------------- renders (producer-generated media) ----------------
   Generated reels live in `renders/<platform>/<render_id>/`, served at
   /renders — never in /media, which holds the scraped corpus. One studio item
   maps to exactly one render dir, so `render.file` joins to `Proposal.file`. */

export interface RenderFrame {
  frame: string;
  kb?: number;
  provider?: string | null;
  on_screen_text?: string | null;
  /** Seconds this frame holds on screen; only the newest records carry it. */
  duration_s?: number | null;
}

export interface RenderAsset {
  name: string;
  bytes: number;
}

export interface RenderRecord {
  render_id: string;
  platform: string;
  file: string; // joins to Proposal.file exactly
  agent?: string | null;
  kind?: string | null;
  content_id?: string | null;
  slug?: string | null;

  // the caption generator ships later — null on every record today
  caption?: string | null;
  caption_model?: string | null;
  /** Ready-to-paste caption alternates, when the generator wrote any. */
  alt_captions?: string[];
  hashtags?: string[];

  duration_s?: number | null;
  width?: number | null;
  height?: number | null;
  /** Producer-declared frame shape, e.g. "9:16". Preferred over width/height
      when present; not every record carries it yet. */
  aspect_ratio?: string | null;
  /** How the producer fitted source frames into the output box ("cover"). */
  video_fit?: string | null;
  fps?: number | null;
  has_audio?: boolean | null;

  provider?: string | null;
  seed?: number | null;
  frames?: RenderFrame[];

  run_id?: string | null;
  evaluation?: Record<string, unknown> | null;
  /** Shots the producer skipped, when it reported any. */
  dropped_shots?: unknown[];
  note?: string | null;

  assets?: RenderAsset[];
  created_at?: number;
  updated_at?: number;

  // cache-busted by the hub (`?v=<updated_at ms>`) so a re-render repaints
  video_url?: string | null;
  poster_url?: string | null;
  local_path?: string | null;
  bytes?: number | null;
}

/* ---------------- producer registry (§3, §5) ---------------- */

export interface SecretStatus {
  name: string;
  env_var: string;
  required: boolean;
  present?: boolean | null; // resolvability, self-reported — never the value (§10.4)
}

export interface JSONSchemaProp {
  type?: string;
  default?: unknown;
  enum?: unknown[];
  description?: string;
  title?: string;
  minimum?: number;
  maximum?: number;
}

export interface JSONSchema {
  type?: string;
  properties?: Record<string, JSONSchemaProp>;
  required?: string[];
}

/* Every agent this checkout knows about, registered or not (GET /api/agents).

   Distinct from Producer, which is "who has registered". Registration is lazy — an agent
   registers when its CLI first runs — so on a clean install the producer roster is empty and
   the UI could say nothing about the key that gates the Blueprint stage. This type always
   carries the built-in trio, with `registered` telling you whether the rest of the manifest
   (config_schema, workflow_stages) is actually known. */
export interface AgentRosterEntry extends Partial<Producer> {
  name: string;
  registered: boolean;
  dir?: string | null;
  secrets?: SecretStatus[];
}

export interface Producer {
  name: string;
  kind: string | null;
  consumes: string[];
  human_gate: boolean;
  needs_reference: boolean;
  produces: string | null;
  output_status: string | null;
  config_schema?: JSONSchema | null;
  secrets?: SecretStatus[];
  registered_at?: number;
  updated_at?: number;
  workflow_stages?: string[];
}

export interface AgentBoardItem {
  content_id: string;
  stage: string;
  score?: number | null;
  file?: string | null;
  updated?: number | null;
}
export interface AgentRun {
  run_id: string;
  platform?: string | null;
  started?: number | null;
  ended?: number | null;
  counts: { total: number; done: number; failed: number };
  items: AgentBoardItem[];
}
export interface AgentBoard {
  agent: string;
  kind?: string | null;
  workflow_stages: string[];
  runs: AgentRun[];
}

export interface AgentConfigResponse {
  agent: string;
  config: Record<string, unknown>;
  defaults: Record<string, unknown>;
  config_schema: JSONSchema;
}

/* ---------------- audio intelligence (§2, D3) ---------------- */

export interface TrendingSound {
  audio_id: string;
  title: string;
  artist: string;
  is_original: boolean;
  is_reusable: boolean;
  sound_page_url: string;
  uses_in_corpus: number;
  uses_count_meta?: number | null;
  recent_uses?: number;
  trend_score: number;
  bucket: string; // Rising | Hot | Steady | Saturated | …
  example?: { content_id: string; url: string; virality_score: number } | null;
}

/* ---------------- analysis blueprint v2 (§2, §4, D1) ---------------- */

export interface BlueprintVideoMeta {
  estimated_duration_seconds?: number;
  aspect_ratio?: string;
  resolution?: string;
  fps?: number;
  content_type?: string;
  one_line_summary?: string;
  detailed_summary?: string;
  target_platform?: string;
  likely_ai_generated?: boolean;
  ai_generation_signals?: string[];
  total_shots?: number;
}

export interface BlueprintGlobalStyle {
  overall_mood?: string;
  genre?: string;
  visual_style?: string;
  color_grading?: string;
  dominant_color_palette_hex?: string[];
  lighting_style?: string;
  pacing?: string;
  editing_style?: string;
  recurring_visual_motifs?: string[];
  film_look_reference?: string;
}

export interface BlueprintAudio {
  music_description?: string;
  music_genre?: string;
  tempo_bpm_estimate?: number;
  music_mood?: string;
  has_voiceover?: boolean;
  voiceover_transcript?: string;
  has_lyrics?: boolean;
  lyrics_transcript?: string;
  sound_effects?: string[];
  audio_sync_notes?: string;
  audio_id?: string;
  audio_title?: string;
  audio_artist?: string;
  audio_is_original?: boolean;
  audio_is_reusable?: boolean;
  sound_page_url?: string;
}

export interface BlueprintAudioStrategy {
  audio_type?: string;
  voiceover_role?: string;
  music_role?: string;
  beat_markers_s?: number[];
  reuse_recommendation?: string;
  substitute_brief?: string;
  sync_notes?: string;
}

export interface BlueprintCharacter {
  id: string;
  role: string;
  detailed_appearance: string;
  appears_in_shots?: number[];
}

export interface BlueprintTextOverlay {
  start_time: number;
  end_time: number;
  text: string;
  font_style?: string;
  color?: string;
  position?: string;
  animation?: string;
}

export interface BlueprintShot {
  shot_index: number;
  start_time: number;
  end_time: number;
  duration: number;
  description?: string;
  generation_prompt: string;
  negative_prompt?: string;
  subjects_present?: string[];
  setting_location?: string;
  action_motion?: string;
  camera_shot_size?: string;
  camera_angle?: string;
  camera_movement?: string;
  lens_feel?: string;
  lighting?: string;
  color_palette_hex?: string[];
  mood?: string;
  on_screen_text?: string;
  transition_in?: string;
  transition_out?: string;
}

export interface BlueprintRegenGuide {
  recommended_models?: string[];
  master_style_prompt?: string;
  global_negative_prompt?: string;
  consistency_notes?: string;
  assembly_instructions?: string;
  shot_prompt_sequence?: unknown[];
}

export interface BlueprintViralityFormula {
  hook?: { type?: string; first_seconds?: string; on_screen_text?: string };
  retention_devices?: unknown[];
  pacing?: { cuts?: number; avg_shot_len_s?: number };
  cta?: { present?: boolean; text?: string };
  replicable_formula?: string;
  tags?: string[];
}

export interface BlueprintEvaluation {
  score_0_100?: number;
  per_criterion?: Record<string, number>;
  passes?: number;
  gaps_remaining?: string[];
  accepted?: boolean;
  judge_model?: string;
  verdict?: string;
}

export interface Blueprint {
  content_id: string;
  schema_version: number;
  url?: string | null;
  model?: string;
  analyzed_by?: string;
  duration_s?: number | null;
  is_reference?: boolean;
  platform?: string;
  analyzed_at?: number;

  video_metadata?: BlueprintVideoMeta;
  global_style?: BlueprintGlobalStyle;
  audio?: BlueprintAudio;
  audio_strategy?: BlueprintAudioStrategy;
  characters_and_subjects?: BlueprintCharacter[];
  text_overlays?: BlueprintTextOverlay[];
  shots?: BlueprintShot[];
  regeneration_guide?: BlueprintRegenGuide;
  virality_formula?: BlueprintViralityFormula;
  evaluation?: BlueprintEvaluation;
}

/* ---------------- reference intake (§2, §8) ---------------- */

export interface ReferenceItem {
  content_id?: string;
  ref_id?: string;
  id?: string;
  url?: string;
  status?: string;
  analyzed?: boolean;
  is_reference?: boolean;
  created_at?: number;
  note?: string | null;
  [k: string]: unknown;
}

/* ---------------- discovery / AutoSearch candidates (§11.2, §11.4) --------
   Mirrors discovery/{platform}/candidates.json rows exactly (verified against
   the live GET /api/discovery/instagram/pending response — `handle` is the
   full profile URL the agent hydrated, not a bare @handle). */

export interface CandidateRelevance {
  score: number; // 0..1, Claude relevance judgment
  reasons: string[];
}

export const CANDIDATE_STATUSES = ["pending", "approved", "rejected"] as const;
export type CandidateStatus = (typeof CANDIDATE_STATUSES)[number];

export interface Candidate {
  candidate_id: string;
  handle: string;
  platform: string;
  source_term?: string | null;
  discovered_via: string;
  followers?: number | null;
  median_plays?: number | null;
  sample_reels: string[];
  relevance: CandidateRelevance;
  status: string; // CandidateStatus, kept loose to tolerate future values
  in_pages: boolean; // derived: handle already a non-comment line in pages.txt
  added_at: number;
  updated_at: number;
  ts?: number;
}

/* ---------------- platform-wide concerns (§10) ---------------- */

export interface LogEvent {
  agent: string;
  level: string; // info | warn | error | …
  event: string;
  msg?: string | null;
  run_id?: string | null;
  platform?: string | null;
  content_id?: string | null;
  ts: number;
  data?: Record<string, unknown> | null;
}

export interface EvalRecord {
  agent: string;
  target_type: string; // blueprint | clone | proposal | …
  target_id: string;
  scores?: Record<string, number> | null;
  verdict?: string | null;
  judge?: string | null;
  notes?: string | null;
  platform?: string | null;
  ts: number;
}

export interface Insight {
  ts: number;
  platform: string;
  kind: string; // method | negative | finding | ...
  text: string;
  tags: string[];
}

export interface Job {
  platform: string;
  stage: Stage;
  status: JobStatus;
  started: number;
  ended: number | null;
  rc: number | null;
  tail: string;
}

// SSE /api/events streams the whole JOBS dict, keyed "platform:stage:seq".
export type Jobs = Record<string, Job>;

/** Automatic run settings for one platform.

    There is no daemon outside the hub, so this only fires while the hub is running — it is
    a best-effort "while you have this open", not a cron guarantee, and the UI says so.
    `include_blueprints` is opt-in because that stage calls a paid API on every clip. */
export interface ScheduleRow {
  enabled: boolean;
  every_hours: number;
  include_blueprints: boolean;
  last_run_at: number;
  stages: Stage[];
  next_run_at: number | null;
}

/** The four stages the cascade can fire, in pipeline order. `render` is deliberately
    absent — the hub's CASCADE_STAGES has no render entry, so no configuration can make
    the chain spend image credits. The chain stops at the studio; rendering waits for you. */
export const CASCADE_STAGES = ["analyze", "media", "analysis-engine", "propose"] as const;
export type CascadeStage = (typeof CASCADE_STAGES)[number];

/** GET /api/cascade — one row per platform, with the boundary arithmetic already done.

    The cascade is the second heartbeat: the timer (`ScheduleRow`) decides WHEN whole runs
    happen, the cascade decides when the single next stage happens, keyed on how much NEW
    input has landed rather than on a clock.

    `enabled` as returned by GET is the EFFECTIVE value: a row carrying a `problem` reports
    `enabled: false` and an empty `due`, because that is the truth about what will happen.
    The stored intent survives on disk, so fixing the funnel brings the platform back. */
export interface CascadeRow {
  enabled: boolean;
  /** may the analysis-engine boundary fire? It calls a PAID API once per clip. */
  include_blueprints: boolean;
  /** high-water marks — how much input each boundary has already consumed. Machine-owned;
      accepted-but-ignored on PUT. */
  marks: Record<CascadeStage, number>;
  /** how many recipes one propose firing publishes (1..25), clamped by availability.
      NOT the same thing as `propose_pct` — that is how much of the blueprint output
      reaches the propose boundary, this is what one firing then puts on the gate. */
  propose_count: number;

  // ---- the funnel: one absolute batch size, then percentages of the row above ----
  // The whole configuration, and the only part of it a human sets. `steps` used to be the
  // input and could be typed into a shape where a later boundary fired more often than the
  // one feeding it; percentages capped at 100 make that impossible by construction.
  /** the batch size that anchors the funnel (1..5000) */
  scrape_count: number;
  /** % of the scraped batch that reaches the analyze boundary (1..100) */
  analyze_pct: number;
  /** % of the analyzed output that reaches the media boundary (1..100) */
  media_pct: number;
  /** % of the media output that reaches the analysis-engine boundary (1..100) — PAID */
  blueprint_pct: number;
  /** % of the blueprint output that reaches the propose boundary (1..100) */
  propose_pct: number;

  // ---- derived by the hub, never written back ----
  /** how much NEW input each boundary needs before it fires, keyed by the stage that
      fires. DERIVED from the percentages above — read-only, and ignored on PUT. */
  steps: Record<CascadeStage, number>;
  stages: CascadeStage[];
  /** the live input count for each boundary, in that boundary's own unit */
  counts: Record<CascadeStage, number>;
  /** boundaries that would fire on the next tick */
  due: CascadeStage[];
  /** the count at which each boundary next comes due (marks + steps) */
  next_at: Record<CascadeStage, number>;
  /** non-null when the stored configuration refuses to run — show this sentence in place
      of the toggle state rather than leaving a platform silently off. */
  problem: string | null;
  /** the registered producer that proposes, or null when none/several do */
  propose_agent: string | null;
  propose_agent_problem: string | null;
}
