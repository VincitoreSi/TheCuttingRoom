#!/usr/bin/env python3
"""
similar-content — Producer SPI self-registration (PIPELINE.md §3, §5, §10).

Run this on startup. It:
  1. POSTs this agent's manifest to POST /api/producers/register (idempotent by name).
  2. Declares config_schema (tunable knobs + defaults, §10.3) and secrets BY NAME only (§10.4).
  3. Self-reports secret resolvability (present = env var set locally) WITHOUT ever sending
     or printing the secret value.
  4. Prints GET /api/config/agent/<name>/secrets/status so misconfig is visible.

Only BACKEND_API + AGENT_NAME come from the environment (bootstrap exception, §10.3);
everything else is declared here / fetched from the hub at run start.

Usage:  python3 register.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

BACKEND_API = os.environ.get("BACKEND_API", "http://127.0.0.1:8787").rstrip("/")
AGENT_NAME = os.environ.get("AGENT_NAME", "similar-content")
HERE = os.path.dirname(os.path.abspath(__file__))


def load_env_names(path):
    """Read .env into a dict for LOCAL presence checks only. Values never leave this process."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def http_json(method, path, payload=None):
    url = BACKEND_API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}
    except urllib.error.URLError as e:
        print(f"[register] hub unreachable at {BACKEND_API}: {e}", file=sys.stderr)
        sys.exit(2)


# ---- secret presence: local check only, by ENV VAR NAME, never value --------------------
dotenv = load_env_names(os.path.join(HERE, ".env"))


def present(env_var):
    return bool(os.environ.get(env_var) or dotenv.get(env_var))


# Derive the ACTIVE image-provider key from image_config.json (single source of truth).
with open(os.path.join(HERE, "image_config.json")) as f:
    img_cfg = json.load(f)
active_provider = img_cfg["active"]
active_key_env = img_cfg["providers"][active_provider].get("api_key_env")

# Declare secrets BY NAME ONLY (§10.4). The active provider's key is required; alternate
# image-provider keys are optional (only needed if you switch `active` in image_config.json).
secrets = []
seen = set()


def add_secret(name, env_var, required):
    if not env_var or env_var in seen:
        return
    seen.add(env_var)
    secrets.append({
        "name": name,
        "env_var": env_var,
        "required": required,
        "present": present(env_var),   # self-reported resolvability; value NEVER sent
    })


add_secret("image_provider_key", active_key_env, True)
for pname, pcfg in img_cfg["providers"].items():
    ev = pcfg.get("api_key_env")
    if ev and ev != active_key_env:
        add_secret(f"image_provider_key_{pname}", ev, False)

# ---- the manifest (PIPELINE.md §5) ------------------------------------------------------
manifest = {
    "name": AGENT_NAME,
    "kind": "clone",
    "consumes": ["corpus", "analysis", "audio", "insights"],
    "human_gate": False,
    "needs_reference": False,
    "produces": "studio_markdown",
    "output_status": "proposed",
    "workflow_stages": ["Queued", "Generating", "Self-eval", "Proposed", "Approved",
                        "Rendering", "Rendered", "Rejected"],
    # How the hub launches a per-item render (POST /api/studio/{p}/{file}/render). The hub
    # hardcodes no producer path — it executes only what a producer declares here, and only
    # if `dir` resolves to a direct sibling of the backend repo.
    "renderable": True,
    "dir": "SimilarContent",
    "render_cmd": ["uv", "run", "cli.py", "render"],
    # How the hub launches `propose` (POST /api/pipeline/{p}/propose, and the cascading
    # heartbeat). A SEPARATE capability from `renderable` on purpose: proposing reads the
    # corpus and writes markdown into the human gate and costs nothing, while rendering
    # spends image credits per frame — so the free, unattended trigger must not be gated on
    # (or grantable by) the paid one. The hub appends the `propose` subcommand itself and
    # will not take it from here, which is what stops a manifest reaching a paid verb.
    "proposes": True,
    "propose_cmd": ["uv", "run", "cli.py"],
    "config_schema": {
        "type": "object",
        "title": "similar-content knobs",
        "properties": {
            "image_provider": {
                "type": "string",
                "default": active_provider,
                "enum": list(img_cfg["providers"].keys()),
                "description": "Active image backend (mirrors image_config.json `active`)."
            },
            "smoke_test_provider": {
                "type": "string", "default": "pollinations",
                "description": "Keyless provider used for a free smoke render before spending credits."
            },
            "top_n": {
                "type": "integer", "default": 5, "minimum": 1, "maximum": 25,
                "description": "How many clone recipes `cli.py propose` publishes per run (the default for --count)."
            },
            "prefer_blueprint": {
                "type": "boolean", "default": True,
                "description": "If a schema_version:2 blueprint exists for the exemplar's content_id, use it as source of truth instead of re-deriving beats."
            },
            # ---- the ease gate and its threshold lifecycle -------------------------------
            # `propose` ranks easy-first; anything that does not clear `ease_threshold` can
            # only arrive as virality backfill. On a corpus of 6-7 shot, ~10s reels the whole
            # pool can sit just under the gate — so the run REPORTS what the gate did (how
            # many cleared, the best score, what to lower it to), and these knobs are how you
            # act on that. Lowering is a HUMAN act; the only change a run may make on its own
            # is putting the threshold BACK (see ease_auto_restore).
            "ease_threshold": {
                "type": "integer", "default": 55, "minimum": 0, "maximum": 100,
                "description": "Ease gate 0-100 (higher = easier to remake). Candidates scoring at or above this rank easy-first; the rest can only appear as backfill. A ~10s, 6-7 shot, fully-static reel scores ~51-52, a 2-shot 7s static clip ~70. A clip whose duration is unknown, or 30s or longer, is never 'easy' at any setting — shots + static alone total 65, so without that rule a 90s single-take would outrank every real candidate. If a run reports 0 of N cleared, lower this to the score it names."
            },
            # `type: integer` with a null default is deliberate: the Agent Desk renders an
            # integer as a number input, which shows blank for null and yields null again
            # when cleared — exactly the int-or-nothing behaviour this knob needs. Declaring
            # `["integer", "null"]` instead drops it to a free-text box (see
            # Dashboard/src/components/agent/AgentConfigForm.tsx), which is worse for a knob
            # whose whole content is a number, and the Dashboard is out of scope here.
            "ease_restore_to": {
                "type": "integer", "default": None, "minimum": 0, "maximum": 100,
                "description": "Where a lowered ease_threshold came from, so it can be put back (blank = nothing to restore). Recorded once, when the threshold is below the default and nothing is recorded yet, and cleared only by a restore that actually happened — a value you set here is never rewritten by a run. Automation may only ever RAISE the threshold to this value — lowering is a human act, because a wrong restore is visible (fewer easy picks) while a wrong lowering silently degrades every proposal."
            },
            "ease_auto_restore": {
                "type": "boolean", "default": False,
                "description": "When more than `top_n` candidates in the pool clear `ease_restore_to`, put ease_threshold back to it automatically. Off by default: the run reports that the corpus now supports the original threshold and changes nothing, so the first move is always yours. Even on, it only ever acts on a full scheduled run — never on one narrowed by --count/--top/--topic, and never in the same run that first recorded the target — and it abandons the write if ease_threshold changed in the hub while the run was scoring."
            },
            "backfill_order": {
                "type": "string", "default": "virality", "enum": ["virality", "ease"],
                "description": "How the remainder is ordered when too few candidates clear the ease gate. virality = the proven winners first (default). ease = the least-bad-to-remake first, when production cost matters more than the score."
            },
            "fidelity_score_threshold": {
                "type": "integer", "default": 85, "minimum": 0, "maximum": 100,
                "description": "Self-eval gate: clone fidelity to the blueprint must meet this before publish (§10.2)."
            },
            "reuse_public_audio_only": {
                "type": "boolean", "default": True,
                "description": "Only reuse public/reusable original audio; otherwise substitute the nearest public equivalent and flag it."
            },
            "aspect_ratio": {
                "type": "string", "default": "9:16", "enum": ["9:16", "4:5", "1:1"],
                "description": "Output canvas. 9:16 (1080x1920) is the reels/shorts/tiktok format and the only one that fills a phone full-bleed — keep it unless you are deliberately producing feed content. 4:5 = 1080x1350 (IG feed portrait), 1:1 = 1080x1080."
            },
            "video_fit": {
                "type": "string", "default": "auto", "enum": ["auto", "cover", "contain"],
                "description": "How a generated frame meets the canvas. auto = crop when the frame is within 10% of the canvas aspect (Nano Banana's 768x1344 is 1.6% off, so no bars), letterbox when it is further out (its square 1024x1024 fallback would lose 44% of the width and cut the hook text). cover = always crop. contain = always letterbox. None of them ever stretch."
            },
            "render_steps": {
                "type": "integer", "default": 30, "minimum": 10, "maximum": 50,
                "description": "Diffusion steps. FLUX / NVIDIA-NIM ONLY — ignored by nano_banana, which is a text-to-image LLM with no step count."
            },
            "render_seed": {
                "type": "integer", "default": 0,
                "description": "Fixed seed for subject continuity. FLUX / NVIDIA-NIM ONLY — nano_banana has no seed; it holds continuity by anchoring every frame to the first generated image instead."
            },
            "max_frames_per_clone": {"type": "integer", "default": 12, "minimum": 1, "maximum": 40},
            "caption_model": {
                "type": "string", "default": "gemini-2.5-flash",
                "description": "Text model that writes the Instagram caption at render time."
            },
            "caption_temperature": {"type": "number", "default": 0.8, "minimum": 0, "maximum": 2},
            # NB: there is deliberately no video_width/video_height knob. The canvas is
            # derived from aspect_ratio, so the output can never be a size that disagrees
            # with the aspect it claims to be.
            "video_fps": {"type": "integer", "default": 30, "minimum": 12, "maximum": 60},
            "frame_min_hold_s": {
                "type": "number", "default": 0.6, "minimum": 0.1,
                "description": "Shortest a single frame may be held when fitting shots to the source duration."
            },
            "image_retries": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6},
            "pace_seconds": {
                "type": "number", "default": 2.0, "minimum": 0,
                "description": "Minimum gap between image-API calls (rate-limit courtesy)."
            }
        }
    },
    "secrets": secrets,
}

if __name__ == "__main__":
    status, body = http_json("POST", "/api/producers/register", manifest)
    ok = status in (200, 201)
    print(f"[register] POST /api/producers/register -> HTTP {status} "
          f"({'ok' if ok else 'FAILED'})")
    if not ok:
        print(json.dumps(body, indent=2))
        sys.exit(1)

    # Confirm roster entry
    _, roster = http_json("GET", "/api/producers")
    mine = next((p for p in (roster or []) if p.get("name") == AGENT_NAME), None)
    print(f"[register] roster now lists: name={mine and mine.get('name')} "
          f"kind={mine and mine.get('kind')} "
          f"consumes={mine and mine.get('consumes')} "
          f"output_status={mine and mine.get('output_status')}")

    # Secret status — NAMES + present/absent only, never values
    _, sec = http_json("GET", f"/api/config/agent/{AGENT_NAME}/secrets/status")
    print("[register] secret status (name -> present, value NEVER shown):")
    for s in (sec or []):
        print(f"    - {s.get('name')} [{s.get('env_var')}] "
              f"required={s.get('required')} present={s.get('present')}")
