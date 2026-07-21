#!/usr/bin/env python3
"""engine/hub.py — typed client for the pipeline hub (the single integration point).

Built strictly against the hub's live `/openapi.json` (ReelScraper `api/app.py`, PIPELINE.md
§11.2 "discovery contract"). Every read and write AutoSearch does goes through this client
over HTTP — the agent never touches ReelScraper's files directly (the prime directive, §0).

Endpoints used (all confirmed present in /openapi.json and in ReelScraper's api/app.py):
  GET  /api/platforms                                   health check
  GET  /api/config/{p}                                  platform niche/keywords + pages.txt lines
  GET  /api/corpus/{p}/factors | /brief?q=               optional grounding context
  GET  /api/insights            POST /api/insights       shared cross-agent exchange (§10.1)
  POST /api/logs                                         curated lifecycle events (§10.1)
  GET  /api/config/agent/{agent}                         this agent's hub-stored config (§10.3)
  POST /api/producers/register                          self-register manifest (§3/§10.3/§10.4)
  GET  /api/producers | /api/producers/{name}            the producer roster
  GET  /api/config/agent/{agent}/secrets/status          secret STATUS only, never values (§10.4)
  POST /api/discovery/{p}                                ingest/upsert one creator candidate
  GET  /api/discovery/{p}[?status=]                       list candidates (derived in_pages)
  GET  /api/discovery/{p}/pending                         the human review queue
  POST /api/discovery/{p}/{candidate_id}/status           the gate: approve -> pages.txt append
  GET  /api/agents/{name}/board?platform=&limit_runs=     this agent's live workflow board
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger("as.hub")

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
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    # ---- health -----------------------------------------------------------------------
    def platforms(self) -> list[dict]:
        return self._request("GET", "/api/platforms")

    def health_ok(self) -> bool:
        try:
            self.platforms()
            return True
        except HubError:
            return False

    # ---- platform config (niche + discovery keywords + pages.txt) ---------------------
    def platform_config(self, platform: str) -> dict:
        """{"config": <niche_config.json>, "pages": [...]}."""
        return self._request("GET", f"/api/config/{platform}") or {}

    # ---- grounding context (optional, read-only) ---------------------------------------
    def factors(self, platform: str) -> Any:
        return self._request("GET", f"/api/corpus/{platform}/factors")

    def brief(self, platform: str, q: str | None = None) -> Any:
        return self._request("GET", f"/api/corpus/{platform}/brief", params={"q": q})

    # ---- shared exchange ----------------------------------------------------------------
    def list_insights(self) -> list[dict]:
        return self._request("GET", "/api/insights") or []

    def post_insight(self, text: str, platform: str = "shared", kind: str = "finding",
                     tags: list[str] | None = None) -> dict:
        return self._request(
            "POST", "/api/insights",
            body={"text": text, "platform": platform, "kind": kind, "tags": tags or []},
        )

    # ---- §10.1 central logging -----------------------------------------------------------
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

    # ---- §10.3 config / §10.4 secrets / registration -------------------------------------
    def get_agent_config(self, agent: str) -> dict:
        return self._request("GET", f"/api/config/agent/{agent}") or {}

    def register_producer(self, manifest: dict) -> dict:
        """Idempotent upsert by name. Registering makes the config + secrets-status endpoints
        resolve for this agent and surfaces it in the Dashboard roster (kind=discovery)."""
        return self._request("POST", "/api/producers/register", body=manifest)

    def secrets_status(self, agent: str) -> list[dict]:
        return self._request("GET", f"/api/config/agent/{agent}/secrets/status") or []

    def list_producers(self) -> list[dict]:
        return self._request("GET", "/api/producers") or []

    def get_producer(self, name: str) -> dict | None:
        try:
            return self._request("GET", f"/api/producers/{name}")
        except HubError:
            return None

    # ---- discovery / candidate ingestion (PIPELINE.md §11.2) ------------------------------
    def post_candidate(self, platform: str, candidate: dict) -> dict:
        """Ingest/upsert one creator candidate. `handle` MUST be the pages.txt-matching form
        (full `https://www.instagram.com/<handle>` URL) — the hub appends it verbatim on
        approval. Status is hub-managed: forced to 'pending' on first insert, never silently
        un-gated on re-ingest."""
        return self._request("POST", f"/api/discovery/{platform}", body=candidate)

    def list_candidates(self, platform: str, status: str | None = None) -> list[dict]:
        return self._request("GET", f"/api/discovery/{platform}", params={"status": status}) or []

    def pending_candidates(self, platform: str) -> list[dict]:
        return self._request("GET", f"/api/discovery/{platform}/pending") or []

    def set_candidate_status(self, platform: str, candidate_id: str, status: str,
                             note: str | None = None) -> dict:
        return self._request(
            "POST", f"/api/discovery/{platform}/{candidate_id}/status",
            body={"status": status, "note": note},
        )

    # ---- agent workflow board -------------------------------------------------------------
    def board(self, name: str, platform: str | None = None, limit_runs: int | None = None) -> dict:
        return self._request(
            "GET", f"/api/agents/{name}/board",
            params={"platform": platform, "limit_runs": limit_runs},
        ) or {}
