#!/usr/bin/env python3
"""engine/nanobanana.py — image generation via Gemini 2.5 Flash Image ("Nano Banana").

Hand-rolled urllib, mirroring AnalysisEngine/engine/gemini.py — no SDK, no dependency.

TWO THINGS THIS MODEL DOES NOT HAVE, which the recipe's knobs imply it does:

  * **No seed.** `render_seed` is a FLUX/NVIDIA-NIM parameter. Reproducibility and
    cross-frame subject consistency cannot come from pinning a seed here.
  * **No negative_prompt field.** It is a text-to-image LLM, not a diffusion endpoint with
    a conditioning slot, so negatives are folded into the prompt as an AVOID clause
    (engine/recipe.compose_frame_prompt).

Consistency instead comes from IMAGE ANCHORING, which is what this model is actually good
at: frame 0 is generated from text alone, then every later frame is generated with frame 0
attached as a reference image and an instruction to hold the subject, wardrobe and setting
fixed. That is what makes six shots read as the same person in the same room.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("sc.image")

_DEFAULT_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
                     "gemini-2.5-flash-image:generateContent")
_RETRY_WAITS = (2, 6, 18)          # transient 429/5xx backoff

ANCHOR_INSTRUCTION = (
    "Use the attached reference image as the visual anchor. The person's face, hair, build "
    "and skin tone, the wardrobe styling, the room, and the lighting must be IDENTICAL to "
    "the reference. Change only what the description below specifies."
)


class ImageError(RuntimeError):
    pass


class NanoBananaClient:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-image",
                 endpoint: str | None = None, timeout: int = 180,
                 retries: int = 3):
        if not api_key:
            raise ImageError("GEMINI_API_KEY is not set (see SimilarContent/.env.example)")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint or _DEFAULT_ENDPOINT
        self.timeout = timeout
        self.retries = max(1, retries)

    # ---- low-level HTTP -------------------------------------------------------------
    def _req(self, method: str, url: str, data: bytes | None = None,
             headers: dict | None = None, timeout: int | None = None):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            raise ImageError(f"transport error calling the image API: {e.reason}") from e

    # ---- generation -----------------------------------------------------------------
    def generate_image(self, prompt: str,
                       ref_images: list[tuple[bytes, str]] | None = None,
                       aspect_ratio: str = "9:16") -> tuple[bytes, str]:
        """Render one frame. Returns `(image_bytes, mime_type)`.

        `ref_images` are `(bytes, mime)` anchors sent ahead of the prompt — see the module
        docstring on why consistency depends on them.
        """
        parts: list[dict] = []
        if ref_images:
            for raw, mime in ref_images:
                parts.append({"inline_data": {
                    "mime_type": mime or "image/png",
                    "data": base64.b64encode(raw).decode()}})
            parts.append({"text": ANCHOR_INSTRUCTION})
        parts.append({"text": prompt})

        body = json.dumps({
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"],
                                 "imageConfig": {"aspectRatio": aspect_ratio}},
        }).encode()
        url = f"{self.endpoint}?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        last = ""
        for attempt in range(self.retries):
            status, raw = self._req("POST", url, data=body, headers=headers)
            if status < 300:
                return self._extract_image(raw)
            last = raw.decode("utf-8", "replace")[:400]
            # 429 = quota/rate limit, 5xx = transient upstream. Anything else (400 bad
            # prompt, 403 bad key) will not improve on retry, so fail immediately.
            if status != 429 and status < 500:
                raise ImageError(f"image generation failed {status}: {last}")
            if attempt < self.retries - 1:
                wait = _RETRY_WAITS[min(attempt, len(_RETRY_WAITS) - 1)]
                log.warning("image API %s, retrying in %ss", status, wait)
                time.sleep(wait)
        raise ImageError(f"image generation failed after {self.retries} attempts: {last}")

    @staticmethod
    def _extract_image(raw: bytes) -> tuple[bytes, str]:
        """Walk candidates[0].content.parts[] for the part carrying inline image data.

        The model may return a text part alongside (or instead of) the image — a refusal,
        or commentary — so the absence of an image part is reported with whatever text came
        back, which is far more useful than a KeyError.
        """
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ImageError(f"image API returned non-JSON: {raw[:200]!r}") from e

        candidates = doc.get("candidates") or []
        if not candidates:
            fb = doc.get("promptFeedback") or doc.get("error") or {}
            raise ImageError(f"image API returned no candidates: {json.dumps(fb)[:300]}")

        texts = []
        for part in (candidates[0].get("content") or {}).get("parts") or []:
            blob = part.get("inline_data") or part.get("inlineData")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"]), blob.get("mime_type") or \
                    blob.get("mimeType") or "image/png"
            if part.get("text"):
                texts.append(part["text"])
        reason = candidates[0].get("finishReason") or "no image part"
        raise ImageError(f"no image in response ({reason}): {' '.join(texts)[:300]}")
