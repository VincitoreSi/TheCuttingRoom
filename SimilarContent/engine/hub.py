#!/usr/bin/env python3
"""engine/hub.py — typed client for the pipeline hub, this agent's ONLY integration point.

Every read and write goes over HTTP (PIPELINE.md §3, "the one principle"): this agent never
touches the backend repo's files, and the backend never reaches into this directory except
to launch `cli.py render`.

Endpoints used:
  GET  /api/platforms                              health
  GET  /api/corpus/{p}/top?n=                      ranked exemplars to propose from
  GET  /api/corpus/{p}/search?q=&k=                topic-focused exemplars
  GET  /api/studio/{p}?status=&agent=              the approved queue
  GET  /api/studio/{p}/{file}                      one item
  POST /api/studio/{p}                             publish a proposal (the human gate)
  GET  /api/content/{p}                            source caption + full content_id
  GET  /api/analysis/{p}                           the shortcode -> content_id index
  GET  /api/analysis/{p}/{content_id}              the blueprint (virality_formula)
  GET  /api/renders/{p}?file=                      what has already been rendered
  POST /api/renders/{p}                            upload the rendered reel (§ render store)
  POST /api/producers/register                     self-registration
  GET  /api/config/agent/{agent}                   tunable knobs (§10.3)
  POST /api/logs | /api/evals | /api/insights      lifecycle, self-eval, learnings
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("sc.hub")

DEFAULT_TIMEOUT = 60


class HubError(RuntimeError):
    """A non-2xx response (or transport failure) from the hub."""


class HubClient:
    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    # ---- low-level ------------------------------------------------------------------
    def _request(self, method: str, path: str, params: dict | None = None,
                 body: Any | None = None, timeout: int | None = None):
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
                return json.loads(payload.decode("utf-8")) if payload else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise HubError(f"{method} {path} -> {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise HubError(f"{method} {path} -> transport error: {e.reason}") from e

    def health_ok(self) -> bool:
        try:
            self._request("GET", "/api/platforms", timeout=10)
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

    # ---- reads ----------------------------------------------------------------------
    def corpus_top(self, platform: str, n: int = 15) -> list[dict]:
        """Top-N viral exemplars, already ranked by virality_score desc."""
        return self._request("GET", f"/api/corpus/{platform}/top", params={"n": n}) or []

    def corpus_search(self, platform: str, q: str, k: int = 15) -> list[dict]:
        """Closest exemplars to a topic. Rows are thinner than `top` (no duration/audio)."""
        return self._request("GET", f"/api/corpus/{platform}/search",
                             params={"q": q, "k": k}) or []

    def analysis_list(self, platform: str) -> list[dict]:
        """Every saved blueprint's header — carries BOTH `content_id` and `url`, which is
        the only way to join a corpus `top` row (url, no content_id) to its blueprint."""
        return self._request("GET", f"/api/analysis/{platform}") or []

    def studio(self, platform: str, status: str | None = None,
               agent: str | None = None) -> list[dict]:
        return self._request("GET", f"/api/studio/{platform}",
                             params={"status": status, "agent": agent}) or []

    def studio_item(self, platform: str, file: str) -> dict:
        return self._request("GET", f"/api/studio/{platform}/{urllib.parse.quote(file)}")

    def content(self, platform: str) -> list[dict]:
        return self._request("GET", f"/api/content/{platform}") or []

    def blueprint(self, platform: str, content_id: str) -> dict | None:
        try:
            return self._request("GET", f"/api/analysis/{platform}/{content_id}")
        except HubError:
            return None          # a missing blueprint is an expected empty state

    def renders(self, platform: str, file: str | None = None) -> list[dict]:
        return self._request("GET", f"/api/renders/{platform}",
                             params={"file": file}) or []

    def agent_config(self, agent: str) -> dict:
        got = self._request("GET", f"/api/config/agent/{agent}") or {}
        merged = dict(got.get("defaults") or {})
        merged.update(got.get("config") or {})
        return merged

    # ---- writes ---------------------------------------------------------------------
    def register_producer(self, manifest: dict) -> dict:
        return self._request("POST", "/api/producers/register", body=manifest)

    def post_studio(self, platform: str, filename: str, text: str, *,
                    agent: str, kind: str, status: str | None = None) -> dict:
        """Publish a proposal into the human gate.

        `status` is OMITTED by default, and that is deliberate. The hub keeps an existing
        item's gate state unless the body names one explicitly (ReelScraper/api/app.py
        ::save_proposal), so a re-proposal of a filename a human already approved stays
        approved — whereas sending `status:"proposed"` would silently un-approve it. A first
        insert still lands as `proposed`, which is what the manifest declares.
        """
        body = {"filename": filename, "text": text, "agent": agent, "kind": kind}
        if status is not None:
            body["status"] = status
        return self._request("POST", f"/api/studio/{platform}", body=body)

    def post_render(self, platform: str, record: dict, assets: list[Path],
                    timeout: int = 300) -> dict:
        """Upload a rendered reel and its metadata.

        Assets go up base64-encoded in the JSON body. That is deliberate: the hub has no
        `python-multipart` dependency, and both sides speak plain stdlib urllib — so this
        stays dependency-free end to end. Fine for the few MB a slideshow weighs; a future
        video-generation agent producing 40MB clips will want a raw-body endpoint instead.
        """
        payload = dict(record)
        payload["assets"] = [
            {"name": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode()}
            for p in assets if p.exists()
        ]
        return self._request("POST", f"/api/renders/{platform}", body=payload,
                             timeout=timeout)

    def post_log(self, agent: str, event: str, *, run_id: str, platform: str,
                 level: str = "info", content_id: str | None = None,
                 msg: str = "", data: dict | None = None) -> None:
        """Lifecycle events only (§10.1). Never fatal — losing a log line must not kill
        a render that is otherwise succeeding."""
        try:
            self._request("POST", "/api/logs", body={
                "agent": agent, "run_id": run_id, "platform": platform, "level": level,
                "event": event, "content_id": content_id, "msg": msg,
                "data": data or {}, "ts": time.time()}, timeout=10)
        except HubError as e:
            log.debug("log post failed (ignored)", extra={"err": str(e)})

    def post_eval(self, body: dict) -> None:
        try:
            self._request("POST", "/api/evals", body=body, timeout=20)
        except HubError as e:
            log.warning("eval post failed", extra={"err": str(e)})

    def post_insight(self, body: dict) -> None:
        try:
            self._request("POST", "/api/insights", body=body, timeout=20)
        except HubError as e:
            log.warning("insight post failed", extra={"err": str(e)})
