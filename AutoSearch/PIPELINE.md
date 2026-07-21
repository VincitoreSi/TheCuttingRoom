# AutoSearch — build prompt (self-contained)

**Paste this into a fresh Claude Code session opened in `~/TheCuttingRoom/AutoSearch`.**
It is self-contained. AutoSearch is the pipeline's **discovery** agent — the new front door that finds new
Instagram creators worth scraping, scores them for niche-fit, and proposes them to the hub for human approval
into `pages.txt`. It mirrors the shape of the sibling `AnalysisEngine` agent (uv + `engine/` + `memory/`).

Pipeline position (new 7-stage shape):
```
Discover → Sources → Scrape → Analyze → Media → Blueprint → Studio
   ▲ AutoSearch
```

Read `../PIPELINE.md §11` (the integration design + per-agent effects + frontend) and
`../AnalysisEngine.build-prompts.md` (the ecosystem conventions to match) before writing code. Build strictly
against the hub's live `/openapi.json` at `http://127.0.0.1:8787`.

---

## 0. Identity

- **name** `auto-search`, **kind** `discovery`. Own directory, CLAUDE.md-driven, `uv`-managed Python ≥3.10.
- **Prime directive:** read work and write results **only through the hub** (`BACKEND_API`, default
  `http://127.0.0.1:8787`). Never import ReelScraper or any sibling's code. Never write into another
  project's directory. AutoSearch is, alongside ReelScraper, the **only** agent permitted to touch Instagram —
  and only read-only, guest-first, burner-opt-in (see §1 SAFETY). Producers never scrape.
- **Environment:** `BACKEND_API` (hub URL), `ANTHROPIC_API_KEY` (required — term expansion + relevance
  scoring), `IG_SESSIONID` (optional burner, login-gated surfaces only). Secrets from env / gitignored `.env`
  or `session.txt` only.

---

## 1. SAFETY SPECIFICATION — NON-NEGOTIABLE (embed verbatim in CLAUDE.md)

> This is a hard contract, subordinate to and never weaker than the repo SAFETY section governing
> `ReelScraper/platforms/instagram/scrape.py`. If any config value, flag, refactor, or convenience would relax
> a rule here, the rule wins and the action is forbidden. AutoSearch is a read-only, SOURCE-side discovery
> agent; it never weakens ReelScraper's guest-only guarantee and never writes into ReelScraper's directory.
> When in doubt: do less, slower, or nothing.

**0. Precedence.** These rules bind all code paths, CLI flags, config values, and any future surface (hashtag,
audio, …). No knob, env var, or flag may disable the circuit breaker, pacing floors, daily/per-surface caps,
run-duration cap, kill-switch, or the forbidden-action list. Values may only be set MORE conservative, never
less.

**1. Account rules — burner ONLY, opt-in, guest-first.**
- **Burner account ONLY. NEVER a personal/real account** — no exception, override, or "just this once."
- **Guest by default.** With no operator-supplied burner session, run GUEST-ONLY surfaces and print at startup,
  verbatim: *"No burner session supplied — running GUEST-ONLY. Login-gated surfaces (topsearch,
  discover/chaining) are SKIPPED. Discovery will be shallower."* Absence of a session is never an error and
  never a prompt to obtain one.
- **`guest_only=true` forces guest mode** even if a session is present.
- **Session channels (the ONLY permitted):** gitignored `.env` → `IG_SESSIONID` (+ optional `IG_CSRFTOKEN`),
  or a gitignored `session.txt` (ReelScraper `load_session` format; must contain `sessionid`).
- **Never committed, never logged, never echoed.** Redact the cookie everywhere; log only
  `session: present (burner)` or `session: absent (guest-only)`. Every guest cookie jar MUST assert
  `"sessionid" not in jar`.

**2. Read-only allowlist + FORBIDDEN list.**
- ALLOWED (the entire surface): guest cookie bootstrap `GET https://www.instagram.com/`; guest hydration
  `GET /api/v1/users/web_profile_info/`; guest profile-HTML fallback; reel-sample `POST /api/v1/clips/user/`
  (read-only list); burner-only opt-in `GET /web/search/topsearch/` and
  `GET /api/v1/discover/chaining/?target_id=`. Headers restricted to ReelScraper's shape (Chrome UA,
  `X-IG-App-ID: 936619743392459`, csrf/cookie, optional Referer).
- **FORBIDDEN (no flag re-enables):** ANY engagement/write action (likes, follows, comments, DMs, posts, story
  views/reactions, saves, "seen" receipts); ANY login automation (credential submit, session refresh, OAuth,
  2FA, checkpoint/challenge); CAPTCHA solving (any); headless/full-browser impersonation
  (Selenium/Playwright/Puppeteer); rotating proxies / residential-IP / UA farms / evasion tooling; writing into
  ReelScraper's directory or editing `pages.txt` directly (the hub appends approved handles).

**3. Pacing (STRICTLY slower than the scraper).** All jittered `random.uniform(*range)`; these are FLOORS,
`pacing_seconds` may raise not lower:
- `SEARCH_DELAY=(6.0,12.0)`, `EXPAND_DELAY=(10.0,20.0)`, `HYDRATE_DELAY=(4.0,8.0)`, `SURFACE_DELAY=(15.0,30.0)`.
- Guest session force-refresh every ≤25 requests; reactively on 401/403.
- Caps (config may only lower): `per_term_limit` (default 5) queries/term; ≤20 topsearch + ≤20 expand/run;
  ≤150 hydrations/run; `daily_search_cap` (default 300 IG requests/day, persisted counter — stop when hit).
- **Run-duration cap:** any single run terminates cleanly at 30 min wall-clock, saving partial progress.

**4. Circuit breaker + resume.** `RateLimited` trips after 3 consecutive HTTP 429s; on trip log
`CIRCUIT BREAKER`, save progress, STOP cleanly. Backoff `15*(attempt+1)+random.uniform(0,5)`, ≤4 attempts,
refresh guest session per retry. 401/403 = stale session (refresh + retry once), not a breaker trip; on a
login-gated 401/403 the burner is limited → drop to guest-only for the rest of the run. Resume cache
(`<name>_raw.json` keyed by handle; skip existing; re-save after each unit); a resumed run respects the same
daily counter — resume never resets the budget.

**5. Ban-risk (print at startup, verbatim intent):** GUEST mode is effectively ZERO account-risk but
shallower; BURNER mode CAN get the burner rate-limited/blocked/PERMANENTLY BANNED — expected and acceptable
ONLY because it's disposable; NEVER supply a personal/valued account; no pacing eliminates ban risk on
login-gated surfaces.

**6. Kill-switch.** Fetch the hub config flag `discovery_enabled` (`GET /api/config/agent/auto-search`) at run
start AND between every surface/term transition AND at the top of every heartbeat. If it disables discovery, or
the hub is unreachable/ambiguous (**fail closed**), stop immediately and cleanly, saving progress.

**7. Data hygiene.** Store PUBLIC metadata ONLY (handle, numeric id, follower/following/post counts, category,
verified/business/private booleans, public bio, public external_url, median plays/cadence, derived scores).
NEVER store/POST/log/cache private data (email, phone, private contents, viewer/session ids, the cookie) —
drop such fields if returned. **Purge on reject:** rejected candidates leave only a minimal audit stub (handle
+ status + ts), no scraped metadata.

---

## 2. Cadence — WEEKLY budget → RANDOM daily → HEARTBEAT execution (anti-bot, REQUIRED)

Discovery is **never** run as a burst. A weekly budget is scattered into randomized daily allotments (with rest
days), and each day's allotment is executed as a thin trickle across **heartbeat** ticks during organic hours —
so Instagram sees sporadic, low-volume, human-hours activity, not a scripted cadence. This is a safety
mechanism, governed by §1 like everything else.

### 2a. The weekly plan (`memory/plan.json`)
Regenerate when the ISO week rolls over (or config changes). **Deterministic from a per-week seed** so a
crashed/restarted process resumes the SAME plan, never a fresh random one:
```json
{ "week_start": "2026-07-13", "weekly_budget": 120, "seed": 917342,
  "days": {
    "2026-07-13": {"target": 0,  "windows": []},                 // rest day
    "2026-07-14": {"target": 22, "windows": [[10,12],[19,21]]},  // active: 2 organic windows
    "2026-07-15": {"target": 31, "windows": [[14,17]]}, ...
  } }
```
Distribution: pick `active_days_per_week` random days active; split `weekly_budget` across them with a random
weighting where **no single day exceeds ~35%** of the week (no spike); assign each active day 1–2 random hour
windows inside `active_hours`; rest days get `target:0`. A "work unit" = one search term executed (or one
expand). `daily_search_cap` remains a hard ceiling on top of the plan.

### 2b. The heartbeat — `uv run cli.py beat <platform>`
Bounded, idempotent, safe to call every `heartbeat_minutes`. Each beat:
1. **Kill-switch (fail-closed):** `GET /api/config/agent/auto-search`; if `discovery_enabled` false or hub
   unreachable → log `beat.skip reason=disabled`, exit 0.
2. **Plan:** load/regenerate `plan.json` for the current ISO week.
3. **Gate the beat** — a **no-op tick** (log `beat.skip` + reason, exit 0) unless ALL hold: today not a rest
   day (`target>0`); now inside one of today's `windows`; today's ledger `done < min(target, daily_search_cap)`;
   the breaker cooldown (if any) elapsed; `random() < beat_action_probability`. **Most beats no-op — that is the
   point** (organic scatter).
4. **Act (bounded):** do ≤ `beat_max_units` work units. Per unit: pick/reuse a search term, run one guest-first
   (or burner opt-in) surface call, hydrate+score a few candidates, `POST /api/discovery/{p}` the qualifying
   ones, emit `item.*` board events (§4). Respect ALL §1 pacing floors + per-run caps inside the beat.
5. **Ledger:** increment `memory/caps/<date>.json` `done`; persist the resume cache; record a breaker cooldown
   if it tripped.
6. Exit. A beat is short (seconds–≤2 min) so the request rate stays a trickle.

### 2c. Delivery (how beats fire)
- **Hub heartbeat scheduler (default, local-first, opt-in):** the hub runs a background daemon thread that,
  every `heartbeat_minutes` ± jitter, if `discovery_enabled`, shells out the `auto-search-beat` stage
  (`STAGE_CMD["auto-search-beat"] = ["uv","run","cli.py","beat",p]`). It is **off by default**; the operator
  turns discovery on (kill-switch flag) to start it. See `../PIPELINE.md §11` for the hub side.
- **Alternatives (documented):** OS `cron` or the `schedule` cloud routine pinging `cli.py beat` every
  `heartbeat_minutes`; or `cli.py run <platform>` for a one-off manual exhaustive pass (bypasses the plan; still
  respects caps/pacing/breaker/kill-switch).

**Why it evades detection:** weekly budget → random days (incl. rest days) → random intra-day windows →
probabilistic per-beat action → jittered pacing floors → 25-request session refresh → daily hard cap. The
observable pattern is sporadic, low-volume, human-hours browsing — no scripted rhythm.

---

## 3. Directory layout (mirror AnalysisEngine)
```
AutoSearch/
  CLAUDE.md                 # identity + SAFETY (§1 verbatim) + cadence (§2) + run/beat contract + memory model
  cli.py                    # `run <platform>` | `beat <platform>` | `synthetic <platform>` | `smoke` | `status`
  pyproject.toml            # name="auto-search"; [project.scripts] auto-search="cli:main"; deps: anthropic, jsonschema
  .env.example              # ANTHROPIC_API_KEY=  IG_SESSIONID=(optional burner)  BACKEND_API=http://127.0.0.1:8787
  .gitignore                # .env, session.txt, *_raw.json, logs/, __pycache__/, memory/caps/, memory/plan.json
  engine/
    __init__.py             # __version__, AGENT_NAME="auto-search"
    hub.py                  # HubClient(urllib): register_producer, post_log, post_insight, get_agent_config,
                            #   secrets_status, get_factors/brief, post_candidate, list_candidates, set_candidate_status
    ig.py                   # guest bootstrap (+assert no sessionid), burner session.txt loader, _http header shape,
                            #   web_profile_info hydration, clips/user reel sampling, HTML regex fallback, RateLimited
    search.py               # topsearch (burner), discover/chaining (burner), term→surface orchestration, caps, resume
    plan.py                 # weekly-plan generation/reload (§2a), daily ledger, beat-gating logic (§2b)
    score.py                # heuristic signals (followers, median_plays, cadence) + threshold gates
    claude.py               # anthropic.Anthropic; expand_terms() + score_candidates() (messages.create + output_config json_schema)
    memory.py               # markdown memory: system_prompt.base, <platform>/notes, trending.md
    schema.py               # jsonschema validators for the 2 Claude outputs + the candidate payload
    circuit.py              # CircuitBreaker(max_strikes=3, pace_seconds) + CircuitTripped  (verbatim ae pattern)
    logsetup.py             # per-run JSONL + console; getLogger("as.<mod>")
  memory/                   # system_prompt.base.md, <platform>/notes.md, plan.json (gitignored), caps/<date>.json, trending.md
  tests/                    # test_plan.py, test_schema.py, smoke_hub.py (see §7)
```

---

## 4. Run loop ↔ workflow_stages + lifecycle events
`workflow_stages` = `["Queued","Searching","Scoring","Proposed","Approved","Rejected"]` (registered in the
manifest). `Proposed/Approved/Rejected/Queued` match the Dashboard's `stageTone` literals exactly;
`Searching/Scoring` render as in-flight. Every `POST /api/logs` carries `agent="auto-search"`,
`platform=<p>`, `run_id`, and per-item `content_id=<candidate_id>`:
1. **run.start** (after idempotent `register_producer` so config/secrets/board resolve).
2. Pull niche + keywords from `GET /api/config/agent/auto-search` + `GET /api/corpus/{p}/factors` + insights.
3. Term expansion (Anthropic §5) — no per-item events.
4. Per raw candidate: `item.start data.stage=Queued` → `item.stage Searching` → `item.stage Scoring`.
5. Candidate posted (`POST /api/discovery/{p}`) → `item.done data={stage:"Proposed", score:<relevance>}`.
6. Human approves/rejects → hub `gate.jsonl` → board gate-join moves the item to Approved/Rejected (AutoSearch
   posts nothing).
7. Per-candidate failure → `item.error` (reducer → Failed).
8. **run.end**, preceded by one `POST /api/insights` (`kind="finding"`, tags `["trending-terms","auto-search"]`).

In **beat** mode the same events fire, just a few items per beat — the board shows a live trickle.

---

## 5. Anthropic usage (`engine/claude.py`)
`anthropic.Anthropic()` (zero-arg; `ANTHROPIC_API_KEY` from env). Model from config, default
`claude-opus-4-8`. Both call points use `client.messages.create(..., output_config={"format":{"type":
"json_schema","schema":{…}}})` — **omit `thinking`** (cheapest/lowest-latency for bounded extraction).
- **Term expansion** (1 call/run): niche + seed keywords + factors + prior trending insight → schema
  `{keywords[], hashtags[], audio_terms[]}` (all `additionalProperties:false`, `required`).
- **Relevance scoring** (batched, ~10 candidates/call): niche + compact candidate list → schema
  `{scores:[{handle, score, reasons[]}]}`. Combine with heuristic signals into `relevance={score,reasons}`.
- Errors: SDK auto-retries 429/5xx; wrap in `CircuitBreaker` (3 strikes → clean partial-exit); guard
  `stop_reason` (`refusal`→skip, `max_tokens`→retry smaller); `json.loads` the first text block.
  Cache the stable system/niche block (`cache_control:{type:"ephemeral"}`) across scoring batches.

---

## 6. Config schema + secrets (registry manifest)
```jsonc
{ "name":"auto-search", "kind":"discovery", "consumes":["config","corpus","insights"],
  "produces":"creator_candidates", "human_gate":true, "needs_reference":false, "output_status":"pending",
  "workflow_stages":["Queued","Searching","Scoring","Proposed","Approved","Rejected"],
  "config_schema": { "type":"object","additionalProperties":false, "properties": {
    "weekly_search_budget":   {"type":"integer","default":120,"minimum":1,"description":"Work units (search terms+expands) per 7-day window"},
    "active_days_per_week":   {"type":"integer","default":5,"minimum":1,"maximum":7,"description":"Active days/week; the rest are randomized rest days"},
    "active_hours":           {"type":"array","default":[9,23],"description":"[startHour,endHour] local window beats may act in"},
    "heartbeat_minutes":      {"type":"integer","default":20,"minimum":1,"description":"Scheduler tick cadence"},
    "beat_action_probability":{"type":"number","default":0.35,"minimum":0,"maximum":1,"description":"Chance an in-window beat does work"},
    "beat_max_units":         {"type":"integer","default":2,"minimum":1,"description":"Max work units per beat"},
    "daily_search_cap":       {"type":"integer","default":300,"minimum":1,"description":"Hard ceiling: IG requests/day"},
    "per_term_limit":         {"type":"integer","default":5,"minimum":1,"description":"Candidates hydrated+scored per term"},
    "min_followers":          {"type":"integer","default":2000,"minimum":0},
    "min_median_plays":       {"type":"integer","default":3000,"minimum":0},
    "relevance_threshold":    {"type":"number","default":0.6,"minimum":0,"maximum":1},
    "pacing_seconds":         {"type":"number","default":6.0,"minimum":0,"description":"Min gap between paced actions (floors in §1 win)"},
    "guest_only":             {"type":"boolean","default":true,"description":"true = never use the burner; guest surfaces only"},
    "discovery_enabled":      {"type":"boolean","default":false,"description":"Kill-switch. false = agent + hub scheduler idle"},
    "model":                  {"type":"string","default":"claude-opus-4-8"}
  } },
  "secrets": [ {"name":"anthropic_api_key","env_var":"ANTHROPIC_API_KEY","required":true},
               {"name":"ig_sessionid","env_var":"IG_SESSIONID","required":false} ] }
```
Secrets declared by NAME only (never values). `discovery_enabled` defaults **false** — discovery (and the hub
scheduler) stay idle until the operator turns it on.

---

## 7. Verification (works WITHOUT a live IG session or Anthropic key)
1. **`uv run cli.py status`** — hub health + secret status (ANTHROPIC absent/present, IG absent), prints the
   guest-only banner when no session.
2. **Plan unit test (`tests/test_plan.py`)** — a fixed seed yields a plan whose day-targets sum ≈
   `weekly_search_budget`, has exactly `active_days_per_week` active days, no day > ~35% of the week, rest days
   `target:0`, windows inside `active_hours`; the beat-gate no-ops on rest days / out-of-window / over-cap /
   probability, and acts otherwise (with a stubbed clock + RNG).
3. **`uv run cli.py synthetic <platform>`** — fabricate N candidates (`discovered_via="synthetic"`, precomputed
   relevance, no network/Anthropic), drive the full event + POST path; assert each lands in `candidates.json`
   `status=pending` and the Agent Desk board shows items in the Proposed lane.
4. **`uv run cli.py smoke`** — guest bootstrap only (assert no `sessionid`) + one `web_profile_info` hydration
   of a known public handle; in CI an injected fake transport returns canned JSON.
5. **Hub roundtrip (`tests/`)** — POST a candidate; GET `/pending` shows it `in_pages=false`; approve →
   `appended_to_pages=true` + handle in `pages.txt` (comments/order preserved) + `gate.jsonl` record; second
   approve → `appended=false` (idempotent); reject → no `pages.txt` mutation + purge; 404/400 edge cases.
6. **claude.py unit test** — mocked client asserts `messages.create(model=claude-opus-4-8, output_config.format.
   type="json_schema", no thinking)` and that 429/5xx drive `CircuitBreaker` (3 strikes → CircuitTripped).

**Safety invariants asserted:** with `guest_only=true`/no session, topsearch/chaining are skipped with a log
and the run still completes via guest hydration; AutoSearch never writes into `ReelScraper/` (only via hub
HTTP); pacing constants are strictly larger than ReelScraper's; the breaker + daily/per-term caps + run-duration
cap + kill-switch are enforced; the guest-only rule of the scraper is never touched.
