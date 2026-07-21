#!/usr/bin/env python3
"""engine/hub.py — typed client for the pipeline hub (the single integration point).

Built strictly against the hub's live `/openapi.json` (fetched first, mirrored here). Every
read and write in AnalysisEngine goes through this client over HTTP — the agent never touches
another project's files (PIPELINE.md "the one principle").

Endpoints used (all confirmed present in /openapi.json):
  GET  /api/platforms                                  health + analyzed counts
  GET  /api/analysis/{p}/pending?<filters>             the analyze queue (min_score, tier, ...)
  GET  /api/reference/{p}/pending                       the reference queue (is_reference items)
  GET  /api/analysis/{p}                                list stored blueprints
  GET  /api/analysis/{p}/{content_id}                   one stored blueprint
  POST /api/analysis/{p}                                write a blueprint (VideoAnalysisIn)
  GET  /api/corpus/{p}/factors | /brief?q=              optional grounding context
  POST /api/insights                                    append a transferable learning
  POST /api/logs                                        curated lifecycle events (§10.1)
  POST /api/evals                                       self-eval / judge results (§10.2)
  GET  /api/config/agent/{agent}                        this agent's hub-stored config (§10.3)
  POST /api/producers/register                          self-register manifest (enables config/secrets)
  GET  /api/config/agent/{agent}/secrets/status         secret STATUS only, never values (§10.4)
  (GET /media/... served for local blueprint media; video_url is hub-relative)
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("ae.hub")

DEFAULT_TIMEOUT = 60


class HubError(RuntimeError):
    """A non-2xx response (or transport failure) from the hub."""


class HubClient:
    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    # ---- low-level ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any | None = None,
        timeout: int | None = None,
        raw: bool = False,
    ):
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:1000]
            raise HubError(f"{method} {path} -> {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise HubError(f"{method} {path} -> transport error: {e.reason}") from e
        if raw:
            return payload
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    # ---- health / discovery ---------------------------------------------------------
    def platforms(self) -> list[dict]:
        return self._request("GET", "/api/platforms")

    def health_ok(self) -> bool:
        try:
            self.platforms()
            return True
        except HubError:
            return False

    def foreign_checkout(self) -> str | None:
        """The hub's own directory, when it belongs to a DIFFERENT checkout than this agent.

        None means "no mismatch to report" — either the hub is ours, or it cannot say (a hub
        older than /api/hub, or one that is simply unreachable). Absence of evidence is not
        evidence of a mismatch, so only a definite mismatch is ever returned.

        Two clones of this project on one machine — one niche each — share nothing but the
        loopback. BACKEND_API is the *only* thing aiming this agent at a hub, and a .env
        copied between clones, or a BACKEND_API exported in the shell, aims it at the other
        one: the proposal is written to that niche's studio, against that niche's corpus,
        and every call returns 200. Nothing else in the system can notice. This can.

        Comparing paths is not a breach of the hub-only rule — nothing is read from the
        sibling, and the hub reports its own location over HTTP. Only the expected value is
        local.
        """
        try:
            info = self._request("GET", "/api/hub", timeout=10) or {}
        except HubError:
            return None
        root = str(info.get("root") or "")
        if not root:
            return None
        # <repo>/<Agent>/engine/hub.py -> <repo>/ReelScraper
        mine = Path(__file__).resolve().parents[2] / "ReelScraper"
        try:
            if Path(root).resolve() == mine.resolve():
                return None
        except OSError:
            return None
        return root

    # ---- work queues ----------------------------------------------------------------
    def pending(self, platform: str, **filters) -> list[dict]:
        """The analyze queue: clips with local media but no schema-2 blueprint, ranked by
        virality. Filters: min_score, tier, min_duration, max_duration, content_type, limit,
        reanalyze=<content_id>, stale=true. Items carry the clip's audio_* fields."""
        return self._request("GET", f"/api/analysis/{platform}/pending", params=filters) or []

    def reference_pending(self, platform: str) -> list[dict]:
        """The reference queue: references with media but no blueprint yet (is_reference:true)."""
        items = self._request("GET", f"/api/reference/{platform}/pending") or []
        for it in items:
            it.setdefault("is_reference", True)
        return items

    # ---- analysis read/write --------------------------------------------------------
    def list_analysis(self, platform: str) -> list[dict]:
        return self._request("GET", f"/api/analysis/{platform}") or []

    def get_analysis(self, platform: str, content_id: str) -> dict | None:
        try:
            return self._request("GET", f"/api/analysis/{platform}/{content_id}")
        except HubError:
            return None

    def post_analysis(self, platform: str, blueprint: dict) -> dict:
        """Write one blueprint. The hub's VideoAnalysisIn accepts schema_version 2 and stamps
        platform + analyzed_at. content_id is required and must match the queue item."""
        return self._request("POST", f"/api/analysis/{platform}", body=blueprint)

    # ---- grounding context (optional, read-only) ------------------------------------
    def factors(self, platform: str) -> Any:
        return self._request("GET", f"/api/corpus/{platform}/factors")

    def brief(self, platform: str, q: str | None = None) -> Any:
        return self._request("GET", f"/api/corpus/{platform}/brief", params={"q": q})

    # ---- shared exchange ------------------------------------------------------------
    def post_insight(self, text: str, platform: str = "shared", kind: str = "finding",
                     tags: list[str] | None = None) -> dict:
        return self._request(
            "POST", "/api/insights",
            body={"text": text, "platform": platform, "kind": kind, "tags": tags or []},
        )

    # ---- §10.1 central logging ------------------------------------------------------
    def post_log(self, agent: str, event: str, level: str = "info", run_id: str | None = None,
                 platform: str | None = None, content_id: str | None = None,
                 msg: str | None = None, data: dict | None = None) -> None:
        """POST a curated LIFECYCLE event (never every debug line). Best-effort — a hub log
        failure must not abort a run, so it is logged locally and swallowed."""
        try:
            self._request("POST", "/api/logs", body={
                "agent": agent, "event": event, "level": level, "run_id": run_id,
                "platform": platform, "content_id": content_id, "msg": msg, "data": data,
            })
        except (HubError, TypeError, ValueError) as e:
            log.debug("central log post failed (non-fatal)", extra={"err": str(e)})

    # ---- §10.2 eval store -----------------------------------------------------------
    def post_eval(self, agent: str, target_type: str, target_id: str, scores: dict,
                  verdict: str | None = None, judge: str | None = None,
                  notes: str | None = None, platform: str | None = None) -> dict:
        return self._request("POST", "/api/evals", body={
            "agent": agent, "target_type": target_type, "target_id": target_id,
            "scores": scores, "verdict": verdict, "judge": judge, "notes": notes,
            "platform": platform,
        })

    # ---- §10.3 config / §10.4 secrets ----------------------------------------------
    def get_agent_config(self, agent: str) -> dict:
        return self._request("GET", f"/api/config/agent/{agent}") or {}

    def register_producer(self, manifest: dict) -> dict:
        """Idempotent upsert by name. Registering makes the config + secrets-status endpoints
        resolve for this agent and surfaces it in the Dashboard roster."""
        return self._request("POST", "/api/producers/register", body=manifest)

    def secrets_status(self, agent: str) -> list[dict]:
        return self._request("GET", f"/api/config/agent/{agent}/secrets/status") or []

    # ---- media ----------------------------------------------------------------------
    def media_url(self, video_url: str) -> str:
        """Resolve a hub-relative media path (e.g. /media/instagram/<id>.mp4) to an absolute URL."""
        if video_url.startswith("http://") or video_url.startswith("https://"):
            return video_url
        return self.base + video_url

    def download_media(self, video_url: str, dest_path: str, timeout: int = 300) -> str:
        """Download the hub's local media for a clip to dest_path. Returns dest_path."""
        url = self.media_url(video_url)
        req = urllib.request.Request(url, headers={"Accept": "video/mp4,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        except urllib.error.URLError as e:
            raise HubError(f"media download {url} failed: {e}") from e
        return dest_path
