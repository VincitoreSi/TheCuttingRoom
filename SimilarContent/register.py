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
