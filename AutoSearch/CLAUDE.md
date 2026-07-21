# AutoSearch — agent identity + rules (for Claude Code)

**What this is.** AutoSearch (`auto-search`, kind `discovery`) is the pipeline's new front door —
a SOURCE-side agent that searches Instagram (keyword search, related-creator chaining, guest
profile hydration) to find new creators worth scraping, scores them for niche-fit (heuristics +
an optional LLM relevance judgment), and posts them as **candidates** to the hub. A human approves
candidates in the Dashboard → the hub appends the handle to `pages.txt` → the next scrape
ingests them. It closes the loop that is manual today (hand-curated `pages.txt`).

Pipeline shape (7 stages): `Discover → Sources → Scrape → Analyze → Media → Blueprint → Studio`.
AutoSearch owns the first arrow: candidates → human gate → `pages.txt`.

There is exactly ONE `CLAUDE.md` — this one. The full build spec is `PIPELINE.md` in this
directory; read it before making structural changes. This file is the day-to-day operating
contract.

## Prime directive (non-negotiable)
Read work and write results **only through the hub API** (`BACKEND_API`, default
`http://127.0.0.1:8787`). Never import ReelScraper or any sibling's code. Never write into
another project's directory — the hub (not AutoSearch) appends approved handles to
`pages.txt`. AutoSearch is, alongside ReelScraper, the **only** agent permitted to touch
Instagram — and only read-only, guest-first, burner-opt-in (see §SAFETY below). Producers
never scrape. On startup verify the hub is up (`GET /api/platforms`); if it is down, stop
and tell the operator to start it in ReelScraper (`uv run cli.py start`).

## Run commands
```
uv sync                              # create .venv + install deps (jsonschema only)
uv run cli.py status                 # hub health + secret status + guest-only banner + config
uv run cli.py run instagram          # manual/exhaustive discovery pass (still respects
                                      #   caps/pacing/breaker/kill-switch; bypasses the weekly plan)
uv run cli.py beat instagram         # one bounded, idempotent heartbeat tick (§2 below)
uv run cli.py synthetic instagram    # fabricate N candidates, no network, no LLM — verification
uv run cli.py smoke                  # guest bootstrap (assert no sessionid) + one hydration
```

## To fully validate a LIVE run the operator must
1. OPTIONALLY `export GEMINI_API_KEY=...` **and** set `term_expansion_enabled: true` on the
   agent's config desk. Both are needed — the flag is the spend switch and is checked first,
   so a key exported for AnalysisEngine never silently bills discovery. Without them
   discovery runs on the seed keywords alone, which is the supported default, and
2. optionally supply a **burner** IG session (`IG_SESSIONID` env, or gitignored `session.txt`)
   to unlock login-gated surfaces — absence is normal, not an error (guest-only is the default
   and is expected to be shallower), and
3. flip the kill-switch: `discovery_enabled: true` in this agent's hub config
   (`PUT /api/config/agent/auto-search`, or the Dashboard's config form).
Without these, `status`/`synthetic`/`smoke`/the weekly-plan and Gemini unit tests all still
work — that is exactly what §7 verification below proves.

---

## §SAFETY SPECIFICATION — NON-NEGOTIABLE (embedded verbatim from `PIPELINE.md §1`)

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

## §2 Cadence — WEEKLY budget → RANDOM daily → HEARTBEAT execution (anti-bot)

Discovery is **never** run as a burst. A weekly budget (`memory/plan.json`, deterministic from a
per-week seed — a crashed/restarted process resumes the SAME plan) is scattered into randomized
daily allotments (with rest days: `target:0`), and each day's allotment executes as a thin trickle
across **heartbeat** ticks during organic hours. No single day exceeds ~35% of the weekly budget;
each active day gets 1-2 random hour windows inside `active_hours`. `daily_search_cap` remains a
hard ceiling on top of the plan.

**The heartbeat (`uv run cli.py beat <platform>`)** is bounded, idempotent, safe to call every
`heartbeat_minutes`. Each beat: (1) kill-switch check, fail-closed; (2) load/regenerate the weekly
plan; (3) **gate** — a no-op tick (log `beat.skip` + reason) unless ALL hold: today not a rest day,
now inside a window, today's ledger `done < min(target, daily_search_cap)`, breaker cooldown
elapsed, `random() < beat_action_probability` — **most beats no-op, that is the point**; (4) if
gated open, do ≤ `beat_max_units` work units (pick a term, one guest-first surface call,
hydrate+score, `POST /api/discovery/{p}` the qualifiers, emit `item.*` board events); (5) increment
the ledger + persist the resume cache + record any breaker cooldown; (6) exit — a beat is
seconds-to-≤2min, keeping the request rate a trickle.

**Delivery:** the hub runs an opt-in background scheduler thread (off by default —
`discovery_enabled` is the switch) that fires `auto-search-beat` every `heartbeat_minutes` ± jitter.
Alternatives: OS `cron` / a `schedule` cloud routine pinging `cli.py beat`; or a manual
`cli.py run <platform>` one-off pass (bypasses the plan, still respects every cap/pacing/breaker/
kill-switch rule above).

**Why it evades detection:** weekly budget → random days (incl. rest days) → random intra-day
windows → probabilistic per-beat action → jittered pacing floors → 25-request session refresh →
daily hard cap. The observable pattern is sporadic, low-volume, human-hours browsing.

---

## The run loop ↔ `workflow_stages` + lifecycle events

`workflow_stages = ["Queued","Searching","Scoring","Proposed","Approved","Rejected"]` (registered
in the manifest). Every `POST /api/logs` carries `agent="auto-search"`, `platform=<p>`, `run_id`,
and per-item `content_id=<candidate_id>` — computed client-side via
`engine.schema.candidate_id(platform, handle)`, the SAME stable hash the hub derives, so the
board's discovery gate-join (keyed on `content_id == candidate_id`) lines up:

1. **run.start** (after idempotent `register_producer` so config/secrets/board resolve).
2. Pull niche + keywords (`GET /api/config/{p}` — the platform's `niche_config.json`) +
   `GET /api/corpus/{p}/factors` + the prior `trending-terms` shared insight.
3. Term expansion (Gemini, §5 of PIPELINE.md) — OFF by default; no per-item events. Runs
   only when `term_expansion_enabled` is true AND a Gemini key resolves, and falls back to
   the seed keywords verbatim in every other case (flag off, no key, bad JSON, API error).
4. Per raw candidate: `item.start data.stage=Queued` → `item.stage Searching` → `item.stage Scoring`.
5. Candidate posted (`POST /api/discovery/{p}`) → `item.done data={stage:"Proposed", score:<relevance>}`.
6. Human approves/rejects → hub `gate.jsonl` → board gate-join moves the item to
   Approved/Rejected (AutoSearch posts nothing further).
7. Per-candidate failure → `item.error` (reducer → Failed).
8. **run.end**, preceded by one `POST /api/insights` (`kind="finding"`, tags
   `["trending-terms","auto-search"]`) when at least one candidate was proposed.

In **beat** mode the same events fire, just a few items per beat — the board shows a live trickle.

## `pages.txt` handle form — the one gotcha
The hub stores handles as **full URLs** (`https://www.instagram.com/<handle>`) in `pages.txt` and
matches/dedupes/appends against that exact string. Every candidate POST sets `handle` to that full
URL form (`engine.schema.to_pages_handle(username)`) — never the bare username — so approval's
`pages.txt` append/dedupe matches existing lines.

## Layout
```
cli.py                  run | beat | synthetic | smoke | status
engine/hub.py           typed hub client (built against /openapi.json)
engine/ig.py            guest bootstrap (+assert no sessionid), burner session.txt loader,
                        _http header shape, web_profile_info hydration, clips/user reel
                        sampling, HTML regex fallback, RateLimited 3-strike breaker
engine/search.py        topsearch (burner), discover/chaining (burner), term->surface
                        orchestration, caps, resume (<platform>_raw.json)
engine/plan.py          weekly-plan generation/reload (§2), daily ledger, beat-gating logic
engine/score.py         heuristic signals (followers, median_plays) + threshold gates
engine/gemini.py        Gemini REST over stdlib urllib (no SDK); expand_terms() +
                        score_candidates() (generateContent + responseSchema JSON mode).
                        Optional: gated behind term_expansion_enabled.
engine/memory.py        markdown memory: system_prompt.base, <platform>/notes, trending.md
engine/schema.py        jsonschema validators for the 2 LLM outputs + the candidate payload
engine/circuit.py       CircuitBreaker(max_strikes=3) + CircuitTripped (verbatim ae/scraper pattern)
engine/logsetup.py      per-run pretty console + JSONL
memory/                 system_prompt.base.md, <platform>/notes.md, plan.json (gitignored),
                        caps/<date>.json (gitignored), trending.md
tests/                  test_plan.py, test_schema.py, test_gemini.py,
                        test_expansion_gate.py, smoke_hub.py
```

## Platform-wide conventions (PIPELINE.md §10)
- **Logging (§10.1):** shared `engine/logsetup` per-run `logs/<start>_<cmd>.log` (pretty + JSONL);
  `POST /api/logs` for LIFECYCLE events only, stamped with `run_id`.
- **Config (§10.3):** `GET /api/config/agent/auto-search` at run start over defaults; knobs
  declared in the manifest `config_schema` (Dashboard-editable), including all cadence knobs and
  the `discovery_enabled` kill-switch (default **false**).
- **Secrets (§10.4):** referenced by env-var NAME only (`GEMINI_API_KEY` — declared
  **optional**, since discovery runs keyword-only by default — and `IG_SESSIONID`); the
  hub sees status, never values. `.env.example` documents the names; `.env`/`session.txt` are
  gitignored.
