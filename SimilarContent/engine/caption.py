#!/usr/bin/env python3
"""engine/caption.py — write the Instagram caption for a rendered clone.

Instagram has no post API for this pipeline, so the operator posts by hand. The caption is
therefore a deliverable in its own right: it ships with the render and gets copied straight
into the composer.

The prompt mirrors the SOURCE reel's voice, length, emoji density and hashtag band rather
than inventing a house style — the clone rides a proven format, and the caption is part of
that format. It is explicitly told not to restate the text already burned into the frames,
which is the most common way an auto-written caption reads as filler.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger("sc.caption")

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM = (
    "You write Instagram Reel captions for short-form fashion/lifestyle content.\n"
    "Mirror the SOURCE caption's voice, approximate length, emoji density, line-break "
    "style and hashtag count as closely as you can — but never reuse its wording.\n"
    "Do not restate text that is already burned into the video frames; the caption should "
    "add the hook or the call to action, not repeat what the viewer can read on screen.\n"
    "Return STRICT JSON with keys: caption (string, hashtags NOT included), "
    "hashtags (array of strings, each starting with #), alt_captions (array of 2 strings)."
)


class CaptionError(RuntimeError):
    pass


class GeminiTextClient:
    """A trimmed Gemini text client — JSON mode only, no File API.

    Separate from NanoBananaClient because it targets a different model and response
    modality; sharing one class would mean a flag that changes almost everything.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", timeout: int = 120):
        if not api_key:
            raise CaptionError("GEMINI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate_json(self, system_instruction: str, user_text: str,
                      temperature: float = 0.8, max_output_tokens: int = 2048) -> dict:
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_output_tokens,
                                 "responseMimeType": "application/json"},
        }).encode()
        url = f"{_BASE}/{self.model}:generateContent?key={self.api_key}"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                doc = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise CaptionError(
                f"caption model failed {e.code}: "
                f"{e.read().decode('utf-8', 'replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise CaptionError(f"transport error calling the caption model: {e.reason}") from e

        candidates = doc.get("candidates") or []
        if not candidates:
            raise CaptionError("caption model returned no candidates")
        text = "".join(p.get("text", "")
                       for p in (candidates[0].get("content") or {}).get("parts") or [])
        return _loads_lenient(text)


def _loads_lenient(text: str) -> dict:
    """Parse the model's JSON, tolerating a ```json fence if one slips through."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        out = json.loads(text)
    except ValueError as e:
        raise CaptionError(f"caption model did not return JSON: {text[:200]}") from e
    if not isinstance(out, dict):
        raise CaptionError("caption model returned JSON that is not an object")
    return out


def build_prompt(plan, source_caption: str | None,
                 virality_formula: dict | None) -> str:
    """Assemble the caption brief from everything we know about the clone."""
    vf = virality_formula or {}
    on_screen = [s.on_screen_text for s in plan.shots if s.on_screen_text]
    blocks = [
        f"CLONE TITLE: {plan.title}",
        f"SHOT COUNT: {len(plan.shots)}   TARGET DURATION: {plan.target_duration_s}s",
    ]
    if source_caption:
        blocks.append(f"SOURCE CAPTION (mirror its voice, do not reuse its words):\n{source_caption}")
    if vf.get("hook"):
        blocks.append(f"HOOK: {vf['hook']}")
    if vf.get("cta"):
        blocks.append(f"CALL TO ACTION: {vf['cta']}")
    if vf.get("tags"):
        blocks.append(f"SOURCE TAGS: {', '.join(str(t) for t in vf['tags'][:20])}")
    if plan.replicable_formula:
        blocks.append(f"FORMULA: {plan.replicable_formula}")
    if on_screen:
        blocks.append("TEXT ALREADY ON SCREEN (do not repeat it):\n- "
                      + "\n- ".join(on_screen))
    return "\n\n".join(blocks)


def generate_caption(client: GeminiTextClient, plan, source_caption: str | None = None,
                     virality_formula: dict | None = None,
                     temperature: float = 0.8) -> dict:
    """-> {"caption": str, "hashtags": [str], "alt_captions": [str]}"""
    out = client.generate_json(SYSTEM, build_prompt(plan, source_caption, virality_formula),
                               temperature=temperature)
    caption = str(out.get("caption") or "").strip()
    if not caption:
        raise CaptionError("caption model returned an empty caption")
    tags = [str(t).strip() for t in (out.get("hashtags") or []) if str(t).strip()]
    tags = [t if t.startswith("#") else f"#{t}" for t in tags]
    return {"caption": caption, "hashtags": tags,
            "alt_captions": [str(a).strip() for a in (out.get("alt_captions") or [])
                             if str(a).strip()][:2]}
