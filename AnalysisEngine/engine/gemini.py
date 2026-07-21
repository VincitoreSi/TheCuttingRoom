#!/usr/bin/env python3
"""engine/gemini.py — Gemini REST client (File API + generateContent), stdlib urllib only.

No heavy SDK (companion D1: "Gemini REST API via urllib/httpx — no heavy SDK"). Model
`gemini-2.5-pro` for both analysis and the judge pass.

DEFECT FIX (companion "Defects to fix" → hardcoded, expiring FILE_URI): the scratch pinned a
single `FILE_URI` that expires (~48h). Here we upload the clip FRESH every run via the File
API's resumable-upload protocol, poll until the file is ACTIVE, use the returned URI, and
delete it afterwards. `generate_json()` detects an expired/missing-file error and lets the
caller re-upload — a URI is never hardcoded or cached across runs.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("ae.gemini")

_BASE = "https://generativelanguage.googleapis.com"


class GeminiError(RuntimeError):
    pass


class GeminiFileExpired(GeminiError):
    """The referenced File API URI is gone/expired — the caller should re-upload."""


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.5-pro", timeout: int = 600):
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    # ---- low-level HTTP -------------------------------------------------------------
    def _req(self, method: str, url: str, data: bytes | None = None,
             headers: dict | None = None, timeout: int | None = None):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            return e.code, dict(e.headers or {}), body.encode("utf-8")
        except urllib.error.URLError as e:
            raise GeminiError(f"transport error calling Gemini: {e.reason}") from e

    # ---- File API -------------------------------------------------------------------
    def upload_file(self, path: str, mime_type: str = "video/mp4",
                    display_name: str | None = None) -> dict:
        """Upload a local file via the resumable File API. Returns the file resource
        {name, uri, state, ...}. Always a FRESH upload — never a cached URI."""
        p = Path(path)
        size = p.stat().st_size
        display_name = display_name or p.name

        # 1) start a resumable session — the upload URL comes back in a response header.
        start_headers = {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        }
        start_body = json.dumps({"file": {"display_name": display_name}}).encode()
        status, headers, body = self._req(
            "POST", f"{_BASE}/upload/v1beta/files?key={self.api_key}",
            data=start_body, headers=start_headers, timeout=60,
        )
        if status >= 300:
            raise GeminiError(f"file upload start failed {status}: {body.decode('utf-8','replace')[:500]}")
        upload_url = headers.get("X-Goog-Upload-URL") or headers.get("x-goog-upload-url")
        if not upload_url:
            raise GeminiError("no X-Goog-Upload-URL returned from resumable start")

        # 2) upload the bytes and finalize.
        up_headers = {
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }
        status, _h, body = self._req("POST", upload_url, data=p.read_bytes(),
                                     headers=up_headers, timeout=self.timeout)
        if status >= 300:
            raise GeminiError(f"file upload finalize failed {status}: {body.decode('utf-8','replace')[:500]}")
        info = json.loads(body.decode("utf-8")).get("file", {})
        log.info("uploaded to File API", extra={"file_name": info.get("name"), "state": info.get("state")})
        return info

    def wait_active(self, file_name: str, poll_s: float = 3.0, max_wait_s: float = 300.0) -> dict:
        """Poll GET files/{name} until state == ACTIVE (video processing). Raises on FAILED."""
        deadline = time.monotonic() + max_wait_s
        name = file_name.split("/")[-1]
        while True:
            status, _h, body = self._req(
                "GET", f"{_BASE}/v1beta/files/{name}?key={self.api_key}", timeout=30)
            if status >= 300:
                raise GeminiError(f"file poll failed {status}: {body.decode('utf-8','replace')[:300]}")
            info = json.loads(body.decode("utf-8"))
            state = info.get("state")
            if state == "ACTIVE":
                return info
            if state == "FAILED":
                raise GeminiError(f"File API processing FAILED for {name}")
            if time.monotonic() > deadline:
                raise GeminiError(f"File API file {name} not ACTIVE after {max_wait_s}s (state={state})")
            time.sleep(poll_s)

    def delete_file(self, file_name: str) -> None:
        """Clean up an uploaded file. Best-effort."""
        name = file_name.split("/")[-1]
        try:
            self._req("DELETE", f"{_BASE}/v1beta/files/{name}?key={self.api_key}", timeout=30)
        except GeminiError as e:
            log.debug("file delete failed (non-fatal)", extra={"err": str(e)})

    # ---- generation -----------------------------------------------------------------
    def generate_json(self, system_instruction: str, user_text: str,
                      file_uri: str | None = None, mime_type: str = "video/mp4",
                      temperature: float = 0.4, max_output_tokens: int = 64000) -> dict:
        """Call generateContent in JSON mode. Returns the parsed dict. Raises GeminiFileExpired
        if the response indicates the file_uri is gone (so the caller re-uploads)."""
        parts: list[dict] = []
        if file_uri:
            parts.append({"file_data": {"mime_type": mime_type, "file_uri": file_uri}})
        parts.append({"text": user_text})
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        url = f"{_BASE}/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        status, _h, body = self._req(
            "POST", url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, timeout=self.timeout)
        text = body.decode("utf-8", "replace")
        if status >= 300:
            lower = text.lower()
            if file_uri and ("not found" in lower or "permission" in lower
                             or "expired" in lower or status in (403, 404)):
                raise GeminiFileExpired(f"file uri may be expired ({status}): {text[:300]}")
            raise GeminiError(f"generateContent {status}: {text[:800]}")

        data = json.loads(text)
        cands = data.get("candidates") or []
        if not cands:
            raise GeminiError(f"no candidates returned: {text[:500]}")
        cand = cands[0]
        finish = cand.get("finishReason")
        out = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        usage = data.get("usageMetadata", {})
        log.info("generateContent done", extra={"finish": finish, "chars": len(out),
                                                "tokens": usage.get("totalTokenCount")})
        if not out.strip():
            raise GeminiError(f"empty text in candidate (finishReason={finish})")
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as e:
            # Truncation (MAX_TOKENS) or stray fences — surface for the repair pass.
            raise GeminiError(
                f"model returned non-JSON (finishReason={finish}, {e}); first 200 chars: {out[:200]}"
            ) from e
        parsed["_finish_reason"] = finish
        return parsed
