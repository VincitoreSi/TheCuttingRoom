#!/usr/bin/env python3
"""
api/app.py — the local API hub. ONE backend the whole pipeline runs on.

Wraps core/ (config · corpus · content · studio · insights) + pipeline control + media,
and serves the built React frontend. Everything is localhost, no cloud. Auto-docs at /docs.

The frontend (a separate agent builds it) consumes the REST + SSE here; Claude Code agents
can use the same surface. Run via `python -m uvicorn api.app:app` or the `cli.py start`.
"""
import json, subprocess, threading, time, asyncio, hashlib, os, random, urllib.request
import base64, re, shutil, ipaddress, socket, urllib.parse
from pathlib import Path

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (JSONResponse, StreamingResponse, HTMLResponse,
                               RedirectResponse, FileResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys, logging
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.corpus import Corpus
from core.logsetup import setup_logging
from core.memory import SharedInsights
from core.audio import collect_sounds

PLATFORMS = ["instagram", "x", "youtube"]
STUDIO_STATUSES = {"draft", "proposed", "approved", "rejected"}
CANDIDATE_STATUSES = {"pending", "approved", "rejected"}


# ---------------- request models (make /docs self-documenting) ----------------
class ConfigUpdate(BaseModel):
    config: Optional[dict] = None                 # niche_config.json contents
    pages: Optional[list[str]] = None             # pages.txt lines


class ScheduleIn(BaseModel):
    """Automatic run settings for one platform. Every field optional so the UI can toggle
    one thing without resending the rest."""
    enabled: Optional[bool] = None
    every_hours: Optional[float] = None
    # analysis-engine spends API credits per clip; unattended, that adds up. Off unless
    # the operator explicitly asks for it.
    include_blueprints: Optional[bool] = None


class Proposal(BaseModel):
    """A studio item written by a producer agent.

    Backward-compatible: {text, filename} still works. New human-gate fields are
    optional — a producer stamps agent/kind and (by default) status="proposed"."""
    text: str                                     # the proposal markdown
    filename: Optional[str] = None                # optional name (.md appended)
    agent: Optional[str] = None                   # producing agent, e.g. "similar-content"
    kind: Optional[str] = None                    # clone | proposal | idea | template
    status: Optional[str] = None                  # draft | proposed | approved | rejected (default proposed)


class StatusUpdate(BaseModel):
    """A human-gate decision recorded against a studio item."""
    status: str                                   # draft | proposed | approved | rejected
    note: Optional[str] = None


class RenderAssetIn(BaseModel):
    """One binary artifact of a render, base64-encoded.

    Base64-in-JSON rather than multipart on purpose: `python-multipart` is not a
    dependency of this hub (a File/Form route would fail at import), and every producer
    agent speaks hand-rolled stdlib urllib — this keeps both sides dependency-free."""
    name: str                                     # "reel.mp4" | "poster.jpg" | "frame-00.png"
    content_b64: str
    content_type: Optional[str] = None


class RenderIn(BaseModel):
    """A rendered artifact uploaded by a producer (POST /api/renders/{platform}).

    Deliberately producer- and technique-agnostic: nothing here assumes a slideshow.
    A future video-generation agent (Veo/Flow) posts kind="video", has_audio=true and
    an empty frames[] against this same model."""
    file: str                                     # the studio .md filename — THE join key
    agent: str
    kind: str = "slideshow"                       # slideshow | video
    content_id: Optional[str] = None
    slug: Optional[str] = None
    caption: Optional[str] = None
    caption_model: Optional[str] = None
    hashtags: list[str] = []
    duration_s: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    aspect_ratio: Optional[str] = None            # "9:16" (reels) | "4:5" | "1:1"
    video_fit: Optional[str] = None               # cover (crop to fill) | contain (letterbox)
    has_audio: bool = False
    provider: Optional[str] = None
    seed: Optional[int] = None
    frames: list[dict] = []                       # [{frame, kb, provider, on_screen_text, duration_s}]
    run_id: Optional[str] = None
    evaluation: Optional[dict] = None
    assets: list[RenderAssetIn] = []
    model_config = {"extra": "allow"}


class RenderRequest(BaseModel):
    """Body of POST /api/studio/{p}/{file}/render — the per-item render trigger."""
    force: bool = False


class SecretDecl(BaseModel):
    """A secret declared BY NAME only — the hub never stores the value (§10.4)."""
    name: str
    env_var: str
    required: bool = True
    present: Optional[bool] = None                # agent self-reports resolvability


class ProducerManifest(BaseModel):
    """A producer's self-registration manifest (§3 Producer SPI)."""
    name: str                                     # idempotent upsert key
    kind: Optional[str] = None                    # clone | proposal | idea | template
    consumes: list[str] = []                      # corpus|analysis|audio|insights|reference_blueprint
    human_gate: bool = False
    needs_reference: bool = False
    produces: Optional[str] = None                # e.g. "studio_markdown"
    output_status: Optional[str] = None           # proposed | draft
    config_schema: Optional[dict] = None          # JSON Schema of tunable knobs + defaults (§10.3)
    secrets: list[SecretDecl] = []                # declared by NAME only (§10.4)
    workflow_stages: list[str] = []               # ordered lane labels for the agent board
    model_config = {"extra": "allow"}


class UnsafeFetchURL(ValueError):
    """A reference URL that must never be fetched."""


def assert_fetchable_url(url) -> str:
    """Reject anything that is not a public http(s) URL, BEFORE it reaches a fetcher.

    `urllib.request.urlopen` honours `file://` and `ftp://`, and the fetched bytes land in
    `media/<platform>/ref_<sha1>.mp4` which is served back over the `/media` static mount.
    Unchecked, `{"url": "file:///…/.ssh/id_rsa"}` is a read-anything primitive and
    `http://169.254.169.254/…` reaches the cloud metadata service. The hub binds loopback
    only, but its threat model already assumes an open browser tab is inside the perimeter
    (see the CORS note above and _validate_render_cmd), so neither is acceptable.

    Hostnames are resolved and every returned address checked: a public name that resolves
    to 127.0.0.1 or 169.254.169.254 is the standard DNS-rebinding way around a name-only
    blocklist. This narrows the window rather than closing it — the fetcher resolves again
    and could in principle get a different answer — but it stops the whole practical class.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeFetchURL("url is required")
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeFetchURL(f"only http(s) URLs may be fetched, not {parsed.scheme or 'a relative URL'!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeFetchURL("url has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeFetchURL(f"could not resolve {host!r}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UnsafeFetchURL(f"{host!r} resolves to the non-public address {ip}")
    return url.strip()


class ReferenceIn(BaseModel):
    """Register an ad-hoc reference/template video (only consumer: the template agent)."""
    url: str
    note: Optional[str] = None


class CandidateIn(BaseModel):
    """A creator candidate posted by the auto-search discovery agent (PIPELINE.md §11.2).

    Upserted by candidate_id (stable hash of platform+handle if not supplied). Status is
    hub-managed (never trusted from the agent): forced to 'pending' on first insert and
    never silently un-gated on re-ingest."""
    candidate_id: Optional[str] = None
    handle: str
    platform: Optional[str] = None
    source_term: Optional[str] = None
    discovered_via: Optional[str] = None
    followers: Optional[int] = None
    median_plays: Optional[float] = None
    sample_reels: list[str] = []
    relevance: Optional[dict] = None            # {score, reasons[]}
    ts: Optional[float] = None
    model_config = {"extra": "allow"}


class LogIn(BaseModel):
    """A curated LIFECYCLE event an agent POSTs to the central log (§10.1)."""
    agent: str
    level: str = "info"                           # debug|info|warning|error
    event: str = ""                               # run_start|item_done|run_end|error|eval ...
    msg: Optional[str] = None
    run_id: Optional[str] = None                  # links back to the agent's local log file
    platform: Optional[str] = None
    content_id: Optional[str] = None
    ts: Optional[float] = None
    data: Optional[dict] = None
    model_config = {"extra": "allow"}


class EvalIn(BaseModel):
    """A self-eval / judge result posted to the eval store (§10.2)."""
    agent: str
    target_type: str                              # blueprint|clone|proposal|idea|audio ...
    target_id: str
    scores: Optional[dict] = None                 # {overall, per_criterion:{...}}
    verdict: Optional[str] = None
    judge: Optional[str] = None                   # judge model id
    notes: Optional[str] = None
    platform: Optional[str] = None
    ts: Optional[float] = None
    model_config = {"extra": "allow"}


class AgentConfigIn(BaseModel):
    """Per-agent config written from the Dashboard (§10.3). Free-form dict."""
    config: dict = {}


class InsightIn(BaseModel):
    text: str
    platform: str = "shared"
    kind: str = "finding"                         # finding | negative | method | idea
    tags: list[str] = []


class Beat(BaseModel):
    """One segment of the frame-by-frame timeline."""
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    description: Optional[str] = None
    shot_type: Optional[str] = None               # close-up | wide | POV | screen-recording ...
    on_screen_text: Optional[str] = None


class Shot(BaseModel):
    """One shot in a schema-2 blueprint. Extra keys (camera, lighting, ...) preserved."""
    shot_index: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None
    description: Optional[str] = None
    generation_prompt: Optional[str] = None       # self-contained text-to-video prompt
    negative_prompt: Optional[str] = None
    model_config = {"extra": "allow"}


class VideoAnalysisIn(BaseModel):
    """What an analysis agent POSTs per analyzed clip.

    schema_version 1 = the lean VideoAnalysis frame-by-frame doc (hook/beats/...).
    schema_version 2 = AnalysisEngine's rich, generation-ready BLUEPRINT (a superset
    that keeps the lean `virality_formula` the hub `brief` reads). Both validate here;
    `content_id` stays REQUIRED and extra keys are preserved. Old lean docs with NO
    `schema_version` still load (default 1)."""
    content_id: str
    schema_version: int = 1                        # 1 = lean doc, 2 = rich blueprint
    url: Optional[str] = None
    model: Optional[str] = None                   # e.g. "gemini-2.5-flash" / "gemini-2.5-pro"
    analyzed_by: Optional[str] = None             # e.g. "AnalysisEngine"
    duration_s: Optional[float] = None
    is_reference: bool = False                    # True = a reference/template blueprint, not corpus content
    # --- lean (schema_version 1) fields — still accepted ---
    summary: Optional[str] = None
    hook: Optional[dict] = None                   # {type, first_seconds, on_screen_text}
    beats: Optional[list[Beat]] = None            # frame-by-frame timeline
    visual_style: Optional[dict] = None           # {color_palette, lighting, editing_pace, camera, transitions}
    subjects: Optional[list[str]] = None
    setting: Optional[str] = None
    text_overlay: Optional[dict] = None           # {present, density, style, key_phrases}
    pacing: Optional[dict] = None                 # {cuts, avg_shot_len_s}
    retention_devices: Optional[list[str]] = None
    cta: Optional[dict] = None                    # {present, text}
    tags: Optional[list[str]] = None
    replicable_formula: Optional[str] = None      # how to recreate this format
    # --- rich blueprint (schema_version 2) blocks — all optional ---
    video_metadata: Optional[dict] = None
    global_style: Optional[dict] = None
    audio: Optional[dict] = None                  # v1 {music_type,...} OR v2 rich audio block
    audio_strategy: Optional[dict] = None         # {audio_type, beat_markers_s[], reuse_recommendation, ...}
    characters_and_subjects: Optional[list[dict]] = None
    text_overlays: Optional[list[dict]] = None
    shots: Optional[list[Shot]] = None            # each with generation_prompt/negative_prompt
    regeneration_guide: Optional[dict] = None
    virality_formula: Optional[dict] = None       # {hook, retention_devices[], pacing, cta, replicable_formula, tags}
    evaluation: Optional[dict] = None             # {score_0_100, per_criterion, passes, accepted, ...}
    # tolerate extra fields the agent adds; don't warn on the `model` field name
    model_config = {"extra": "allow", "protected_namespaces": ()}


def _interpreter():
    for cand in (ROOT / ".venv" / "bin" / "python", ROOT / "venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


PY = _interpreter()
log = logging.getLogger("api.hub")

app = FastAPI(title="Pipeline API hub", version="1.0")
# Loopback origins only — NOT `allow_origins=["*"]`.
#
# The hub binds 127.0.0.1, which stops network attackers but not the browser you already
# have open: with a wildcard origin, any page you visit could call these routes and read
# the responses. Every route here is unauthenticated by design (single-user local tool), and
# one of them launches a producer subprocess, so a wildcard turns "any website you browse"
# into "anything that can drive your pipeline".
#
# The port is not fixed (cli.py falls back when 8787 is busy) and the Dashboard dev server
# picks its own, so this matches any port on loopback rather than an enumerated list.
# Production needs no CORS at all: the hub serves the built Dashboard same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup():
    setup_logging("hub")   # idempotent — no-op if cli.py already set it up
    log.info("API hub started", extra={"platforms": PLATFORMS, "interpreter": PY})
    try:
        threading.Thread(target=_discovery_heartbeat_loop, daemon=True).start()
        log.info("discovery heartbeat scheduler thread started (idle unless discovery_enabled)")
    except Exception as e:
        # must never block/fail startup — discovery is strictly opt-in
        log.error("failed to start discovery heartbeat scheduler", extra={"err": str(e)})
    try:
        threading.Thread(target=_schedule_loop, daemon=True).start()
        log.info("pipeline schedule thread started (idle unless a platform is enabled)")
    except Exception as e:
        # same rule as discovery: a scheduler must never be able to block startup
        log.error("failed to start pipeline scheduler", extra={"err": str(e)})

# ---------------- helpers ----------------
def pdir(platform):
    if platform not in PLATFORMS:
        raise HTTPException(404, f"unknown platform {platform}")
    return ROOT / "platforms" / platform


AUDIO_FIELDS = ("audio_id", "audio_title", "audio_artist", "audio_is_original",
                "audio_is_reusable", "sound_page_url", "audio_uses_count")


def _content(platform):
    f = pdir(platform) / "content.json"
    if not f.exists():
        return []
    rows = json.loads(f.read_text(encoding="utf-8"))
    media = ROOT / "media" / platform
    adir_p = ROOT / "analysis" / platform
    out = []
    for r in rows:
        cid = r.get("content_id")
        mp4 = media / f"{cid}.mp4"
        jpg = media / f"{cid}.jpg"
        item = {
            "platform": platform, "creator": r.get("creator"),
            "creator_followers": r.get("creator_followers"),
            "content_id": cid, "url": r.get("url"),
            "plays": r.get("plays"), "virality_score": r.get("virality_score"),
            "tier": r.get("tier"), "reach_multiplier": r.get("reach_multiplier"),
            "outlier_score": r.get("outlier_score"), "engagement_rate": r.get("engagement_rate"),
            "velocity": r.get("velocity"), "duration_s": r.get("duration_s"),
            "caption": r.get("caption"), "posted": r.get("posted_iso"),
            "posted_ts": r.get("posted_ts"),
            "video_url": (f"/media/{platform}/{cid}.mp4" if mp4.exists() else r.get("media_url")),
            "thumb_url": (f"/media/{platform}/{cid}.jpg" if jpg.exists() else r.get("thumbnail_url")),
            "video_local": mp4.exists(),
            "analyzed": (adir_p / f"{cid}.json").exists(),
        }
        # audio/sound fields — null-tolerant (older content.json predates them)
        for k in AUDIO_FIELDS:
            item[k] = r.get(k)
        out.append(item)
    return out


# ---------------- persistence helpers (producers / studio-meta / reference / logs / evals / config) ----------------
def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _append_jsonl(path, rec):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


PRODUCERS_FILE = ROOT / "producers" / "registry.json"
LOGS_FILE = ROOT / "logs" / "agents.jsonl"

# in-memory ring buffer of recent central log events, for the SSE `log` channel
_LOG_BUF: list[dict] = []
_LOG_LOCK = threading.Lock()
_LOG_MAX = 2000


def _push_log(rec):
    with _LOG_LOCK:
        _LOG_BUF.append(rec)
        if len(_LOG_BUF) > _LOG_MAX:
            del _LOG_BUF[:-_LOG_MAX]

# ---------------- meta ----------------
def _has_raw_scrape(platform) -> bool:
    """Has a scrape left raw output on disk for this platform?

    `scrape` and the board do not share a file: scrape writes `<content>_raw*.json`, and
    only `analyze` turns that into the `content.json` the board reads. Without this,
    "scraped 250 reels, never analyzed" and "never scraped anything" are the same
    response — so a finished scrape looks like a scrape that found nothing, and every
    empty-state in the Dashboard tells you to run the stage you just ran.

    Matched by convention rather than by name: instagram writes reels_raw*.json, x
    posts_raw*.json, youtube shorts_raw*.json, and a sharded run adds _2, _3. Keying this
    to one platform's filename would report the other two as never scraped.
    """
    return any(pdir(platform).glob("*_raw*.json"))


_PAGES_URL_RE = re.compile(r"^\s*https?://[^/]+/", re.I)
# Path segments that introduce an id rather than being one (youtube.com/channel/UC…).
_PAGES_PATH_PREFIXES = {"channel", "c", "user"}


def _norm_page_handle(line: str) -> str:
    """A pages.txt line reduced to the identity the scraper will actually fetch.

    pages.txt accepts three spellings of the same creator — `handle`, `@handle`, and the
    full profile URL — and every scraper collapses them before fetching (see
    `platforms/instagram/scrape.py::norm_handle`). The hub did not: it compared raw
    strings. AutoSearch posts approved candidates in the URL form and a human types the
    bare handle, so approving a creator already on the watchlist appended them a SECOND
    time. The scrape then deduped and pulled that creator once, while the Board counted
    two pages — a count quietly disagreeing with the run it was describing.

    Deliberately not lowercased. Instagram handles are case-insensitive, but YouTube
    channel ids are not, and folding case here would merge two genuinely different
    channels into one.
    """
    s = _PAGES_URL_RE.sub("", line or "").strip()
    s = s.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in s.split("/") if p]
    if not parts:
        return ""
    head = parts[0].lstrip("@")
    if head in _PAGES_PATH_PREFIXES and len(parts) > 1:
        head = parts[1].lstrip("@")
    return head


def _watchlist(platform):
    """Creators on the watchlist — the non-comment lines of pages.txt, one per creator.

    This is the count the Board's Sources node wants. It used to show `creators`, which
    counts distinct creators in the SCORED corpus, so a freshly added handle read as
    "0 pages" until two more stages had run.

    Deduped the way the scraper dedupes, so the number describes what a scrape will fetch
    rather than how many lines are in the file.
    """
    pf = pdir(platform) / "pages.txt"
    if not pf.exists():
        return []
    out, seen = [], set()
    for line in pf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = _norm_page_handle(line)
        if key and key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


# Counting reels means parsing the raw scrape dump, which runs to tens of MB — far too
# expensive to redo on every /api/platforms poll. The file is rewritten wholesale by a
# scrape and never appended to, so (mtime_ns, size) is a sound cache key: any new scrape
# changes at least one of them.
_RAW_COUNT_CACHE: dict = {}


def _scraped_count(platform) -> int:
    """How many items the last scrape actually pulled, straight from the raw dumps.

    The raw shape is {handle: [item, ...]} for every platform (see each normalize.py).
    Anything unparseable counts as zero rather than raising — a half-written dump during
    a live scrape must not 500 the endpoint the whole Dashboard polls.
    """
    total = 0
    for f in sorted(pdir(platform).glob("*_raw*.json")):
        try:
            st = f.stat()
        except OSError:
            continue
        key = (str(f), st.st_mtime_ns, st.st_size)
        if key not in _RAW_COUNT_CACHE:
            _RAW_COUNT_CACHE.clear()          # bounded: one entry per live raw file
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                _RAW_COUNT_CACHE[key] = sum(
                    len(v) for v in data.values() if isinstance(v, list)
                ) if isinstance(data, dict) else 0
            except (OSError, json.JSONDecodeError, AttributeError):
                _RAW_COUNT_CACHE[key] = 0
        total += _RAW_COUNT_CACHE[key]
    return total


GEMINI_ENV_VARS = ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY")

# Where each agent keeps its own gitignored .env. Sibling dirs spelled out rather than
# reusing ANALYSIS_ENGINE_DIR / AUTO_SEARCH_DIR: those constants are defined ~1200 lines
# below with the stage commands, and depending on the deferred binding would be a trap for
# the next person who moves either one. A renderable producer may also declare `dir` in its
# manifest, which wins — that is how a producer outside this list is found.
AGENT_DIRS = {
    "analysis-engine": "AnalysisEngine",
    "auto-search": "AutoSearch",
    "similar-content": "SimilarContent",
}


def _env_file_declares(path, names) -> bool:
    """Does this .env assign any of `names` a non-empty value? PRESENCE ONLY.

    The value is compared against the empty string and discarded. It is never returned,
    never logged, never bound to a name that outlives the loop — the hub does not store
    secrets, and this function is the only place it reads a file that holds one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() in names and value.strip().strip("'\""):
            return True
    return False


def _agent_env_dir(agent: str, manifest: dict | None = None):
    """The directory holding `agent`'s .env, or None when it cannot be resolved."""
    declared = (manifest or {}).get("dir")
    if declared:
        # Same containment rule the render launcher applies: a direct sibling, no traversal.
        candidate = (ROOT.parent / declared).resolve()
        if candidate.parent == ROOT.parent.resolve() and candidate.is_dir():
            return candidate
    known = AGENT_DIRS.get(agent)
    return (ROOT.parent / known) if known else None


def _secret_present(env_var: str, agent_dir) -> bool | None:
    """Is `env_var` set for an agent living in `agent_dir`? None = cannot tell.

    The hub checks its own environment first, then that agent's gitignored .env, because
    that is where ./init writes the key and the hub does not inherit it.
    """
    if not env_var:
        return None
    if os.environ.get(env_var):
        return True
    if agent_dir is None:
        return None
    return _env_file_declares(agent_dir / ".env", {env_var})


def _gemini_key_present() -> bool:
    """Is a Gemini key reachable by the agents that need one? PRESENCE ONLY."""
    if any(os.environ.get(v) for v in GEMINI_ENV_VARS):
        return True
    return any(_env_file_declares(ROOT.parent / d / ".env", set(GEMINI_ENV_VARS))
               for d in ("AnalysisEngine", "SimilarContent"))


def _media_count(platform) -> int:
    d = ROOT / "media" / platform
    if not d.is_dir():
        return 0
    # ref_*.mp4 are operator-supplied reference clips, not corpus media — the blueprint
    # stage works off the corpus, so they must not make it look ready.
    return sum(1 for f in d.glob("*.mp4") if not f.name.startswith("ref_"))


def stage_readiness(platform):
    """Can each stage do anything useful right now, and if not, what unblocks it?

    Every one of these stages already refuses cleanly with a precise message — "no
    creators given — fill pages.txt", "no scraped data — scrape first", "no content.json
    — run analyze first", "no Gemini key in env". The problem was never the backend: it
    was that the only way to read those messages was to run the stage, let it fail, and
    squint at a truncated subprocess tail inside a board node. This exposes the same
    preconditions BEFORE the click, so the Dashboard can disable a doomed Run and name
    the stage that would fix it.

    `blocked_by` is a stage the user can run to clear the block — the one-click fix. It
    is None when running something else cannot help (a missing API key, an empty
    watchlist), in which case `reason` says what the human has to do instead.
    """
    has_watchlist = bool(_watchlist(platform))
    has_raw = _has_raw_scrape(platform)
    has_corpus = (pdir(platform) / "content.json").exists()
    has_media = _media_count(platform) > 0
    has_key = _gemini_key_present()

    def st(ready, blocked_by=None, reason=""):
        return {"ready": ready, "blocked_by": blocked_by, "reason": reason}

    out = {
        "scrape": st(has_watchlist, None,
                     "" if has_watchlist else
                     "No creators on the watchlist. Add a handle in Config first."),
        "analyze": st(has_raw, "scrape",
                      "" if has_raw else "Nothing scraped yet — run Scrape first."),
        "media": st(has_corpus, "analyze",
                    "" if has_corpus else "No scored corpus yet — run Analyze first."),
    }
    # The blueprint stage needs BOTH persisted clips and a key. Report the missing
    # prerequisite that comes first in the pipeline, so following the chain terminates.
    if not has_media:
        out["analysis-engine"] = st(False, "media",
                                    "No clips persisted yet — run Media first.")
    elif not has_key:
        out["analysis-engine"] = st(
            False, None,
            "No Gemini key. Set GEMINI_API_KEY in AnalysisEngine/.env, then restart the hub.")
    else:
        out["analysis-engine"] = st(True)
    # Discovery is opt-in and never part of the core run; it has no corpus precondition.
    out["auto-search"] = st(True)
    return out


def _source_mtime() -> float:
    """Newest mtime across the hub's own Python sources.

    Python loads a module once and keeps it; a hub launched before a `git pull` goes on
    serving the code it imported at startup, from memory, with no outward sign. Comparing
    this against the value captured at import answers the one question that matters:
    is the running process still the code that is on disk?

    Only the files whose contents shape the HTTP surface are stamped. Platform scrapers are
    re-read on every stage run (they are subprocesses), so a change there is picked up
    without a restart and must not be reported as skew.
    """
    newest = 0.0
    for f in [ROOT / "cli.py", *(ROOT / "api").glob("*.py"), *(ROOT / "core").glob("*.py")]:
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    return newest


# Captured at import — i.e. the state of the tree this process actually loaded.
SOURCE_MTIME_AT_START = _source_mtime()


def _checkout_niche() -> Optional[str]:
    """The niche this checkout works on, from the platforms' niche_config.json.

    Normally identical across platforms (a niche clone is branched wholesale), so the
    common case returns one name. Distinct values are joined rather than picking a winner:
    a clone that is half Fashion and half Fitness is worth seeing, not smoothing over.
    """
    names = []
    for p in PLATFORMS:
        try:
            cfg = json.loads((pdir(p) / "niche_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = (cfg.get("niche") or "").strip()
        if n and n not in names:
            names.append(n)
    return " · ".join(names) or None


@app.get("/api/hub")
def hub_identity():
    """Which checkout this hub is, and whether it is still running the code on disk.

    Exists because of a real half-hour of confusion: a hub started at 14:43 was still
    holding :8787 when the tree was re-cloned at 17:33. `./init` saw something answering
    and reused it, so a freshly built Dashboard talked to a three-hour-old API. The new
    fields it asked for (`watchlist`, `scraped_items`) simply were not in the response, and
    the Board rendered "undefined pages" — a symptom that points nowhere near the cause.

    `stale` is the whole point of the endpoint, but it is not the only signal: a hub old
    enough to predate this route answers 404, which tells a caller the same thing. Callers
    must treat "no /api/hub" as stale rather than as an error.
    """
    now = _source_mtime()
    return {
        "root": str(ROOT),
        # What this checkout is FOR. One clone per niche is the supported way to run two
        # niches at once (scripts/new-niche.sh), and two Dashboards are otherwise identical
        # on screen — so the hub says which one it is and the UI can put it in the chrome.
        "niche": _checkout_niche(),
        # Second of tolerance: a checkout can land its files inside the same second the
        # hub imports them, and a restart loop is worse than a missed skew of one second.
        "stale": now > SOURCE_MTIME_AT_START + 1,
        "source_mtime": SOURCE_MTIME_AT_START,
        "source_mtime_now": now,
    }


@app.get("/api/platforms")
def platforms():
    out = []
    for p in PLATFORMS:
        rows = _content(p)
        out.append({
            "platform": p, "has_data": bool(rows),
            # Raw scrape output on disk. Distinct from has_data, which means a SCORED
            # corpus exists; the gap between the two is exactly the analyze stage.
            # Read off the filesystem so it survives a hub restart — the job ledger,
            # the only other evidence a scrape had run, is in-memory and does not.
            "scraped": _has_raw_scrape(p),
            # Counts the Board needs BEFORE analyze has run, so a node can report its own
            # stage instead of the end of the pipeline: watchlist size the moment a handle
            # is added, reels pulled the moment a scrape finishes.
            "watchlist": len(_watchlist(p)),
            "scraped_items": _scraped_count(p),
            "readiness": stage_readiness(p),
            "items": len(rows), "creators": len({r["creator"] for r in rows}),
            "viral": sum(1 for r in rows if r.get("tier") == "Viral"),
            "media_ready": sum(1 for r in rows if r.get("video_local")),
            "analyzed": sum(1 for r in rows if r.get("analyzed")),
        })
    return out

def _write_pages(platform, handles):
    """Reconcile pages.txt with the watchlist the Dashboard sent, keeping the file's prose.

    GET /api/config strips comments and blank lines before handing the list to the UI, so
    writing `"\\n".join(pages)` straight back — which is what this used to do — permanently
    deleted every comment in the file on the first save. pages.txt ships from
    pages.txt.example as an annotated file explaining what belongs in it; a new user's very
    first "add a handle" silently destroyed that.

    Comment and blank lines keep their positions, handles that survived keep theirs, removed
    handles go, and additions land at the end.
    """
    pf = pdir(platform) / "pages.txt"
    pf.parent.mkdir(parents=True, exist_ok=True)
    wanted = [h.strip() for h in handles if h and h.strip()]
    remaining = list(dict.fromkeys(wanted))          # de-duped, order preserved
    out = []
    prior = pf.read_text(encoding="utf-8").splitlines() if pf.exists() else []
    for line in prior:
        bare = line.strip()
        if not bare or bare.startswith("#"):
            out.append(line)                          # prose and spacing survive verbatim
            continue
        if bare in remaining:
            out.append(line)
            remaining.remove(bare)                    # kept in place, not re-appended
        # else: the handle was removed from the watchlist — drop the line
    out.extend(remaining)                             # whatever is new goes at the end
    pf.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


# ---------------- config (one place) ----------------
@app.get("/api/config/{platform}")
def get_config(platform):
    d = pdir(platform)
    cfg = {}
    cf = d / "niche_config.json"
    if cf.exists():
        cfg = json.loads(cf.read_text(encoding="utf-8"))
    pages = []
    pf = d / "pages.txt"
    if pf.exists():
        pages = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
    return {"config": cfg, "pages": pages}

@app.put("/api/config/{platform}")
def put_config(platform, body: ConfigUpdate):
    d = pdir(platform)
    if body.config is not None:
        (d / "niche_config.json").write_text(json.dumps(body.config, indent=2), encoding="utf-8")
    if body.pages is not None:
        _write_pages(platform, body.pages)
    return {"ok": True}

# ---------------- corpus + content ----------------
@app.get("/api/content/{platform}")
def content(platform):
    return _content(platform)

@app.get("/api/corpus/{platform}/factors")
def factors(platform):
    return Corpus(platform).factors()

@app.get("/api/corpus/{platform}/top")
def top(platform, n: int = 15):
    return Corpus(platform).top_viral(n)

@app.get("/api/corpus/{platform}/brief")
def brief(platform, q: str | None = None):
    return {"brief": Corpus(platform).brief(query=q)}

@app.get("/api/corpus/{platform}/search")
def search(platform, q: str, k: int = 10):
    return Corpus(platform).exemplars(q, k)

# ---------------- studio + insights (with the human gate) ----------------
def _studio_meta_path(platform):
    return ROOT / "studio" / platform / "meta.json"


# Studio filenames come from agents over HTTP and are used to build paths, so they are
# whitelisted rather than merely stripped of "/": a bare `.replace("/", "_")` still lets
# through "..", NUL, and backslash-separated paths on some filesystems.
_STUDIO_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _studio_filename(filename: Optional[str]) -> str:
    """Sanitize an agent-supplied studio filename into a safe, flat `<name>.md`."""
    stem = (filename or "proposal").strip()
    if stem.endswith(".md"):
        stem = stem[:-3]
    stem = _STUDIO_NAME_RE.sub("_", stem).strip("._-")[:180]
    if not stem:
        raise HTTPException(400, "filename does not yield a usable name")
    return stem + ".md"


@app.get("/api/studio/{platform}")
def studio(platform, status: str | None = None, agent: str | None = None):
    """List studio items (newest first). Each carries file/text plus the human-gate
    fields status/agent/kind. Filterable by ?status= and ?agent=. Legacy .md files with
    no metadata default to status 'draft'."""
    d = ROOT / "studio" / platform
    if not d.exists():
        return []
    meta = _read_json(_studio_meta_path(platform), {})
    out = []
    for f in sorted(d.glob("*.md"), reverse=True):
        m = meta.get(f.name, {})
        out.append({
            "file": f.name,
            "text": f.read_text(encoding="utf-8"),
            "status": m.get("status", "draft"),
            "agent": m.get("agent"),
            "kind": m.get("kind"),
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
            "note": m.get("note"),
        })
    if status:
        out = [x for x in out if x["status"] == status]
    if agent:
        out = [x for x in out if x.get("agent") == agent]
    return out


@app.get("/api/studio/{platform}/{file}")
def studio_item(platform, file):
    """One studio item by filename — so an agent rendering a single approved item
    doesn't have to fetch and filter the entire studio list."""
    pdir(platform)  # validate
    name = _studio_filename(file)
    p = ROOT / "studio" / platform / name
    if not p.exists():
        raise HTTPException(404, f"no studio item {platform}/{name}")
    m = _read_json(_studio_meta_path(platform), {}).get(name, {})
    return {"file": name, "text": p.read_text(encoding="utf-8"),
            "status": m.get("status", "draft"), "agent": m.get("agent"),
            "kind": m.get("kind"), "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"), "note": m.get("note")}


@app.post("/api/studio/{platform}")
def save_proposal(platform, body: Proposal):
    """Producer agents POST generated items here: {filename, text, agent, kind, status}.
    Default status is 'proposed' (enters the human gate)."""
    pdir(platform)  # validate
    d = ROOT / "studio" / platform
    d.mkdir(parents=True, exist_ok=True)
    name = _studio_filename(body.filename)
    (d / name).write_text(body.text, encoding="utf-8")
    meta = _read_json(_studio_meta_path(platform), {})
    now = time.time()
    prev = meta.get(name, {})
    # A re-POST of an existing item MUST NOT silently un-gate it. Only an explicit
    # `status` in the body (or a first insert) may move an item's gate state — otherwise
    # an agent re-posting its own markdown (e.g. to stamp rendered-media info) would
    # reset a human's `approved` decision back to `proposed`.
    status = body.status or prev.get("status") or "proposed"
    if status not in STUDIO_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STUDIO_STATUSES)}")
    meta[name] = {"status": status,
                  "agent": body.agent if body.agent is not None else prev.get("agent"),
                  "kind": body.kind if body.kind is not None else prev.get("kind"),
                  "created_at": prev.get("created_at", now), "updated_at": now,
                  "note": prev.get("note")}
    _write_json(_studio_meta_path(platform), meta)
    return {"ok": True, "file": name, "status": status}


@app.post("/api/studio/{platform}/{file}/status")
def set_studio_status(platform, file, body: StatusUpdate):
    """Record a human-gate decision for a studio item: {status, note}.
    Updates the item's status and appends the decision to studio/<p>/gate.jsonl."""
    pdir(platform)  # validate
    if body.status not in STUDIO_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STUDIO_STATUSES)}")
    name = _studio_filename(file)
    d = ROOT / "studio" / platform
    if not (d / name).exists():
        raise HTTPException(404, f"no studio item {platform}/{name}")
    meta = _read_json(_studio_meta_path(platform), {})
    now = time.time()
    entry = meta.get(name, {"created_at": now})
    entry.update({"status": body.status, "updated_at": now, "note": body.note})
    meta[name] = entry
    _write_json(_studio_meta_path(platform), meta)
    rec = {"ts": now, "file": name, "status": body.status, "note": body.note,
           "agent": entry.get("agent"), "kind": entry.get("kind")}
    _append_jsonl(d / "gate.jsonl", rec)
    log.info("studio gate decision", extra={"platform": platform, "file": name, "status": body.status})
    return {"ok": True, "file": name, "status": body.status}

@app.post("/api/studio/{platform}/{file}/render")
def render_studio_item(platform, file, body: RenderRequest | None = None):
    """Render ONE approved studio item. The producer that WROTE the item is the one
    launched (resolved from the registry), so this generalizes to any producer declaring
    renderable:true — the hub names no agent."""
    pdir(platform)  # validate
    name = _studio_filename(file)
    if not (ROOT / "studio" / platform / name).exists():
        raise HTTPException(404, f"no studio item {platform}/{name}")
    entry = _read_json(_studio_meta_path(platform), {}).get(name, {})
    if entry.get("status") != "approved":
        raise HTTPException(409, "only approved items can be rendered")
    agent = entry.get("agent")
    if not agent:
        raise HTTPException(409, "studio item has no producing agent to render it")

    # A deterministic job key rather than the usual `:{seq}`: the job IS the item, so this
    # both dedupes concurrent renders of the same item and gives the Dashboard a single
    # map lookup (jobs[`${platform}:render:${file}`]) off the existing SSE snapshot.
    key = f"{platform}:render:{name}"
    ex = JOBS.get(key)
    if ex and ex.get("status") in ("queued", "running"):
        return {"job_id": key, "already_running": True}

    args = ["--file", name] + (["--force"] if (body and body.force) else [])
    try:
        job_id = _launch_stage_job(platform, "render", cmd_kwargs={"agent": agent},
                                   extra_args=args, job_key=key,
                                   meta={"file": name, "agent": agent})
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info("render launched", extra={"platform": platform, "file": name, "agent": agent})
    return {"job_id": job_id, "already_running": False}


# ---------------- renders (producer-generated media — NEVER the scraped corpus) ----------------
# Generated reels live in their own namespace, `renders/<platform>/<render_id>/`, served at
# /renders. They must NEVER be written into `media/<platform>/`, which holds scraped corpus
# media keyed by content_id: overwriting a real reel there makes the corpus serve our own
# output under a real creator's id, with metrics that no longer describe the video. That has
# happened once. The separation is enforced structurally below, not by convention.
RENDER_KINDS = {"slideshow", "video"}
ASSET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ASSET_EXTS = {".mp4", ".jpg", ".jpeg", ".png", ".webp"}
# a scraped-corpus filename shape (`<19-digit id>_<user id>.mp4`) — refused outright
CORPUS_NAME_RE = re.compile(r"^\d{15,}_\d+\.")
MAX_RENDER_BYTES = 64 * 1024 * 1024
_RENDER_LOCK = threading.Lock()


def _renders_dir():
    return ROOT / "renders"


def _render_id(studio_file: str) -> str:
    """Derive a render id from the studio filename — SERVER-side, never client-supplied.
    One studio item maps to exactly one render dir, which makes re-rendering idempotent
    (it overwrites in place) and removes path traversal as a possibility at the source."""
    stem = studio_file[:-3] if studio_file.endswith(".md") else studio_file
    rid = _STUDIO_NAME_RE.sub("_", stem).strip("._-")[:120]
    if not rid:
        raise HTTPException(400, "cannot derive a render_id from that filename")
    return rid


def _render_row(platform, rid, rec):
    """Hydrate a stored record with its served URLs and the on-disk path the Dashboard
    offers for manual upload. `?v=<updated_at>` busts the browser cache — without it a
    re-render keeps showing the previous video, since the URL is otherwise unchanged."""
    d = _renders_dir() / platform / rid
    # milliseconds, not seconds: two renders of the same item inside one second would
    # otherwise produce an identical URL and the browser would serve the stale video —
    # which is precisely what this token exists to prevent.
    v = int((rec.get("updated_at") or 0) * 1000)
    names = [a.get("name", "") for a in (rec.get("assets") or [])]
    mp4 = next((n for n in names if n.endswith(".mp4")), None)
    poster = next((n for n in names if n.endswith((".jpg", ".jpeg", ".png", ".webp"))), None)
    return {**rec,
            "video_url": f"/renders/{platform}/{rid}/{mp4}?v={v}" if mp4 else None,
            "poster_url": f"/renders/{platform}/{rid}/{poster}?v={v}" if poster else None,
            "local_path": str(d / mp4) if mp4 else str(d),
            "bytes": sum(a.get("bytes", 0) for a in (rec.get("assets") or []))}


def _render_index():
    return _read_json(_renders_dir() / "index.json", {})


def _rebuild_render_index():
    """Rebuild index.json by walking renders/*/*/render.json. The per-item render.json is
    the source of truth; the index is a derived cache, so a truncated or hand-edited index
    self-heals at startup instead of silently hiding renders."""
    idx = {}
    base = _renders_dir()
    if not base.exists():
        return idx
    for rj in base.glob("*/*/render.json"):
        rec = _read_json(rj, None)
        if isinstance(rec, dict) and rec.get("render_id") and rec.get("platform"):
            idx[f"{rec['platform']}/{rec['render_id']}"] = rec
    _write_json(base / "index.json", idx)
    return idx


@app.post("/api/renders/{platform}")
def save_render(platform, body: RenderIn):
    """A producer uploads a rendered artifact + its metadata. Upsert, keyed on the studio
    filename. Assets arrive base64-encoded (see RenderAssetIn)."""
    pdir(platform)  # validate
    if body.kind not in RENDER_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(RENDER_KINDS)}")
    name = _studio_filename(body.file)
    if not (ROOT / "studio" / platform / name).exists():
        raise HTTPException(404, f"no studio item {platform}/{name}")
    rid = _render_id(name)
    d = _renders_dir() / platform / rid
    d.mkdir(parents=True, exist_ok=True)

    written, total = [], 0
    for a in body.assets:
        an = a.name.lower()
        if not ASSET_NAME_RE.match(an) or Path(an).suffix not in ASSET_EXTS:
            raise HTTPException(400, f"illegal asset name {a.name!r}")
        if CORPUS_NAME_RE.match(an):
            raise HTTPException(400, "asset name looks like a scraped-corpus content_id — refused")
        try:
            raw = base64.b64decode(a.content_b64, validate=True)
        except Exception:
            raise HTTPException(400, f"asset {a.name!r} is not valid base64")
        total += len(raw)
        if total > MAX_RENDER_BYTES:
            raise HTTPException(413, f"render payload exceeds {MAX_RENDER_BYTES} bytes")
        tmp = d / (an + ".part")
        tmp.write_bytes(raw)
        os.replace(tmp, d / an)          # atomic per file: readers see old or new, never torn
        written.append({"name": an, "bytes": len(raw)})

    now = time.time()
    with _RENDER_LOCK:
        idx = _render_index()
        prev = idx.get(f"{platform}/{rid}", {})
        rec = body.model_dump(exclude={"assets"})
        rec.update({"render_id": rid, "platform": platform, "file": name,
                    "assets": written or prev.get("assets", []),
                    "created_at": prev.get("created_at", now), "updated_at": now})
        _write_json(d / "render.json", rec)
        idx[f"{platform}/{rid}"] = rec
        _write_json(_renders_dir() / "index.json", idx)
        _append_jsonl(_renders_dir() / "renders.jsonl",
                      {"ts": now, "platform": platform, "render_id": rid, "file": name,
                       "agent": body.agent, "kind": body.kind, "bytes": total})
    log.info("render saved", extra={"platform": platform, "render_id": rid, "bytes": total})
    return _render_row(platform, rid, rec)


@app.get("/api/renders/{platform}")
def renders(platform, file: str | None = None, agent: str | None = None,
            kind: str | None = None):
    """List renders for a platform, newest first. Filterable by studio file / agent / kind."""
    pdir(platform)  # validate
    rows = [r for k, r in _render_index().items() if k.startswith(f"{platform}/")]
    if file:
        want = _studio_filename(file)
        rows = [r for r in rows if r.get("file") == want]
    if agent:
        rows = [r for r in rows if r.get("agent") == agent]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    rows.sort(key=lambda r: -(r.get("updated_at") or 0))
    return [_render_row(platform, r["render_id"], r) for r in rows]


@app.get("/api/renders/{platform}/{render_id}")
def render_detail(platform, render_id):
    pdir(platform)  # validate
    rec = _render_index().get(f"{platform}/{_render_id(render_id)}")
    if not rec:
        raise HTTPException(404, f"no render {platform}/{render_id}")
    return _render_row(platform, rec["render_id"], rec)


@app.delete("/api/renders/{platform}/{render_id}")
def delete_render(platform, render_id):
    pdir(platform)  # validate
    rid = _render_id(render_id)
    with _RENDER_LOCK:
        idx = _render_index()
        if idx.pop(f"{platform}/{rid}", None) is None:
            raise HTTPException(404, f"no render {platform}/{rid}")
        shutil.rmtree(_renders_dir() / platform / rid, ignore_errors=True)
        _write_json(_renders_dir() / "index.json", idx)
    log.info("render deleted", extra={"platform": platform, "render_id": rid})
    return {"ok": True, "render_id": rid}


@app.get("/api/insights")
def insights():
    return SharedInsights().all()

@app.post("/api/insights")
def add_insight(body: InsightIn):
    r = SharedInsights().add(body.platform, body.kind, body.text, tags=body.tags)
    return r

# ---------------- video analysis (frame-by-frame, written by the VideoAnalysis agent) ----------------
def adir(platform):
    return pdir(platform) and (ROOT / "analysis" / platform)   # pdir validates the platform

@app.get("/api/analysis/{platform}")
def list_analysis(platform):
    """All stored frame-by-frame analyses for a platform (newest first)."""
    d = adir(platform)
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda r: r.get("analyzed_at") or 0, reverse=True)
    return out

def _pending_item(r):
    item = {"content_id": r["content_id"], "url": r.get("url"),
            "video_url": r.get("video_url"), "duration_s": r.get("duration_s"),
            "virality_score": r.get("virality_score"), "tier": r.get("tier"),
            "caption": r.get("caption")}
    # pass through audio fields so AnalysisEngine can enrich its `audio` block (D3b)
    for k in AUDIO_FIELDS:
        item[k] = r.get(k)
    return item


@app.get("/api/analysis/{platform}/pending")
def pending_analysis(platform, min_score: float | None = None, tier: str | None = None,
                     min_duration: float | None = None, max_duration: float | None = None,
                     content_type: str | None = None, limit: int | None = None,
                     reanalyze: str | None = None, stale: bool = False):
    """Clips with local media but no analysis yet, ranked by virality — the analyze queue.

    Default (no filters) = unchanged: unanalyzed clips with local media, ranked by
    virality. Filters (all optional): min_score, tier, min_duration, max_duration,
    content_type, limit. `reanalyze=<content_id>` surfaces that one clip even if already
    analyzed; `stale=true` surfaces analyzed clips whose stored blueprint is schema_version < 2."""
    d = adir(platform)
    have = {f.stem for f in d.glob("*.json")} if d.exists() else set()
    all_rows = _content(platform)

    if reanalyze:
        rows = [r for r in all_rows if r.get("content_id") == reanalyze]
    elif stale:
        stale_ids = set()
        if d.exists():
            for f in d.glob("*.json"):
                doc = _read_json(f, {})
                if doc.get("is_reference"):
                    continue
                if int(doc.get("schema_version") or 1) < 2:
                    stale_ids.add(f.stem)
        rows = [r for r in all_rows if r.get("content_id") in stale_ids]
    else:
        rows = [r for r in all_rows if r.get("video_local") and r.get("content_id") not in have]

    if min_score is not None:
        rows = [r for r in rows if (r.get("virality_score") or 0) >= min_score]
    if tier:
        rows = [r for r in rows if r.get("tier") == tier]
    if min_duration is not None:
        rows = [r for r in rows if (r.get("duration_s") or 0) >= min_duration]
    if max_duration is not None:
        rows = [r for r in rows if (r.get("duration_s") is not None and r["duration_s"] <= max_duration)]
    if content_type:
        rows = [r for r in rows if r.get("content_type") == content_type]

    rows.sort(key=lambda r: -(r.get("virality_score") or 0))
    if limit:
        rows = rows[: int(limit)]
    return [_pending_item(r) for r in rows]

@app.get("/api/analysis/{platform}/{content_id}")
def get_analysis(platform, content_id):
    # Symmetry with save_analysis below, which sanitizes before building the filename.
    # Starlette matches a single path segment so `..%2F..` cannot arrive here today, but a
    # read path that interpolates a raw param into a filename should not rely on that.
    content_id = str(content_id).replace("/", "_")
    if content_id in ("", ".", "..") or "\x00" in content_id:
        raise HTTPException(400, "invalid content_id")
    f = adir(platform) / f"{content_id}.json"
    if not f.exists():
        raise HTTPException(404, f"no analysis for {platform}/{content_id}")
    return json.loads(f.read_text(encoding="utf-8"))

@app.post("/api/analysis/{platform}")
def save_analysis(platform, body: VideoAnalysisIn):
    """An analysis agent POSTs one clip's analysis here.

    Accepts both the lean schema_version 1 doc and AnalysisEngine's rich schema_version 2
    blueprint. References (is_reference=true, content_id `ref_<hash>`) save to the SAME
    `analysis/<p>/<content_id>.json` layout and are served at `/api/analysis/<p>/<ref_id>`."""
    d = adir(platform)
    d.mkdir(parents=True, exist_ok=True)
    rec = body.model_dump(exclude_none=False)
    rec["platform"] = platform
    rec["analyzed_at"] = time.time()
    cid = str(body.content_id).replace("/", "_")
    (d / f"{cid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    # if this is a reference blueprint, flip its registry status to "analyzed"
    if body.is_reference:
        reg_path = ROOT / "references" / platform / "registry.json"
        reg = _read_json(reg_path, {})
        if cid in reg:
            reg[cid]["status"] = "analyzed"
            reg[cid]["analyzed_at"] = rec["analyzed_at"]
            _write_json(reg_path, reg)
    log.info("analysis saved", extra={"platform": platform, "content_id": cid,
                                      "schema_version": body.schema_version,
                                      "is_reference": body.is_reference,
                                      "model": body.model, "shots": len(body.shots or []),
                                      "beats": len(body.beats or [])})
    return {"ok": True, "content_id": cid, "schema_version": body.schema_version,
            "is_reference": body.is_reference}

# ---------------- producer registry (pluggability backbone, §3) ----------------
@app.post("/api/producers/register")
def register_producer(body: ProducerManifest):
    """A producer self-registers its manifest on startup. Idempotent upsert keyed by
    `name` → persisted to producers/registry.json. Secrets are declared by NAME only —
    the hub stores name/env_var/required/present flags, NEVER secret values (§10.4)."""
    reg = _read_json(PRODUCERS_FILE, {})
    now = time.time()
    prev = reg.get(body.name, {})
    manifest = body.model_dump(exclude_none=False)
    manifest["registered_at"] = prev.get("registered_at", now)
    manifest["updated_at"] = now
    reg[body.name] = manifest
    _write_json(PRODUCERS_FILE, reg)
    log.info("producer registered", extra={"producer": body.name, "kind": body.kind})
    return {"ok": True, "name": body.name}


@app.get("/api/producers")
def list_producers():
    """The producer roster (Dashboard renders lanes from this). New producers appear here
    automatically the moment they register."""
    reg = _read_json(PRODUCERS_FILE, {})
    return list(reg.values())


@app.get("/api/producers/{name}")
def get_producer(name):
    reg = _read_json(PRODUCERS_FILE, {})
    if name not in reg:
        raise HTTPException(404, f"no producer {name}")
    return reg[name]


# ---------------- agent workflow board (per-agent live task board) ----------------
@app.get("/api/agents/{name}/board")
def agent_board(name: str, platform: str | None = None, limit_runs: int = 10):
    """Reduce the central log stream into runs -> items -> current stage for one agent.
    For producer kinds, left-join studio gate status so Approved/Rejected land in their lanes.
    Backward-compatible with the coarse run.start/item.done/run.end vocabulary."""
    reg = _read_json(PRODUCERS_FILE, {})
    manifest = reg.get(name, {})
    stages = manifest.get("workflow_stages") or []
    kind = manifest.get("kind")

    recs = [r for r in _read_jsonl(LOGS_FILE) if r.get("agent") == name]
    if platform:
        recs = [r for r in recs if (r.get("platform") in (platform, None, "shared"))]
    recs.sort(key=lambda r: r.get("ts") or 0)

    runs: dict[str, dict] = {}
    order: list[str] = []
    def _run(rid, rec):
        if rid not in runs:
            runs[rid] = {"run_id": rid, "platform": rec.get("platform"),
                         "started": rec.get("ts"), "ended": None,
                         "counts": {"total": 0, "done": 0, "failed": 0}, "_items": {}}
            order.append(rid)
        return runs[rid]

    for r in recs:
        rid = r.get("run_id") or "unknown"
        ev = r.get("event") or ""
        run = _run(rid, r)
        data = r.get("data") or {}
        cid = r.get("content_id")
        if ev == "run.start":
            run["started"] = r.get("ts") or run["started"]
        elif ev == "run.end":
            run["ended"] = r.get("ts")
        elif ev in ("item.start", "item.stage", "item.done", "item.error") and cid:
            it = run["_items"].setdefault(cid, {"content_id": cid, "stage": stages[0] if stages else "Queued",
                                                "score": None, "file": None, "updated": None})
            if ev == "item.error":
                it["stage"] = "Failed"
            elif ev == "item.done":
                it["stage"] = data.get("stage") or ("Proposed" if kind and kind != "analyzer" else "Done")
            else:
                it["stage"] = data.get("stage") or it["stage"]
            if data.get("score") is not None:
                it["score"] = data.get("score")
            if data.get("file"):
                it["file"] = data.get("file")
            it["updated"] = r.get("ts")

    # producer gate join: overwrite terminal stage from studio status by filename
    if kind and kind != "analyzer" and platform:
        studio_items = {s["file"]: s for s in studio(platform) if isinstance(s, dict) and s.get("file")}
        for run in runs.values():
            for it in run["_items"].values():
                st = studio_items.get(it.get("file") or "")
                if st and st.get("status") in ("approved", "rejected"):
                    it["stage"] = "Approved" if st["status"] == "approved" else "Rejected"

    # discovery gate join: overwrite terminal stage from discovery/<p>/gate.jsonl, keyed
    # on content_id (== candidate_id) — no data.file needed, unlike the studio branch above.
    if kind == "discovery" and platform:
        gate_status: dict[str, str] = {}
        for g in _read_jsonl(ROOT / "discovery" / platform / "gate.jsonl"):
            cid = g.get("candidate_id") or g.get("content_id")
            if cid and g.get("status") in ("approved", "rejected"):
                gate_status[cid] = g["status"]
        for run in runs.values():
            for cid, it in run["_items"].items():
                st = gate_status.get(cid)
                if st:
                    it["stage"] = "Approved" if st == "approved" else "Rejected"

    out_runs = []
    for rid in order:
        run = runs[rid]
        items = list(run.pop("_items").values())
        run["items"] = items
        run["counts"]["total"] = len(items)
        run["counts"]["done"] = sum(1 for i in items if i["stage"] in ("Done", "Proposed", "Approved"))
        run["counts"]["failed"] = sum(1 for i in items if i["stage"] in ("Failed", "Rejected"))
        out_runs.append(run)
    out_runs.sort(key=lambda r: r.get("started") or 0, reverse=True)
    return {"agent": name, "kind": kind, "workflow_stages": stages, "runs": out_runs[:max(1, int(limit_runs))]}


# ---------------- reference / template ingestion (only consumer: the template agent) ----------------
def _ref_registry_path(platform):
    return ROOT / "references" / platform / "registry.json"


def _download_reference(url, dest):
    """Best-effort, SAFE reference-media fetch. Prefers yt-dlp if installed (no cookies,
    no login); falls back to a direct HTTP GET for direct media URLs. Never scrapes a
    logged-in session. Returns True on success."""
    # Re-validated here as well as at the endpoint: this is the function that actually
    # dereferences the URL, so it must not depend on every caller having checked first.
    try:
        url = assert_fetchable_url(url)
    except UnsafeFetchURL as e:
        log.warning("refused unsafe reference url", extra={"url": str(url), "err": str(e)})
        return False
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp  # optional; not a hard dependency
        opts = {"outtmpl": str(dest), "quiet": True, "noplaylist": True,
                "format": "mp4/best", "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return dest.exists()
    except ImportError:
        pass
    except Exception as e:
        log.warning("yt-dlp reference download failed", extra={"url": url, "err": str(e)})
    # fallback: direct GET (works for direct .mp4 links; safe, no credentials)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            f.write(r.read())
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        log.warning("direct reference download failed", extra={"url": url, "err": str(e)})
        return False


@app.post("/api/reference/{platform}")
def add_reference(platform, body: ReferenceIn):
    """Register an ad-hoc reference/template video: download its media, assign a synthetic
    id `ref_<hash>`, and mark it PENDING. It is NOT corpus content (never scored, never a
    real reel). AnalysisEngine then analyzes it into a blueprint with is_reference:true."""
    pdir(platform)  # validate
    # Refuse before anything is hashed, registered or written: a rejected URL must leave no
    # trace in the registry, and the caller gets a 400 rather than a silent no_media entry.
    try:
        assert_fetchable_url(body.url)
    except UnsafeFetchURL as e:
        raise HTTPException(400, str(e))
    ref_id = "ref_" + hashlib.sha1(body.url.encode("utf-8")).hexdigest()[:12]
    media_dir = ROOT / "media" / platform
    dest = media_dir / f"{ref_id}.mp4"
    ok = dest.exists() or _download_reference(body.url, dest)
    reg = _read_json(_ref_registry_path(platform), {})
    now = time.time()
    prev = reg.get(ref_id, {})
    reg[ref_id] = {
        "ref_id": ref_id, "url": body.url, "note": body.note,
        "added_at": prev.get("added_at", now),
        "media_local": dest.exists(),
        "status": "analyzed" if (ROOT / "analysis" / platform / f"{ref_id}.json").exists()
                  else ("pending" if dest.exists() else "no_media"),
    }
    _write_json(_ref_registry_path(platform), reg)
    log.info("reference registered", extra={"platform": platform, "ref_id": ref_id, "media_local": dest.exists()})
    return {"ok": True, "ref_id": ref_id, "content_id": ref_id, "media_local": dest.exists(),
            "video_url": (f"/media/{platform}/{ref_id}.mp4" if dest.exists() else None),
            "status": reg[ref_id]["status"], "downloaded": ok}


def _reference_rows(platform):
    reg = _read_json(_ref_registry_path(platform), {})
    adir_p = ROOT / "analysis" / platform
    media_dir = ROOT / "media" / platform
    out = []
    for ref_id, r in reg.items():
        analyzed = (adir_p / f"{ref_id}.json").exists()
        mp4 = media_dir / f"{ref_id}.mp4"
        out.append({
            "ref_id": ref_id, "content_id": ref_id, "url": r.get("url"), "note": r.get("note"),
            "added_at": r.get("added_at"), "is_reference": True,
            "media_local": mp4.exists(),
            "video_url": (f"/media/{platform}/{ref_id}.mp4" if mp4.exists() else None),
            "analyzed": analyzed,
            "status": "analyzed" if analyzed else ("pending" if mp4.exists() else "no_media"),
        })
    out.sort(key=lambda r: -(r.get("added_at") or 0))
    return out


@app.get("/api/reference/{platform}")
def list_references(platform):
    """List registered references + their analysis status."""
    pdir(platform)  # validate
    return _reference_rows(platform)


@app.get("/api/reference/{platform}/pending")
def pending_references(platform):
    """References with local media but no blueprint yet — AnalysisEngine's reference queue.
    Same shape as /api/analysis/<p>/pending items, plus is_reference:true."""
    pdir(platform)  # validate
    return [{"content_id": r["ref_id"], "url": r.get("url"), "video_url": r.get("video_url"),
             "duration_s": None, "virality_score": None, "tier": None, "caption": r.get("note"),
             "is_reference": True}
            for r in _reference_rows(platform) if r["media_local"] and not r["analyzed"]]


# ---------------- discovery / candidate ingestion (only producer: auto-search, PIPELINE.md §11.2) ----------------
def _candidates_path(platform):
    return ROOT / "discovery" / platform / "candidates.json"


def _pages_lines(platform):
    """Non-comment, non-blank lines of pages.txt — same filter as GET /api/config/{p}."""
    pf = pdir(platform) / "pages.txt"
    if not pf.exists():
        return []
    return [l.strip() for l in pf.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def _append_handle_to_pages(platform, handle) -> bool:
    """Safe, comment-preserving, deduped append of an APPROVED discovery handle to
    pages.txt. Dedupes against existing non-comment lines; appends in append-mode
    (never rewrites/reorders the file like put_config's whole-file overwrite). Returns
    False (no-op) if the handle is already present."""
    if not handle:
        return False
    # Compared on the normalized handle, not the raw string: the agent posts the full URL
    # form and a human types the bare handle, so a raw comparison added the same creator
    # twice. See _norm_page_handle.
    existing = {_norm_page_handle(l) for l in _pages_lines(platform)}
    if handle in set(_pages_lines(platform)) or _norm_page_handle(handle) in existing:
        return False
    pf = pdir(platform) / "pages.txt"
    pf.parent.mkdir(parents=True, exist_ok=True)
    prior = pf.read_text(encoding="utf-8") if pf.exists() else ""
    with open(pf, "a", encoding="utf-8") as f:
        if prior and not prior.endswith("\n"):
            f.write("\n")
        f.write(handle + "\n")
    return True


@app.post("/api/discovery/{platform}")
def add_candidate(platform, body: CandidateIn):
    """Ingest/upsert one creator candidate from the auto-search discovery agent. Upserted
    by candidate_id (agent-supplied or a stable `cand_<sha1(platform:handle)>` hash) so
    re-ingesting the same handle never dupes. `status` is hub-managed: forced to 'pending'
    on FIRST insert, and NEVER silently un-gated back to pending if it's already been
    approved/rejected by a human."""
    pdir(platform)  # validate
    cid = body.candidate_id or ("cand_" + hashlib.sha1(f"{platform}:{body.handle}".encode("utf-8")).hexdigest()[:10])
    path = _candidates_path(platform)
    store = _read_json(path, {})
    now = time.time()
    prev = store.get(cid)
    rec = body.model_dump(exclude_none=False)
    rec["candidate_id"] = cid
    rec["platform"] = platform
    rec["ts"] = body.ts or now
    if prev:
        rec["added_at"] = prev.get("added_at", now)
        rec["status"] = prev.get("status", "pending")   # never silently un-gate
        rec["note"] = prev.get("note")
    else:
        rec["added_at"] = now
        rec["status"] = "pending"
    rec["updated_at"] = now
    store[cid] = rec
    _write_json(path, store)
    log.info("discovery candidate ingested", extra={"platform": platform, "candidate_id": cid,
                                                     "handle": body.handle, "status": rec["status"]})
    return {"ok": True, "candidate_id": cid, "handle": body.handle, "status": rec["status"]}


@app.get("/api/discovery/{platform}")
def list_candidates(platform, status: str | None = None):
    """List discovery candidates, newest-first by added_at. Each row carries a derived
    `in_pages` flag (is the handle already a non-comment line in pages.txt?). Optional
    `?status=` filter."""
    pdir(platform)  # validate
    store = _read_json(_candidates_path(platform), {})
    pages = set(_pages_lines(platform))
    rows = sorted(store.values(), key=lambda r: -(r.get("added_at") or 0))
    out = []
    for r in rows:
        r = dict(r)
        r["in_pages"] = r.get("handle") in pages
        out.append(r)
    if status:
        out = [r for r in out if r.get("status") == status]
    return out


@app.get("/api/discovery/{platform}/pending")
def pending_candidates(platform):
    """The human review queue — candidates with status=='pending'."""
    return list_candidates(platform, status="pending")


@app.post("/api/discovery/{platform}/{candidate_id}/status")
def set_candidate_status(platform, candidate_id, body: StatusUpdate):
    """The human gate for discovery candidates: {status, note}. On approved, appends the
    handle to pages.txt (safe/deduped/comment-preserving) and records the outcome in
    discovery/<p>/gate.jsonl, keyed by candidate_id (== content_id for the agent board)."""
    pdir(platform)  # validate
    if body.status not in CANDIDATE_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(CANDIDATE_STATUSES)}")
    path = _candidates_path(platform)
    store = _read_json(path, {})
    if candidate_id not in store:
        raise HTTPException(404, f"no candidate {platform}/{candidate_id}")
    now = time.time()
    rec = store[candidate_id]
    rec["status"] = body.status
    rec["updated_at"] = now
    rec["note"] = body.note
    appended_to_pages = False
    if body.status == "approved":
        appended_to_pages = _append_handle_to_pages(platform, rec.get("handle"))
    store[candidate_id] = rec
    _write_json(path, store)
    gate_rec = {"ts": now, "candidate_id": candidate_id, "content_id": candidate_id,
                "handle": rec.get("handle"), "status": body.status, "note": body.note,
                "appended_to_pages": appended_to_pages}
    _append_jsonl(ROOT / "discovery" / platform / "gate.jsonl", gate_rec)
    log.info("discovery gate decision", extra={"platform": platform, "candidate_id": candidate_id,
                                               "status": body.status, "appended_to_pages": appended_to_pages})
    return {"ok": True, "candidate_id": candidate_id, "status": body.status,
            "appended_to_pages": appended_to_pages}


# ---------------- audio / sound intelligence (audio_id is the sound join key) ----------------
def _content_json(platform):
    """Raw content.json rows (carry posted_ts + audio fields) for sound aggregation."""
    return _read_json(pdir(platform) / "content.json", [])


def _parse_window(window):
    """'14d' / '30d' / '14' -> days (int). Defaults to 14."""
    if not window:
        return 14
    s = str(window).strip().lower().rstrip("d")
    try:
        return max(int(float(s)), 1)
    except ValueError:
        return 14


@app.get("/api/audio/{platform}/trending")
def audio_trending(platform, window: str = "14d", limit: int = 50,
                   reusable_only: bool = False, mood: str | None = None,
                   min_trend: float | None = None):
    """Ranked trending sounds, derived from the audio metadata in scraped reels.
    NOTE (§8): this is trending WITHIN your tracked creators, not the platform-wide chart."""
    pdir(platform)  # validate
    rows = _content_json(platform)
    sounds = collect_sounds(rows, window_days=_parse_window(window))
    if reusable_only:
        sounds = [s for s in sounds if s.get("is_reusable")]
    if mood:
        m = mood.lower()
        sounds = [s for s in sounds if m in ((s.get("title") or "") + " " + (s.get("artist") or "")).lower()]
    if min_trend is not None:
        sounds = [s for s in sounds if (s.get("trend_score") or 0) >= min_trend]
    return sounds[: int(limit)]


@app.get("/api/audio/{platform}/sound/{audio_id}")
def audio_sound(platform, audio_id):
    """Detail for one sound + the reels using it."""
    pdir(platform)  # validate
    rows = _content_json(platform)
    sounds = {s["audio_id"]: s for s in collect_sounds(rows, window_days=14)}
    if audio_id not in sounds:
        raise HTTPException(404, f"no sound {audio_id} in {platform} corpus")
    using = [{"content_id": r.get("content_id"), "url": r.get("url"),
              "virality_score": r.get("virality_score"), "tier": r.get("tier"),
              "creator": r.get("creator"), "posted": r.get("posted_iso")}
             for r in rows if str(r.get("audio_id")) == str(audio_id)]
    using.sort(key=lambda r: -(r.get("virality_score") or 0))
    return {**sounds[audio_id], "reels": using}


# ---------------- unified agent logging (§10.1) ----------------
@app.post("/api/logs")
def add_log(body: LogIn):
    """Agents POST curated LIFECYCLE events here (run start/end, item done, errors, evals)
    — never every debug line. Appended to logs/agents.jsonl and streamed on the SSE `log`
    channel of /api/events. `run_id` links back to the agent's local log file."""
    rec = body.model_dump(exclude_none=False)
    rec["ts"] = body.ts or time.time()
    _append_jsonl(LOGS_FILE, rec)
    _push_log(rec)
    return {"ok": True}


@app.get("/api/logs")
def get_logs(agent: str | None = None, level: str | None = None,
             since: float | None = None, run_id: str | None = None, limit: int = 500):
    """Query the central agent log. Filters: agent, level, since (epoch seconds), run_id."""
    recs = _read_jsonl(LOGS_FILE)
    if agent:
        recs = [r for r in recs if r.get("agent") == agent]
    if level:
        recs = [r for r in recs if r.get("level") == level]
    if run_id:
        recs = [r for r in recs if r.get("run_id") == run_id]
    if since is not None:
        recs = [r for r in recs if (r.get("ts") or 0) >= since]
    return recs[-int(limit):]


# ---------------- evaluation store (§10.2) ----------------
@app.post("/api/evals")
def add_eval(body: EvalIn):
    """Store a self-eval / judge result: evals/<agent>/<target_id>.json + evals/evals.jsonl.
    Decouples evaluation from the artifact."""
    rec = body.model_dump(exclude_none=False)
    rec["ts"] = body.ts or time.time()
    safe_agent = str(body.agent).replace("/", "_")
    safe_id = str(body.target_id).replace("/", "_")
    _write_json(ROOT / "evals" / safe_agent / f"{safe_id}.json", rec)
    _append_jsonl(ROOT / "evals" / "evals.jsonl", rec)
    return {"ok": True, "agent": body.agent, "target_id": body.target_id}


@app.get("/api/evals")
def get_evals(agent: str | None = None, target_type: str | None = None,
              since: float | None = None, limit: int = 500):
    """Query the eval store. Filters: agent, target_type, since (epoch seconds)."""
    recs = _read_jsonl(ROOT / "evals" / "evals.jsonl")
    if agent:
        recs = [r for r in recs if r.get("agent") == agent]
    if target_type:
        recs = [r for r in recs if r.get("target_type") == target_type]
    if since is not None:
        recs = [r for r in recs if (r.get("ts") or 0) >= since]
    return recs[-int(limit):]


# ---------------- per-agent config, schema-driven (§10.3) ----------------
def _config_defaults_from_schema(schema):
    """Pull top-level `default` values out of a JSON Schema's properties."""
    out = {}
    for k, spec in ((schema or {}).get("properties") or {}).items():
        if isinstance(spec, dict) and "default" in spec:
            out[k] = spec["default"]
    return out


@app.get("/api/config/agent/{agent}")
def get_agent_config(agent):
    """The agent's stored config, layered over defaults read from its manifest
    `config_schema` (so a freshly-registered agent is configurable immediately)."""
    reg = _read_json(PRODUCERS_FILE, {})
    schema = (reg.get(agent) or {}).get("config_schema")
    defaults = _config_defaults_from_schema(schema)
    stored = _read_json(ROOT / "config" / "agents" / f"{agent}.json", {})
    return {"agent": agent, "config": {**defaults, **stored},
            "defaults": defaults, "config_schema": schema}


@app.put("/api/config/agent/{agent}")
def put_agent_config(agent, body: AgentConfigIn):
    """Write the agent's config (Dashboard edits it here)."""
    _write_json(ROOT / "config" / "agents" / f"{agent}.json", body.config)
    log.info("agent config saved", extra={"agent": agent})
    return {"ok": True, "agent": agent}


@app.get("/api/config/agent/{agent}/secrets/status")
def agent_secrets_status(agent):
    """Secret STATUS only — never values (§10.4). Returns [{name, env_var, present, required}].

    Presence is evaluated NOW, against the hub's environment and the agent's own .env — it
    used to be replayed from what the agent self-reported when it last registered, and that
    is a snapshot, not a status. Paste a key into ./init and the Agent Desk went on showing
    "SECRET MISSING" until the agent happened to run again and re-register; the Board's
    readiness check, which reads the .env directly, said the opposite at the same moment.
    Two code paths answering one question with opposite answers.

    The two answers are OR-ed, and deliberately not the other way round: the AGENT can see
    sources the hub cannot. AutoSearch accepts `IG_SESSIONID` or a gitignored session.txt;
    AnalysisEngine accepts GEMINI_API_KEY, GEMINI_KEY *or* GOOGLE_API_KEY while its manifest
    names only the first. Letting a live `False` overrule a self-reported `True` would
    report a working agent as broken — a worse error than the one being fixed, and a
    silent one. So the live check can only ever ADD presence.

    The cost is that a key deleted by hand still reads as present until the agent next
    registers. That was already true before this endpoint looked at anything live.
    """
    reg = _read_json(PRODUCERS_FILE, {})
    if agent not in reg:
        raise HTTPException(404, f"no producer {agent}")
    agent_dir = _agent_env_dir(agent, reg[agent])
    out = []
    for s in (reg[agent].get("secrets") or []):
        present = bool(s.get("present")) or bool(_secret_present(s.get("env_var"), agent_dir))
        out.append({"name": s.get("name"), "env_var": s.get("env_var"),
                    "present": present, "required": s.get("required", True)})
    return out


# ---------------- pipeline control + live status ----------------
JOBS = {}          # job_id -> {platform, stage, status, started, ended, rc}
_JOB_SEQ = 0
_JOB_SEQ_LOCK = threading.Lock()
ANALYSIS_ENGINE_DIR = ROOT.parent / "AnalysisEngine"
AUTO_SEARCH_DIR = ROOT.parent / "AutoSearch"
STAGE_CMD = {
    "scrape":  lambda p: ([PY, "scrape.py", "--file", "pages.txt"], pdir(p)),
    "analyze": lambda p: ([PY, "run.py", "analyze"], pdir(p)),
    "media":   lambda p: ([PY, "download_media.py", p], ROOT),
    # AnalysisEngine is a sibling uv-managed project; shell out to its own CLI (built in 6.2).
    "analysis-engine": lambda p: (["uv", "run", "cli.py", "run", p], ANALYSIS_ENGINE_DIR),
    # AutoSearch (discovery, §11) — sibling uv-managed project. "auto-search" = manual/exhaustive
    # pass; "auto-search-beat" = the bounded, mostly-no-op heartbeat tick fired by the scheduler below.
    "auto-search":      lambda p: (["uv", "run", "cli.py", "run", p], AUTO_SEARCH_DIR),
    "auto-search-beat": lambda p: (["uv", "run", "cli.py", "beat", p], AUTO_SEARCH_DIR),
}


def _producer_dir(agent: str) -> Path:
    """Resolve a producer's sibling directory from its REGISTERED manifest, so the hub
    hardcodes no producer name or path. A renderable producer self-declares:
        {"renderable": true, "dir": "SimilarContent",
         "render_cmd": ["uv", "run", "cli.py", "render"]}
    The declared dir is validated to be a direct sibling of this repo — an agent cannot
    talk the hub into executing something elsewhere on the filesystem.

    SCOPE: this constrains the working DIRECTORY. The COMMAND is constrained separately by
    `_validate_render_cmd`, which allowlists the launcher and shape-checks every argument —
    both are required, because `POST /api/producers/register` is unauthenticated and
    `ProducerManifest` sets `extra="allow"`, so `render_cmd` is caller-supplied. Together
    they mean a registered producer can only run a known launcher, inside its own sibling
    directory, with plain arguments. `subprocess.run` never uses `shell=True`.

    The hub remains a single-user tool bound to 127.0.0.1 with no auth (see SECURITY.md);
    do not expose it. But an open browser tab is inside that perimeter, which is why CORS
    is restricted to loopback origins and why the argv is allowlisted rather than trusted."""
    m = _read_json(PRODUCERS_FILE, {}).get(agent) or {}
    if not m.get("renderable"):
        raise HTTPException(400, f"producer {agent!r} does not declare renderable:true")
    rel = str(m.get("dir") or "").strip()
    if not rel or "/" in rel or "\\" in rel or rel.startswith("."):
        raise HTTPException(400, f"producer {agent!r} declared an illegal dir {rel!r}")
    d = (ROOT.parent / rel).resolve()
    if d.parent != ROOT.parent.resolve() or not d.is_dir():
        raise HTTPException(400, f"producer dir {rel!r} is not a sibling directory")
    return d


# A producer's `render_cmd` arrives over an UNAUTHENTICATED route
# (POST /api/producers/register) and ends up as argv for subprocess.run. Without a
# constraint that is remote code execution for anything that can reach this port —
# including, thanks to the browser, any page you happen to have open.
#
# So the launcher is allowlisted and the arguments are shape-checked. This is not
# defence in depth, it is the actual boundary: `dir` only pins the working directory,
# never the command.
RENDER_LAUNCHERS = {"uv", "python", "python3", "node", "npm"}
_RENDER_ARG_RE = re.compile(r"^[A-Za-z0-9._/=:-]{1,120}$")


def _validate_render_cmd(agent: str, argv: list[str]) -> list[str]:
    """Reject any render_cmd that isn't a plain invocation of a known launcher."""
    if not argv:
        raise HTTPException(400, f"producer {agent!r} declared an empty render_cmd")
    if argv[0] not in RENDER_LAUNCHERS:
        raise HTTPException(
            400, f"producer {agent!r} render_cmd must start with one of "
                 f"{sorted(RENDER_LAUNCHERS)}, got {argv[0]!r}")
    for a in argv:
        # No shell metacharacters, no whitespace-smuggled extra words, no absolute paths
        # or traversal — the command must run inside the producer's own directory.
        if not _RENDER_ARG_RE.match(a):
            raise HTTPException(400, f"producer {agent!r} render_cmd has an illegal "
                                     f"argument {a!r}")
        if a.startswith("/") or ".." in a:
            raise HTTPException(400, f"producer {agent!r} render_cmd may not reference "
                                     f"an absolute path or a parent directory: {a!r}")
    return argv


def _render_stage_cmd(platform, agent="similar-content"):
    m = _read_json(PRODUCERS_FILE, {}).get(agent) or {}
    argv = [str(x) for x in (m.get("render_cmd") or ["uv", "run", "cli.py", "render"])]
    argv = _validate_render_cmd(agent, argv)
    return (argv + ["--platform", platform], _producer_dir(agent))


# Per-ITEM render of an approved studio item. Never added to RUN_ALL_STAGES: rendering
# calls a paid image API, so it only ever runs when a human explicitly asks for it.
STAGE_CMD["render"] = _render_stage_cmd

def _stage_env():
    """Environment for a spawned stage.

    Every sibling agent resolves the hub from `BACKEND_API`, defaulting to
    http://127.0.0.1:8787. When the hub is serving on any other port — because 8787 was
    busy and `cli.py` fell back — a child that inherits a bare environment would silently
    talk to the wrong address (or nothing at all). `cli.py start` exports the real URL; this
    guarantees the child sees it even when the hub was launched some other way.
    """
    env = dict(os.environ)
    env.setdefault("BACKEND_API", f"http://{os.environ.get('HUB_HOST', '127.0.0.1')}:"
                                  f"{os.environ.get('HUB_PORT', '8787')}")
    return env


TAIL_CHARS = 1200


def _job_tail(stdout, stderr) -> str:
    """The last of what a stage said, keeping BOTH streams.

    This was `(stdout or stderr or "")[-400:]` — an `or`, so a stage that wrote a single
    progress line to stdout had its entire stderr discarded. Every stage here logs
    progress to stdout and errors to stderr, which meant the failure reason was thrown
    away in precisely the case anyone needed it: the Dashboard showed a red node whose
    tail was the last routine progress line, and nothing anywhere in the app said why.

    stderr goes last so that when the combined text is truncated it is the routine
    progress that gets cut, never the error.
    """
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    parts = [p for p in (out, err) if p]
    return "\n".join(parts)[-TAIL_CHARS:]


def _run_job(job_id, cmd, cwd):
    JOBS[job_id]["status"] = "running"
    log.info("job started", extra={"job_id": job_id, "cmd": " ".join(cmd)})
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           env=_stage_env())
        JOBS[job_id]["rc"] = r.returncode
        JOBS[job_id]["status"] = "done" if r.returncode == 0 else "error"
        JOBS[job_id]["tail"] = _job_tail(r.stdout, r.stderr)
        log.info("job finished", extra={"job_id": job_id, "rc": r.returncode, "status": JOBS[job_id]["status"]})
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        # NOT None. `_run_stage_blocking` returns this rc, and the run-all supervisor reads
        # None as "unknown stage, skip cleanly" — so a stage that crashed outright used to
        # let the rest of the pipeline run on regardless.
        JOBS[job_id]["rc"] = -1
        JOBS[job_id]["tail"] = str(e)
        log.error("job crashed", extra={"job_id": job_id, "err": str(e)})
    JOBS[job_id]["ended"] = time.time()
    # Surface a failed pipeline stage on the central activity log so it shows up in the
    # Dashboard's Activity view (and streams over the SSE `log` channel). Pipeline stages
    # are not registered agents and have no board of their own, so without this a stage that
    # dies — e.g. scrape with no handles in pages.txt — fails completely silently. The error
    # tail is already on JOBS[...].tail for the point-of-action UI; this makes the SAME tail
    # visible in the one place users go looking for "what happened".
    if JOBS[job_id]["status"] == "error":
        j = JOBS[job_id]
        stage = j.get("stage", "pipeline")
        rec = {"ts": j.get("ended") or time.time(), "agent": "pipeline", "level": "error",
               "event": "job_failed", "msg": f"{stage} failed (rc {j.get('rc')})",
               "platform": j.get("platform"), "run_id": job_id,
               "data": {"stage": stage, "rc": j.get("rc"), "tail": j.get("tail", "")}}
        try:
            _append_jsonl(LOGS_FILE, rec); _push_log(rec)
        except Exception:
            log.exception("failed to record job failure to activity log")


def _launch_stage_job(platform, stage, cmd_kwargs=None, extra_args=None,
                      job_key=None, meta=None):
    """Shared job-launch internals behind POST /api/pipeline/{p}/{stage} — also used by the
    discovery heartbeat scheduler so a scheduled auto-search-beat is a first-class job,
    visible in /api/pipeline/status and streamed on /api/events like any manual run.

    The optional args exist for per-ITEM stages (currently `render`): cmd_kwargs selects
    which producer to launch, extra_args names the item, and job_key replaces the usual
    `:{seq}` id with a deterministic one so the job doubles as a per-item lock."""
    global _JOB_SEQ
    if stage not in STAGE_CMD:
        raise ValueError(f"stage must be one of {list(STAGE_CMD)}")
    cmd, cwd = STAGE_CMD[stage](platform, **(cmd_kwargs or {}))
    if extra_args:
        cmd = list(cmd) + [str(a) for a in extra_args]
    if job_key:
        job_id = job_key
    else:
        with _JOB_SEQ_LOCK:
            _JOB_SEQ += 1
            job_id = f"{platform}:{stage}:{_JOB_SEQ}"
    JOBS[job_id] = {"platform": platform, "stage": stage, "status": "queued",
                    "started": time.time(), "ended": None, "rc": None, "tail": "",
                    **(meta or {})}
    threading.Thread(target=_run_job, args=(job_id, cmd, cwd), daemon=True).start()
    return job_id


# The CORE pipeline, in dependency order. Discovery (auto-search) is intentionally excluded —
# it's opt-in and gated behind human review, never part of the one-click core run.
RUN_ALL_STAGES = ["scrape", "analyze", "media", "analysis-engine"]


def _run_stage_blocking(platform, stage, run_id=None):
    """Same job-setup as `_launch_stage_job` (same JOBS entry shape, same SSE visibility)
    but runs the subprocess SYNCHRONOUSLY on the calling thread instead of spawning a
    daemon. Returns the stage's return code (JOBS[job_id]["rc"]), or None if the stage key
    is unknown (skipped cleanly).

    `run_id` is stamped onto the job so the Dashboard can tell which stages belong to one
    "Run full pipeline" click. It used to be generated, returned to the caller, and then
    attached to nothing at all."""
    global _JOB_SEQ
    if stage not in STAGE_CMD:
        return None
    cmd, cwd = STAGE_CMD[stage](platform)
    with _JOB_SEQ_LOCK:
        _JOB_SEQ += 1
        job_id = f"{platform}:{stage}:{_JOB_SEQ}"
    JOBS[job_id] = {"platform": platform, "stage": stage, "status": "queued",
                    "started": time.time(), "ended": None, "rc": None, "tail": "",
                    "run_id": run_id}
    _run_job(job_id, cmd, cwd)   # blocks until this stage finishes (sets rc/status/tail/ended)
    return JOBS[job_id].get("rc")


# Platforms with a run-all in flight. A second click used to start a second supervisor over
# the same files — two scrapes writing one reels_raw.json, an analyze reading it mid-write.
_RUNNING_ALL: set = set()
_RUNNING_ALL_LOCK = threading.Lock()


def _run_all_supervisor(platform, run_id, stages):
    """Supervising daemon thread body: run the core stages IN SEQUENCE, waiting for each to
    finish before starting the next. Unknown stage keys are skipped cleanly. If any stage
    exits non-zero, STOP — later stages are not run and the run is marked failed."""
    log.info("run-all started", extra={"run_id": run_id, "platform": platform, "stages": stages})
    try:
        for stage in stages:
            rc = _run_stage_blocking(platform, stage, run_id=run_id)
            if rc is None:
                log.warning("run-all skipping unknown stage",
                            extra={"run_id": run_id, "stage": stage})
                continue
            if rc != 0:
                log.error("run-all halted on stage failure",
                          extra={"run_id": run_id, "stage": stage, "rc": rc})
                # The per-stage failure is already on the activity log; this says the RUN
                # stopped, and where. Without it the log showed one red stage and simply
                # nothing afterwards, which reads the same as a run still in progress.
                _record_run_all_halt(platform, run_id, stage, rc, stages)
                return
        log.info("run-all finished", extra={"run_id": run_id, "platform": platform})
    finally:
        with _RUNNING_ALL_LOCK:
            _RUNNING_ALL.discard(platform)


def _record_run_all_halt(platform, run_id, stage, rc, stages):
    remaining = stages[stages.index(stage) + 1:] if stage in stages else []
    skipped = f" — {', '.join(remaining)} not run" if remaining else ""
    rec = {"ts": time.time(), "agent": "pipeline", "level": "error", "event": "run_halted",
           "msg": f"Full pipeline stopped at {stage} (rc {rc}){skipped}",
           "platform": platform, "run_id": run_id,
           "data": {"stage": stage, "rc": rc, "skipped": remaining}}
    try:
        _append_jsonl(LOGS_FILE, rec); _push_log(rec)
    except Exception:
        log.exception("failed to record run-all halt to activity log")


@app.post("/api/pipeline/{platform}/run-all")
def run_all(platform):
    """One-click core pipeline: scrape -> analyze -> media -> analysis-engine, run in sequence
    inside a single supervising daemon thread. Returns immediately; per-stage progress shows up
    in the existing JOBS dict (so /api/pipeline/status + /api/events reflect it unchanged). A
    non-zero stage halts the sequence."""
    stages = list(RUN_ALL_STAGES)
    return {"run_id": _start_run_all(platform, stages), "stages": stages}


def _start_run_all(platform, stages, trigger="manual"):
    """Claim the platform and start a supervising thread. Shared by the route and the
    scheduler so both get the same in-flight guard and the same readiness refusal."""
    if platform not in PLATFORMS:
        raise HTTPException(404, f"unknown platform {platform}")
    # One run per platform. A second click used to start a second supervisor over the same
    # files: two scrapes rewriting one reels_raw.json, an analyze reading it half-written.
    with _RUNNING_ALL_LOCK:
        if platform in _RUNNING_ALL:
            raise HTTPException(409, f"a full pipeline run is already in progress for {platform}")
        _RUNNING_ALL.add(platform)
    try:
        # Refuse a run that cannot get past its first stage, with the reason the stage
        # itself would have given — rather than spawning it to fail four seconds later.
        ready = stage_readiness(platform).get(stages[0], {})
        if not ready.get("ready", True):
            raise HTTPException(409, ready.get("reason") or "the pipeline is not ready to run")
        with _JOB_SEQ_LOCK:
            run_id = f"{platform}:run-all:{int(time.time())}"
        threading.Thread(target=_run_all_supervisor, args=(platform, run_id, list(stages)),
                         daemon=True).start()
    except BaseException:
        with _RUNNING_ALL_LOCK:      # the supervisor never started, so it cannot clear this
            _RUNNING_ALL.discard(platform)
        raise
    log.info("run-all launched", extra={"platform": platform, "run_id": run_id,
                                        "stages": list(stages), "trigger": trigger})
    return run_id


# ---- the catch-all, LAST ------------------------------------------------------------
# Starlette resolves routes in registration order, so this must stay below /run-all. It
# used to be declared ~100 lines above it, which meant POST /api/pipeline/{p}/run-all was
# swallowed here as stage="run-all" and answered 400 "stage must be one of [...]" — the
# Run full pipeline button never once started a pipeline. The Dashboard defines no
# onError, so the 400 was discarded and the click looked like it did nothing at all.
# Same rule the media/renders/documentation mounts already follow above.
@app.post("/api/pipeline/{platform}/{stage}")
def run_stage(platform, stage, force: bool = False):
    """Launch one stage.

    Refuses with 409 when the stage's input is not there yet. The stages themselves have
    always refused cleanly ("no scraped data — scrape first"), but only after being
    spawned, so the reason arrived as a subprocess tail and every Run button looked
    equally live. Checking up front lets the Dashboard grey the button and name the stage
    that unblocks it — and keeps that guarantee for anything driving the API directly.

    `?force=true` is the deliberate escape hatch: the readiness check is a convenience,
    not a security boundary, and an operator who knows better should not have to fight it.
    """
    if not force:
        r = stage_readiness(platform).get(stage)
        if r and not r["ready"]:
            raise HTTPException(409, r["reason"])
    try:
        job_id = _launch_stage_job(platform, stage)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job_id": job_id}


# ---------------- scheduled pipeline runs ----------------
# The hub has to be running for any of this to fire — there is no daemon outside it, by
# design (everything is local-first, $0, no cron dependency). A schedule is therefore a
# best-effort "while you have this open", not a guarantee, and the UI says so.
def _schedule_file():
    """Resolved per call, not bound at import: ROOT is repointed at a tmp dir by the test
    fixture (and could be by any embedder), and a module-level constant would keep writing
    to the developer's real config — which is exactly what it did until a test caught it."""
    return ROOT / "config" / "pipeline_schedule.json"

# Free stages only. analysis-engine calls a paid API on every clip, and this runs
# UNATTENDED — the same reasoning that already keeps `render` out of RUN_ALL_STAGES.
# Including it is a deliberate opt-in per platform.
SCHEDULED_STAGES_FREE = ["scrape", "analyze", "media"]
SCHEDULE_DEFAULTS = {"enabled": False, "every_hours": 24,
                     "include_blueprints": False, "last_run_at": 0}
_SCHEDULE_LOCK = threading.Lock()


def _read_schedule():
    """Fail-closed read of the whole schedule map. ANY problem resolves to disabled — a
    scheduler must never turn itself on because a file was unreadable."""
    try:
        raw = _read_json(_schedule_file(), {})
        if not isinstance(raw, dict):
            raw = {}
    except Exception as e:
        log.warning("schedule unreadable, failing closed", extra={"err": str(e)})
        raw = {}
    out = {}
    for p in PLATFORMS:
        row = raw.get(p) if isinstance(raw.get(p), dict) else {}
        merged = {**SCHEDULE_DEFAULTS, **row}
        try:
            merged["every_hours"] = max(1.0, float(merged["every_hours"] or 24))
        except (TypeError, ValueError):
            merged["every_hours"] = 24.0
        merged["enabled"] = bool(merged["enabled"])
        merged["include_blueprints"] = bool(merged["include_blueprints"])
        try:
            merged["last_run_at"] = float(merged.get("last_run_at") or 0)
        except (TypeError, ValueError):
            merged["last_run_at"] = 0.0
        out[p] = merged
    return out


def _write_schedule(sched):
    f = _schedule_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sched, indent=2), encoding="utf-8")


def scheduled_stages(row):
    return list(SCHEDULED_STAGES_FREE) + (["analysis-engine"] if row["include_blueprints"] else [])


@app.get("/api/schedule")
def get_schedule():
    """Per-platform automatic run settings, with `next_run_at` derived so the UI never has
    to recompute the interval arithmetic."""
    sched = _read_schedule()
    for p, row in sched.items():
        row["stages"] = scheduled_stages(row)
        row["next_run_at"] = (row["last_run_at"] + row["every_hours"] * 3600
                              if row["enabled"] and row["last_run_at"] else None)
    return sched


@app.put("/api/schedule/{platform}")
def put_schedule(platform, body: ScheduleIn):
    if platform not in PLATFORMS:
        raise HTTPException(404, f"unknown platform {platform}")
    with _SCHEDULE_LOCK:
        sched = _read_schedule()
        row = sched[platform]
        if body.enabled is not None:
            # Enabling starts the clock now, so turning it on does not immediately fire a
            # run off a stale (or zero) last_run_at.
            if body.enabled and not row["enabled"]:
                row["last_run_at"] = time.time()
            row["enabled"] = bool(body.enabled)
        if body.every_hours is not None:
            row["every_hours"] = max(1.0, float(body.every_hours))
        if body.include_blueprints is not None:
            row["include_blueprints"] = bool(body.include_blueprints)
        _write_schedule(sched)
    log.info("schedule updated", extra={"platform": platform, **{k: row[k] for k in
             ("enabled", "every_hours", "include_blueprints")}})
    return get_schedule()[platform]


def _schedule_tick(now=None):
    """One pass. Returns the platforms it started, so the loop and the tests can both see
    what happened without reading logs."""
    now = now or time.time()
    started = []
    with _SCHEDULE_LOCK:
        sched = _read_schedule()
        due = [p for p, r in sched.items()
               if r["enabled"] and now - r["last_run_at"] >= r["every_hours"] * 3600]
        # Stamp BEFORE launching: a run that takes an hour must not come due again while
        # it is still going, and a crash mid-run must not re-fire on the next tick.
        for p in due:
            sched[p]["last_run_at"] = now
        if due:
            _write_schedule(sched)
    for p in due:
        row = sched[p]
        ready = stage_readiness(p).get(SCHEDULED_STAGES_FREE[0], {})
        if not ready.get("ready", True):
            # Nothing to scrape. Skip quietly rather than starting a run that exists only
            # to fail — the clock has already moved on, so this does not spin.
            log.info("scheduled run skipped", extra={"platform": p, "why": ready.get("reason")})
            continue
        try:
            run_id = _start_run_all(p, scheduled_stages(row), trigger="schedule")
            started.append(p)
            log.info("scheduled run started", extra={"platform": p, "run_id": run_id})
        except HTTPException as e:
            # 409 = one already in flight. Expected when a manual run overlaps a schedule.
            log.info("scheduled run not started", extra={"platform": p, "detail": e.detail})
        except Exception as e:
            log.error("scheduled run failed to start", extra={"platform": p, "err": str(e)})
    return started


def _schedule_loop():
    """Background daemon. Ticks every few minutes; the interval arithmetic lives in
    last_run_at (persisted), not in how long this sleeps — so restarting the hub neither
    re-fires a run nor loses the schedule. Never raises into the caller."""
    while True:
        try:
            _schedule_tick()
        except Exception as e:
            log.error("schedule loop error (staying idle)", extra={"err": str(e)})
        time.sleep(300 * (1.0 + random.uniform(-0.1, 0.1)))


# ---------------- discovery heartbeat scheduler + kill-switch (PIPELINE.md §11.1/§11.2) ----------------
def _discovery_agent_config():
    """Fail-closed read of auto-search's hub-stored config. ANY problem (missing file,
    bad json, unreadable path) resolves to discovery_enabled=False — the scheduler must
    never accidentally turn itself on."""
    try:
        cfg = _read_json(ROOT / "config" / "agents" / "auto-search.json", {})
        if not isinstance(cfg, dict):
            return False, 20.0
        enabled = bool(cfg.get("discovery_enabled", False))
        heartbeat_minutes = float(cfg.get("heartbeat_minutes") or 20)
        if heartbeat_minutes <= 0:
            heartbeat_minutes = 20.0
        return enabled, heartbeat_minutes
    except Exception as e:
        log.warning("discovery scheduler: config unreadable, failing closed", extra={"err": str(e)})
        return False, 20.0


def _discovery_heartbeat_loop():
    """Background daemon thread: ONLY while auto-search's `discovery_enabled` config flag
    is true, fire the auto-search-beat stage for each platform every heartbeat_minutes ±
    jitter. Off by default (discovery_enabled defaults false -> this loop stays idle
    forever, doing nothing but a cheap config read + sleep). Never raises into the caller
    (started fire-and-forget from the startup hook so it can never block startup)."""
    while True:
        try:
            enabled, heartbeat_minutes = _discovery_agent_config()
            if enabled:
                for p in PLATFORMS:
                    try:
                        job_id = _launch_stage_job(p, "auto-search-beat")
                        log.info("discovery heartbeat fired", extra={"platform": p, "job_id": job_id})
                    except Exception as e:
                        log.error("discovery heartbeat failed to launch", extra={"platform": p, "err": str(e)})
            else:
                log.info("discovery heartbeat scheduler idle (discovery_enabled=false)")
        except Exception as e:
            # belt-and-suspenders: the loop itself must never die or take the thread down.
            log.error("discovery heartbeat loop error (staying idle)", extra={"err": str(e)})
            heartbeat_minutes = 20.0
        jitter = random.uniform(-0.15, 0.15)
        sleep_s = max(30.0, heartbeat_minutes * 60.0 * (1.0 + jitter))
        time.sleep(sleep_s)

@app.get("/api/pipeline/status")
def pipeline_status():
    return JOBS

@app.get("/api/events")
async def events(request: Request):
    """SSE stream. Default (unnamed) frames carry the JOBS snapshot (unchanged — existing
    consumers keep working). Named `event: log` frames carry new central log events (§10.1)
    for the Activity view / data-flow animation. Subscribe with EventSource.onmessage (jobs)
    + addEventListener('log', ...)."""
    async def gen():
        last = None
        log_idx = len(_LOG_BUF)   # only stream logs that arrive AFTER connect
        while True:
            if await request.is_disconnected():
                break
            snap = json.dumps(JOBS, default=str)
            if snap != last:
                last = snap
                yield f"data: {snap}\n\n"
            with _LOG_LOCK:
                new = _LOG_BUF[log_idx:]
                log_idx = len(_LOG_BUF)
            for rec in new:
                yield f"event: log\ndata: {json.dumps(rec, default=str)}\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream")

# ---------------- media + docs + frontend static ----------------
MEDIA = ROOT / "media"; MEDIA.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA)), name="media")   # range-request capable

# Producer-generated media, in its own namespace. Kept structurally separate from /media
# (scraped corpus) — see the renders section above for why. Mounted before the "/" catch-all
# because Starlette resolves mounts in registration order.
RENDERS = ROOT / "renders"; RENDERS.mkdir(exist_ok=True)
assert RENDERS.resolve() != MEDIA.resolve(), "render + corpus media namespaces must not merge"
app.mount("/renders", StaticFiles(directory=str(RENDERS)), name="renders")  # range-request capable
_rebuild_render_index()   # index.json is a derived cache; render.json files are the truth

# Documentation site (MkDocs-Material build). Mount BEFORE the frontend catch-all so
# /documentation resolves. Build with: cd documentation && mkdocs build
DOCS_SITE = ROOT.parent / "documentation" / "site"
if DOCS_SITE.exists():
    app.mount("/documentation", StaticFiles(directory=str(DOCS_SITE), html=True), name="documentation")


def _published_docs_url(cfg: Path):
    """Where the documentation is published, read from mkdocs.yml's `site_url`.

    Not hardcoded, and deliberately not parsed with a YAML library: mkdocs is a dev-group
    dependency, so the hub cannot import yaml at runtime. `site_url` is already the single
    declaration of that address — it drives canonical links and sitemap.xml on the Pages
    deploy — and scripts/apply-identity.sh rewrites it per fork. Naming an owner here
    instead would point every fork's hub at somebody else's site.
    """
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^site_url:\s*(\S+)", text, re.MULTILINE)
    if not m:
        return None
    url = m.group(1).strip().strip("'\"")
    # A fork that has not run apply-identity.sh still carries the literal placeholder, and
    # https://GITHUB_USER.github.io/ resolves nowhere. No link beats a dead one.
    return None if "GITHUB_USER" in url else url


DOCS_URL = _published_docs_url(ROOT.parent / "documentation" / "mkdocs.yml")

FRONTEND = ROOT / "frontend" / "dist"


# Browsers request /favicon.ico on their own, whatever the document declares, and a 404
# here gets cached per-origin — so a tab opened before the frontend was built can keep
# showing no icon afterwards. The build ships a real multi-size .ico (Safari does not
# reliably render SVG favicons, so this is the file it actually uses); fall back to the
# SVG only if the raster one is missing from an older build.
#
# Registered BEFORE the "/" mount: Starlette matches in registration order and that mount
# swallows everything beneath it.
@app.get("/favicon.ico", include_in_schema=False)
def _favicon():
    ico = FRONTEND / "favicon.ico"
    if ico.exists():
        return FileResponse(ico, media_type="image/x-icon")
    if (FRONTEND / "favicon.svg").exists():
        return RedirectResponse("/favicon.svg", status_code=308)
    raise HTTPException(404, "no favicon (frontend not built)")


# Shown while frontend/dist has no index.html — i.e. the hub is up but the Dashboard has
# not been built yet, or is being rebuilt underneath us. Self-refreshing, because the whole
# problem it solves is someone staring at a page that will never change on its own.
# Deliberately dependency-free: no /assets, no fonts, nothing that needs the build to exist.
#
# The one link here used to be /docs, the hub's own Swagger UI. Wrong destination twice over:
# Swagger answers "what are the endpoints", not the "how do I get started" question someone
# waiting on a first build is actually asking — and it renders from a CDN bundle, so it shows
# an empty frame whenever the schema or the network is unhappy. It points at the published
# documentation instead. /docs is untouched and still served.
_DOCS_LINK = (f'<p><a href="{DOCS_URL}" style="color:#a49c8e">Documentation</a> — quickstart,'
              ' architecture and the API reference.</p>') if DOCS_URL else ""

_BUILDING_PAGE = """<!doctype html>
<meta charset="utf-8"><title>Building the dashboard…</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font:15px/1.6 ui-sans-serif,-apple-system,system-ui,sans-serif;
         background:#12100e; color:#e8e2d8; }
  .box { text-align:center; max-width:38ch; padding:2rem; }
  .spin { width:34px; height:34px; margin:0 auto 1.4rem;
          border:3px solid #3a352e; border-top-color:#c8a04a; border-radius:50%;
          animation:spin 900ms linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion:reduce) { .spin { animation-duration:3s; } }
  h1 { font-size:1.05rem; font-weight:600; margin:0 0 .5rem; }
  p { margin:.35rem 0; color:#a49c8e; font-size:13px; }
  code { color:#c8a04a; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
</style>
<div class="box">
  <div class="spin"></div>
  <h1>Building the dashboard…</h1>
  <p>The API hub is already running. This page reloads by itself the moment the build
     lands — no need to touch anything.</p>
  <p>Watch it with <code>tail -f .hub.log</code>, or the terminal running <code>./init</code>.</p>
  __DOCS_LINK__
</div>
<script>
  // Poll rather than a blind timer: reload only once real markup is being served, so the
  // spinner never flashes through a half-copied build.
  setInterval(async () => {
    try {
      const r = await fetch("/", { cache: "no-store" });
      if (r.ok) location.reload();
    } catch (e) { /* hub restarting — keep waiting */ }
  }, 2000);
</script>
""".replace("__DOCS_LINK__", _DOCS_LINK)   # .replace, not .format — the CSS and JS above are
                                           # full of literal braces an f-string would swallow

# Created unconditionally so StaticFiles can always mount: whether a frontend exists is
# decided PER REQUEST below, not once at import. It used to be decided here at startup,
# which meant a hub started before the Dashboard was built served a dead placeholder for
# its whole life — the build would finish and the page still never came up without a
# manual restart.
FRONTEND.mkdir(parents=True, exist_ok=True)


@app.get("/", include_in_schema=False)
def _index():
    """The SPA shell, or the building page while it is absent."""
    idx = FRONTEND / "index.html"
    if not idx.exists():
        return HTMLResponse(_BUILDING_PAGE, status_code=503)
    # no-store on the shell only (hashed assets under /assets stay cacheable): a rebuild
    # changes the asset names index.html points at, and a cached shell references the old
    # ones — which 404 after the swap.
    return FileResponse(idx, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


# Registered last: this mount swallows every path not matched above.
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
