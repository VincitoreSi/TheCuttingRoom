#!/usr/bin/env python3
"""engine/schema.py — jsonschema validators for the candidate payload + the two Claude
output schemas (PIPELINE.md §5, AutoSearch/PIPELINE.md §5/§6/§7).

Three documents are validated here:
  * CANDIDATE_SCHEMA        — the payload AutoSearch POSTs to `/api/discovery/{p}` (the hub's
                              `CandidateIn`). `handle` MUST be the pages.txt-matching full URL
                              form (`https://www.instagram.com/<handle>`), never a bare username.
  * KEYWORD_EXPANSION_SCHEMA — Claude term-expansion output (§5, 1 call/run):
                              {keywords[], hashtags[], audio_terms[]}.
  * RELEVANCE_SCORE_SCHEMA   — Claude relevance-scoring output (§5, batched):
                              {scores:[{handle, score, reasons[]}]}.

`candidate_id()` mirrors the hub's own stable-hash algorithm exactly
(`cand_<sha1(f"{platform}:{handle}")[:10]>`) so AutoSearch can compute the SAME id the hub
would derive, and use it consistently as both the POST body's `candidate_id` and the
`content_id` on every lifecycle log — that identity is what lets the hub's `agent_board`
gate-join (keyed on content_id == candidate_id) work.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import jsonschema

_HANDLE_RE = re.compile(r"^https://www\.instagram\.com/[A-Za-z0-9_.]{1,40}/?$")

CANDIDATE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AutoSearch discovery candidate payload (POST /api/discovery/{platform})",
    "type": "object",
    "additionalProperties": True,
    "required": ["handle"],
    "properties": {
        "candidate_id": {"type": ["string", "null"]},
        "handle": {"type": "string", "pattern": _HANDLE_RE.pattern},
        "platform": {"type": ["string", "null"]},
        "source_term": {"type": ["string", "null"]},
        "discovered_via": {"type": ["string", "null"]},
        "followers": {"type": ["integer", "null"], "minimum": 0},
        "median_plays": {"type": ["number", "null"], "minimum": 0},
        "sample_reels": {"type": "array", "items": {"type": "string"}},
        "relevance": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "reasons": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

KEYWORD_EXPANSION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AutoSearch term-expansion output",
    "type": "object",
    "additionalProperties": False,
    "required": ["keywords", "hashtags", "audio_terms"],
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "audio_terms": {"type": "array", "items": {"type": "string"}},
    },
}

RELEVANCE_SCORE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AutoSearch relevance-scoring output",
    "type": "object",
    "additionalProperties": False,
    "required": ["scores"],
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["handle", "score", "reasons"],
                "properties": {
                    "handle": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

_CANDIDATE_VALIDATOR = jsonschema.Draft202012Validator(CANDIDATE_SCHEMA)
_KEYWORD_VALIDATOR = jsonschema.Draft202012Validator(KEYWORD_EXPANSION_SCHEMA)
_RELEVANCE_VALIDATOR = jsonschema.Draft202012Validator(RELEVANCE_SCORE_SCHEMA)


def _errors(validator: jsonschema.Draft202012Validator, doc: Any) -> list[str]:
    out = []
    for e in sorted(validator.iter_errors(doc), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        out.append(f"[schema] {loc}: {e.message}")
    return out


def validate_candidate(doc: dict) -> list[str]:
    return _errors(_CANDIDATE_VALIDATOR, doc)


def validate_keyword_expansion(doc: dict) -> list[str]:
    return _errors(_KEYWORD_VALIDATOR, doc)


def validate_relevance_scores(doc: dict) -> list[str]:
    return _errors(_RELEVANCE_VALIDATOR, doc)


def candidate_id(platform: str, handle: str) -> str:
    """The SAME stable hash the hub derives when `candidate_id` is omitted
    (`ReelScraper/api/app.py::add_candidate`) — computed client-side so item lifecycle
    logs (`content_id=`) always match the hub's candidate_id, which the discovery gate-join
    in `agent_board` depends on."""
    return "cand_" + hashlib.sha1(f"{platform}:{handle}".encode("utf-8")).hexdigest()[:10]


def to_pages_handle(username: str) -> str:
    """Bare username -> the pages.txt-matching full URL form the hub expects in `handle`."""
    username = username.strip().lstrip("@").strip("/")
    return f"https://www.instagram.com/{username}"
