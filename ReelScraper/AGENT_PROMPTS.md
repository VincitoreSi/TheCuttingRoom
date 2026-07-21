# Bootstrap prompts for the remaining agents

This repo is the **backend agent** of the pipeline: it runs the local API hub
(`python cli.py start` → `http://127.0.0.1:8787`, docs at `/docs`) that every other agent
and the frontend connect to. Below are copy-paste prompts to create each remaining agent.

**Set these once in each new session's shell:**
```
export BACKEND_DIR=~/TheCuttingRoom/ReelScraper   # this repo
export BACKEND_API=http://127.0.0.1:8787                        # hub (run `cli.py start` here first)
```

The **producer** and **frontend** agents live in their OWN empty directories and talk to
the hub over HTTP. The **platform analyst** agents work INSIDE `$BACKEND_DIR` (they add a
scraper to `platforms/<p>/`).

---

## A) Platform analysts — run inside `$BACKEND_DIR` (build the missing scrapers)

### x-analyst
```
Work in this repo. Implement platforms/x/scrape.py — an X/Twitter scraper for the handles
in platforms/x/pages.txt. It must emit platforms/x/posts_raw.json {handle:[post,...]} and
platforms/x/profiles_meta.json {handle:{"followers":int}}, matching the exact raw shape
documented in platforms/x/normalize.py (id, created_at, text, public_metrics{impression_count,
like_count, reply_count, retweet_count, quote_count, bookmark_count}, video{duration_s}).
Scrape safely: prefer official/public read APIs, pace requests, add a 3-strike rate-limit
circuit breaker, and support resume (skip handles already saved). Do NOT log into a personal
account. Then verify with: uv run run.py analyze  (scores + memory + content.json).
Keep tuning in platforms/x/niche_config.json. Report items/creators/viral counts when done.
```

### yt-analyst
```
Work in this repo. Implement platforms/youtube/scrape.py — a YouTube Shorts scraper for the
channels in platforms/youtube/pages.txt using the YouTube Data API v3 (search+videos+channels).
Emit platforms/youtube/shorts_raw.json {channel:[video,...]} and profiles_meta.json
{channel:{"followers": subscriberCount}}, matching platforms/youtube/normalize.py (snippet
{publishedAt,title,description}, statistics{viewCount,likeCount,commentCount}, contentDetails
{duration}). Read the API key from an env var; pace requests; support resume. Then verify:
uv run run.py analyze. Tune platforms/youtube/niche_config.json. Report counts.
```

---

## B) Producer agents — each in its OWN empty directory, talk to the hub over HTTP

All read the corpus and write proposals THROUGH the hub (`$BACKEND_API`), never scraping:
- read:  `GET /api/corpus/{platform}/factors` · `/brief?q=` · `/top?n=` · `/search?q=` · `GET /api/content/{platform}`
- read (NEW — video craft):  `GET /api/analysis/{platform}` (all) · `GET /api/analysis/{platform}/{content_id}` (one).
  The `/brief` now embeds a **"Visual formulas"** section from the frame-by-frame analysis of top clips.
- write: `POST /api/studio/{platform}` `{filename, text}` · `POST /api/insights` `{platform,kind,text,tags}`

> **Design update — video frame-by-frame analysis.** A dedicated analysis agent (section D,
> since superseded by **AnalysisEngine**) uses
> Gemini to break down the top clips shot-by-shot (hook, beats, visual style, on-screen text,
> audio, retention devices, a `replicable_formula`) and writes them to the hub. Producer agents
> should now generate from the **visual mechanics**, not just captions + metrics. The adaptation
> note in section E tells each existing agent exactly what to change.

### similar-content
```
You are being created as a standalone Claude Code agent "similar-content" in this empty dir.
Scaffold it: create CLAUDE.md defining your job — generate fresh short-form content ideas
MODELED ON the top viral videos in an external corpus. You never scrape.
Backend hub is at $BACKEND_API (start it in the backend repo with `python cli.py start`).
Verify: curl -s $BACKEND_API/api/corpus/instagram/factors
Method: pull GET /api/corpus/<platform>/brief and /top; cluster the top viral exemplars by
hook/format; for the densest clusters produce 2-3 adjacent FRESH ideas each; every idea must
honor the winning virality factors and avoid the negative ones from the brief.
Output each idea (modeled-on, hook, format/beats, duration, caption + hashtag count, audio,
why-it-travels) and POST the batch as one markdown file to /api/studio/<platform>
{filename:"<date>-similar-<slug>.md", text:"..."}. Then POST /api/insights
{platform, kind:"idea", text:"SimilarContent: <themes>", tags:["studio"]}.
Confirm the hub connection, then stop and wait for a platform + topic.
```

### proposal-content
```
You are being created as a standalone Claude Code agent "proposal-content" in this empty dir.
Scaffold CLAUDE.md: you turn the corpus into DECISIONS — 5 full script proposals, a debate,
then a human gate. You never scrape. Backend hub at $BACKEND_API.
Verify: curl -s $BACKEND_API/api/corpus/instagram/factors
Method: (1) GET /api/corpus/<p>/factors + /brief?q=<topic>; summarize winning factors + style.
(2) Draft 5 full mini-scripts (hook, beat-by-beat shot list, duration, caption+hashtag count,
audio, factor-mapping). (3) DEBATE each for/against the factors + negative patterns; score
0-100 on factor-fit/originality/feasibility/hook; rank — be a harsh critic. (4) HUMAN GATE:
present the top 3 with AskUserQuestion so the user picks. POST all 5 + the debate to
/api/studio/<p> {filename:"<date>-proposals-<slug>.md", text}. Log the pick:
POST /api/insights {platform,kind:"idea",text:"Greenlit: <title> — <why>",tags:["studio","greenlit"]}.
Confirm the hub, then stop and wait for a platform + topic.
```

### auto-content
```
You are being created as a standalone Claude Code agent "auto-content" in this empty dir.
Scaffold CLAUDE.md: you synthesize the WHOLE corpus into ONE highest-conviction "most likely
to go viral" concept. You never scrape. Backend hub at $BACKEND_API.
Verify: curl -s $BACKEND_API/api/corpus/instagram/brief
Method: GET /api/corpus/<p>/factors + /brief + /search?q=<themes> + /api/insights. Converge on
ONE concept: full script (beats, on-screen text, VO, transitions); a spec (duration, caption,
exact hashtag count, audio) where EVERY choice maps to a winning factor and avoids every
negative; a factor-ledger table (choice → evidence); predicted tier; and the 2-3 biggest
risks (honest — "for sure" = evidence-maximal, not guaranteed). POST to /api/studio/<p>
{filename:"<date>-auto-<slug>.md", text}. Log: POST /api/insights {platform,kind:"idea",
text:"AutoContent bet: <concept>",tags:["studio","auto"]}. Confirm hub, then wait for a platform.
```

---

## C) Frontend agent — its OWN empty directory (the end-goal creative board)

```
You are being created as a standalone Claude Code agent "frontend" that builds an
industry-level, animated React control board for a local content-virality pipeline. The
backend hub already runs at $BACKEND_API (FastAPI; OpenAPI at $BACKEND_API/openapi.json —
FETCH IT FIRST and build strictly against that contract).

STACK: Vite + React + TypeScript + Tailwind + shadcn/ui + Framer Motion (animation) +
TanStack Query + Recharts. Native <video> for inline playback. Dev server proxies /api and
/media to $BACKEND_API; production is a static build.

DELIVERY: the final app is served BY the hub — build to ./dist and copy it to
$BACKEND_DIR/frontend/dist so `python cli.py start` opens the whole app on localhost. Same-
origin in prod; use a proxy in dev.

SCREENS (build the Pipeline Board + Content grid first as MVP, then the rest):
1. PIPELINE BOARD (hero): an animated left-to-right flow — Sources → Scrape → Analyze →
   Media → Studio. Each stage is a live node (idle/running/done/error) driven by the SSE
   stream GET /api/events + GET /api/pipeline/status; animated connectors + counts; each node
   has a Run button → POST /api/pipeline/{platform}/{stage}. Platform switcher from
   GET /api/platforms.
2. CONTENT / VIRALITY: GET /api/content/{platform} → a grid of reels with INLINE VIDEO
   (poster = thumb_url; play video_url on hover, full player + details on click), tier badges,
   score, reach/outlier/engagement/velocity, sort/filter. Factor charts from
   GET /api/corpus/{platform}/factors (winners vs drags). Creator leaderboard.
3. CONFIG (one place): GET/PUT /api/config/{platform} — weight sliders (must sum-normalize),
   tier thresholds, discovery keywords, and the pages.txt list — all editable + saved here.
4. STUDIO: GET /api/studio/{platform} → proposal cards (render the markdown); a human-gate
   approve action; play video inline when a proposal carries a rendered asset.
5. INSIGHTS: GET /api/insights → shared findings + negative patterns.

---
Design and build a premium, cinematic UI for the virality pipeline — a local-first
copilot that mines viral posts in the configured niche, analyzes them, proposes scripts,
and renders Reels. It must feel like a bespoke product built for THIS pipeline, never a template.

── VISUAL LANGUAGE: "ATELIER NOIR" ──────────────────────────────────────────
Dark-couture, candle-lit atelier. Espresso leather, bone ink, an oxblood seam
pulled through brass. Calm, editorial, tactile — the opposite of neon SaaS dark mode.

DARK (default identity):
  bg          #17130f  espresso        surface   #201911  leather card
  bg-tint     #1e1712                  surface-2 #2a2118
  line        #3a2e23  warm hairline   line-str  #4d3e2f
  ink         #ede6d8  bone (text)     ink-2     #d0c6b4
  ink-dim     #a2917c                  ink-faint #6f6252
  BRASS       #c9a24b  active/selected/focus/highlight   brass-ink #e2bf6c
  SAGE        #93a06a  success/approve/done              sage-wash #23281a
  OXBLOOD     #b8402f  brand / "the thread" / links      oxblood-ink #d6684f
  amber       #cf9a3a  warn        danger #e05a44  (hotter red — the ONLY error color)
  THREAD GRADIENT (the seam, use sparingly): linear-gradient(120deg,#b8402f 0%,#bd6032 46%,#c9a24b 100%)

LIGHT ("atelier by day" — a warm-paper variant, NOT an inverted dark theme):
  bg #f1ebdf  surface #fbf7ef  ink #241c13  brass #9a7a24  sage #5f6e3e
  oxblood #9a3327  danger #c23b2a
  thread: linear-gradient(120deg,#9a3327 0%,#ab5228 46%,#a4831f 100%)

COLOR SEMANTICS (enforce, don't decorate):
  • The thread gradient marks ONLY things the agent acts through: primary actions,
    score bars, live status, active nav. Never as generic background flair.
  • Brass = attention/active/selected/focus. Sage = success/approve/done.
  • Oxblood = brand + links + "working". The hotter red (#e05a44) is reject/error ONLY.
  • Radii: 15 / 9 / 6px. Shared control height 38px (buttons, inputs, selects align to the px).
  • Easing everywhere: cubic-bezier(0.22, 1, 0.36, 1).
  • Ambient texture (zero image cost): a warm top glow + faint diagonal twill weave
    painted in the ink color via color-mix, so it holds in either theme.
  • Type: serif display (Iowan Old Style / Palatino / Georgia), system sans body,
    mono for numerics/keys. No web fonts — offline, $0.

── MOTION & INTERACTION ──────────────────────────────────────────────────
  • Framer Motion for page transitions AND node/list transitions.
  • Micro-interactions: hover lift on cards (translateY -2/-3px + deeper s
    staggered reveal-on-mount, score bars that scaleX-grow, buttons that press (scale .98).
  • Signature agent-status motif: a needle-and-thread "seam" that sews its
    working (dashoffset loop + needle bob), finishes as a solid sage seam with a knot
    on done, and snaps in two (red) on error — animate transform/stroke on
  • A "next step" banner with a slow sheen sweep across the thread-soft gradient.
  • ALL motion yields to prefers-reduced-motion (kill animations/transitio

── PRODUCT STRUCTURE ─────────────────────────────────────────────────────
  Sidebar nav (Dashboard, Corpus, Watchlist, Proposals, Shelf, Renders, Playbook,
  Config) with an active-state brass wash + thread accent bar. Sticky glas
  (backdrop-blur, saturate) carrying the live agent seam-status and current model.
  Dashboard: stat tiles (one big numeral in the thread gradient), a pipeli
  of stage pills, and the next-step banner.

── ENGINEERING ───────────────────────────────────────────────────────────────
  • Fully responsive: sidebar collapses to a horizontal rail < 860px; grid
    4→2→1; content padding tightens. No horizontal body scroll ever.
  • Accessible: WCAG-AA contrast in BOTH themes, visible focus ring
    (0 0 0 3px brass @ 30%), keyboard nav, semantic landmarks, respects color-scheme.
  • Performant: virtualize the content/corpus grid (windowed rendering) so
    of mined posts scroll at 60fps; lazy-load media; animate only transform/opacity.
  • Theme via CSS custom properties + [data-theme] override that beats the
    prefers-color-scheme default; single source of truth for tokens.

Follow strong visual-design principles — intentional hierarchy, restraint, and a
consistent system. Every gradient, motion, and color must mean something i
pipeline. Make it feel couture, not templated.
Scaffold the project, fetch the OpenAPI, build the MVP (board + content grid with inline
video + config), run it against the hub, then stop and report what's built + what's next.
```

Frontend addition for the design update: add an **Analyzed** badge to content cards
(`analyzed` flag on `GET /api/content/{platform}`; count on `GET /api/platforms`), and an
analysis panel that renders `GET /api/analysis/{platform}/{content_id}` — the shot-by-shot
beats as a timeline over the player, plus hook / visual style / retention devices / the
`replicable_formula`. A "Analyze pending" action can surface `GET /api/analysis/{platform}/pending`.

---

## D) VideoAnalysis agent — HISTORICAL, superseded

> **Superseded — do not build this.** This prompt produced a scratch prototype in a
> `VideoAnalysis/` directory that **no longer exists in the repo**. Its role is filled by
> **AnalysisEngine**, which writes richer `schema_version: 2` blueprints (see section F and
> `AnalysisEngine/CLAUDE.md`). The schema below is the *original v1* shape and is not what
> the pipeline uses today. Kept only to explain how the analysis stage came about.

Fills the gap between *what scored* (metrics) and *why it worked on screen* (craft). It reads
the top clips' locally-saved videos through the hub, has **Gemini** watch them frame-by-frame,
and writes structured breakdowns back to the hub for the producer agents to copy.

```
You are being created as a standalone Claude Code agent "video-analysis" in this EMPTY dir.
You use Google Gemini to analyze short-form videos FRAME BY FRAME and write structured craft
breakdowns back to a local pipeline hub. You never scrape and never generate content ideas.

SCAFFOLD (uv-managed, matching the pipeline ecosystem):
  uv init . ; uv add google-genai requests
  Create CLAUDE.md documenting the job, the env vars, and `uv run analyze.py`.
  Read GEMINI_API_KEY from env (the google-genai SDK auto-detects it; GOOGLE_API_KEY also works).
  Add production logging: per-run log file named by start_time (logs/<start_time>_analyze.log),
  pretty console + JSONL, mirroring the backend's core/logsetup.py convention.

HUB: $BACKEND_API (start the backend with `uv run cli.py start`). Verify first:
  curl -s $BACKEND_API/api/analysis/instagram/pending   # clips to analyze (needs media downloaded)
If pending is empty, the backend must first run `media` for that platform (download_media.py)
so videos exist locally — tell the user; you do NOT scrape or download-scrape yourself.

MODEL: default gemini-2.5-flash (fast, cheap, strong multimodal). Configurable via env
GEMINI_MODEL. Input = the NATIVE VIDEO via the Gemini Files API (Gemini samples the frames
itself, ~1 fps — this IS the frame-by-frame pass, with audio + motion). Do NOT extract frames
with ffmpeg.

LOOP (per platform the user names):
  1. GET $BACKEND_API/api/analysis/<p>/pending  → clips ranked by virality (content_id, video_url,
     duration_s, caption, url). Take the top N (default 15; --limit override). Resume is automatic
     (analyzed clips drop off /pending).
  2. For each clip: download the mp4 from  $BACKEND_API + video_url  to a temp file.
  3. Upload it with the Files API (client.files.upload), wait until state ACTIVE, then call
     client.models.generate_content(model, [file, PROMPT]) with
     config={response_mime_type:"application/json", response_schema: <the schema below>}.
     PROMPT: "Watch this short-form video frame by frame and return the analysis as JSON.
     Focus on the CRAFT another creator would copy: the hook in the first 1-3s, a shot-by-shot
     beat timeline (timestamps, what's on screen, shot type, on-screen text), visual style
     (color, lighting, editing pace, camera), audio (music/voiceover/dialogue/trend), the
     retention devices, any CTA, and a one-line replicable_formula. Be concrete and specific."
  4. POST the result to $BACKEND_API/api/analysis/<p>  (JSON body). Ensure `content_id` matches
     the clip. Delete the uploaded file (client.files.delete) to stay tidy.
  5. Pace between clips (a few seconds); on repeated Gemini errors (3 in a row) STOP and report
     (circuit breaker) — don't burn quota. Log every step; report analyzed/failed counts.

OUTPUT JSON SCHEMA (must match the hub's VideoAnalysisIn — content_id required, rest optional):
  {
    "content_id": str,                 # REQUIRED — the clip id from /pending
    "duration_s": number,
    "summary": str,                    # 1-2 sentences: what happens
    "hook": {"type": str, "first_seconds": str, "on_screen_text": str},
    "beats": [ {"t_start": number, "t_end": number, "description": str,
                "shot_type": str, "on_screen_text": str} ],   # the frame-by-frame timeline
    "visual_style": {"color_palette": str, "lighting": str, "editing_pace": str,
                     "camera": str, "transitions": str},
    "subjects": [str], "setting": str,
    "text_overlay": {"present": bool, "density": str, "style": str, "key_phrases": [str]},
    "audio": {"music_type": str, "voiceover": bool, "dialogue": bool, "trend_audio": bool},
    "pacing": {"cuts": number, "avg_shot_len_s": number},
    "retention_devices": [str],
    "cta": {"present": bool, "text": str},
    "tags": [str],
    "replicable_formula": str,         # how to recreate this format in one line
    "model": "gemini-2.5-flash"
  }
The hub stamps `platform` + `analyzed_at` on save. Confirm the hub connection and that
GEMINI_API_KEY is set, then stop and wait for the user to name a platform.
```

---

## E) Adaptation note — paste into EACH existing agent to adopt the design change

Give this to **similar-content**, **proposal-content**, **auto-content**, and the **frontend**
agent (send it to each running agent, or add it to their CLAUDE.md):

```
DESIGN UPDATE — video frame-by-frame analysis is now in the pipeline.
A new VideoAnalysis agent uses Gemini to break down the top-performing clips shot-by-shot and
stores the result on the hub. Adapt as follows (you still never scrape):

• New reads on the hub:
    GET /api/analysis/{platform}            → all analyses (newest first)
    GET /api/analysis/{platform}/{id}       → one clip's frame-by-frame breakdown
  Each record has: hook{type,first_seconds,on_screen_text}, beats[] (timestamped shot list),
  visual_style, text_overlay, audio, pacing, retention_devices, cta, and a replicable_formula.
  GET /api/content/{platform} now includes an `analyzed` flag per item.

• The generation brief (GET /api/corpus/{platform}/brief) now contains a "Visual formulas"
  section drawn from these analyses.

• CHANGE YOUR METHOD: generate from the VISUAL MECHANICS, not just captions + metrics. For a
  concept modeled on an exemplar, pull that clip's analysis and reuse its hook type, beat
  structure/shot list, on-screen-text style, pacing, and retention devices. Put a concrete
  beat-by-beat shot list in every idea/script and cite the analyzed clip it derives from
  (content_id) in your factor-mapping. If a top exemplar has no analysis yet, say so and lean
  on metrics + caption for that one.

Confirm you can read /api/analysis and that /brief shows "Visual formulas", then continue.
```

---

## F) Finalized pipeline contract (The Cutting Room rollout — supersedes/extends the above)

The hub is now the single integration point for a pluggable **producer** ecosystem. See
`../PIPELINE.md` (§2 hub contract, §3 Producer SPI, §10 platform-wide concerns) and
`../AnalysisEngine.build-prompts.md` (D1/D2a/D3a). `content_id` = content join key;
**`audio_id` = sound join key**. All additions are backward-compatible.

### AnalysisEngine (supersedes the VideoAnalysis scratch in section D)
Produces the rich, generation-ready **schema-2 blueprint** and writes it to `POST /api/analysis/<p>`.
Reads `GET /api/analysis/<p>/pending` (now filterable: `min_score, tier, min_duration, max_duration,
content_type, limit, reanalyze=<id>, stale=true`) — pending items now carry the clip's `audio_*`
fields so the engine can fill its `audio` block. Also consumes the reference queue
(`GET /api/reference/<p>/pending`) and saves those blueprints with `is_reference:true`.

### Every producer (SimilarContent + future proposal/idea/template agents) — the SPI
On startup: `POST /api/producers/register` with `{name, kind, consumes[], human_gate,
needs_reference, produces, output_status, config_schema, secrets[], workflow_stages[]}` (secrets by
env-var NAME only). `workflow_stages` is the ordered lane list for that agent's board (e.g.
`["Queued","Analyzing","Self-eval","Done"]` for analyzers, `["Queued","Generating","Self-eval",
"Proposed","Approved","Rejected"]` for producers). Then `GET /api/config/agent/<name>` for config,
report secret status, and run.
- **Read:** `GET /api/corpus/<p>/{factors,brief,top,search}`, `GET /api/analysis/<p>[/<id>]`,
  `GET /api/audio/<p>/trending`, `GET /api/insights`; reference agents also `GET /api/reference/<p>`.
- **Write:** `POST /api/studio/<p>` `{filename, text, agent, kind, status}` (filename
  `<date>-<agent>-<slug>.md`), `POST /api/insights`, `POST /api/logs` (lifecycle events),
  `POST /api/evals` (self-eval per artifact). Include the `## Audio` block (companion D3c).
  On a **first** insert an omitted `status` becomes `proposed`; on a re-POST the item's existing
  status is **preserved**, so re-posting your own markdown cannot un-approve it.
- **Human gate:** if `human_gate:true`, write `status:"proposed"`; a human flips it via
  `POST /api/studio/<p>/<file>/status`. Approved is **not** terminal: a producer that declares
  `renderable:true` can then be launched on an approved item via
  `POST /api/studio/<p>/<file>/render`, which uploads the finished reel to
  `POST /api/renders/<p>`. Rendering is paid and human-triggered — never part of a pipeline run.
- **Fine-grained lifecycle events** (per-item, for the live agent board): in addition to the coarse
  `run.start`/`item.done`/`run.end` vocabulary, agents may POST `/api/logs` with `item.start`
  (item begins; `data.stage` = the agent's first working lane), `item.stage` (mid-item transition;
  `data.stage`), and `item.error` (item failed; lands in the implicit `Failed` lane). `data.stage`
  MUST be one of the agent's declared `workflow_stages`. `item.done` should set `data.stage` to the
  agent's terminal stage (`Done` for analyzers, `Proposed` for producers — include `data.file` so
  the board can join the human-gate decision) and may include `data.score`. `GET /api/agents/<name>/board`
  `?platform=&limit_runs=` reduces this stream (joined with studio gate status for producers) into
  `{agent, kind, workflow_stages, runs:[{run_id, platform, started, ended, counts, items:[{content_id,
  stage, score, file, updated}]}]}` for the Dashboard's per-agent task board.

### Audio (all producers)
`GET /api/audio/<p>/trending?reusable_only=&mood=<brief>` returns ranked sounds
`{audio_id, title, artist, is_original, is_reusable, sound_page_url, uses_in_corpus, trend_score,
bucket, example}`. Buckets: Rising|Hot|Saturated|Evergreen (trending WITHIN tracked creators — not
the platform-wide chart). Emit the copy-ready `## Audio` block (exact IG sound name + link).

### AutoSearch (kind `discovery`) — the discovery front door (PIPELINE.md §11)
Not a producer — a SOURCE-side agent, alongside ReelScraper the only one permitted to touch
Instagram (read-only, guest-first, burner-opt-in, paced strictly slower than the scraper, with a
kill-switch). It searches for new creators, scores niche-fit, and posts **candidates** for human
approval; approved candidates get appended to `pages.txt` by the HUB (never by the agent itself).
Full build prompt: `../AutoSearch/PIPELINE.md`.
- **Write:** `POST /api/discovery/<p>` `{handle, source_term, discovered_via, followers,
  median_plays, sample_reels[], relevance:{score,reasons[]}}` — upserted by `candidate_id`
  (agent-supplied or a stable `cand_<sha1(platform:handle)>` hash); the hub forces
  `status:"pending"` on first insert and never silently un-gates an already-decided candidate.
- **Read back:** `GET /api/discovery/<p>[?status=]`, `GET /api/discovery/<p>/pending`. A human
  approves/rejects via `POST /api/discovery/<p>/<candidate_id>/status` `{status, note}` in the
  Dashboard — on approval the hub appends the handle to `pages.txt` (safe/deduped/comment-preserving)
  and the next scrape picks it up. AutoSearch posts nothing for this step; the board's
  `kind=="discovery"` gate-join (keyed on `content_id == candidate_id`) reflects the decision.
- **Cadence:** weekly budget → randomized daily allotments → thin heartbeat trickle
  (`STAGE_CMD["auto-search-beat"]`), fired by an opt-in hub scheduler thread gated on the
  per-agent kill-switch `discovery_enabled` (`config/agents/auto-search.json`, default **false**).
  `STAGE_CMD["auto-search"]` is the manual/exhaustive pass. Both shell out to `../AutoSearch`.
- Register with `workflow_stages: ["Queued","Searching","Scoring","Proposed","Approved","Rejected"]`
  and `kind:"discovery"` so the Agent Desk board renders correctly and the gate-join applies.
- `platforms/instagram/discover.py` / `find_profiles.py` are superseded by this agent for ongoing,
  hub-gated discovery — they remain in this repo for manual/offline use only.
