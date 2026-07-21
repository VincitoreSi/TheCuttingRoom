#!/usr/bin/env python3
"""
logsetup.py — shared logging convention for every producer agent (the hub contract §10.1).

Mirrors the shape of ReelScraper's `core/logsetup.py` so every agent in the pipeline
logs identically, then adds the ONE thing a producer needs on top: a tiny helper to
POST curated LIFECYCLE events to the hub's central log.

Two tiers, one schema  {ts, agent, run_id, platform, level, event, content_id?, msg, data}:

  * LOCAL (full fidelity) — every run writes `logs/<ISO-start>_<cmd>.log`:
      - a pretty, level-coloured CONSOLE handler (plain when stdout isn't a TTY), and
      - a machine-readable JSON-LINES FILE handler (one file per run).
    All debug detail lives here. Modules just do `log = logging.getLogger("agent.run")`
    and pass structured fields via `extra={...}` — they land as real JSONL keys.

  * CENTRAL (curated) — `hub_log(...)` POSTs LIFECYCLE events only (run start/end,
    per-item done, errors, eval scores) to `POST {BACKEND_API}/api/logs`. NEVER every
    debug line. `run_id` links a hub event back to this run's local file so the
    Dashboard Activity view can jump from the central timeline to full local detail.

This module is self-contained (stdlib only) and parameterized entirely by `BACKEND_API`
+ `AGENT_NAME` — it never hardcodes the hub's on-disk path.

Usage:
    import logsetup
    run_id = logsetup.setup_logging("run", platform="instagram")   # local handlers
    log = logging.getLogger("agent.run")
    log.info("starting", extra={"limit": 3})
    logsetup.hub_log("run_start", platform="instagram", data={"limit": 3})  # -> hub
    ...
    logsetup.hub_log("run_end", level="info", data={"produced": 3})
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---- config from env (the ONLY bootstrap inputs — §10.3) -----------------------------
BACKEND_API = os.environ.get("BACKEND_API", "http://127.0.0.1:8787").rstrip("/")
AGENT_NAME = os.environ.get("AGENT_NAME", "<AGENT_NAME>")

# ---- where local logs go (this agent's own dir/logs — never a sibling's) -------------
LOG_DIR = Path(__file__).resolve().parent / "logs"

# keys always present on a LogRecord — everything else a caller passes via extra= is
# treated as a structured field and serialised into the JSONL line.
_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
}

_ANSI = {  # console level colours
    "DEBUG": "\033[38;5;244m", "INFO": "\033[38;5;39m", "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;196m", "CRITICAL": "\033[48;5;196m\033[97m",
}
_RESET = "\033[0m"
_DIM = "\033[38;5;244m"

# module-level guard so a second call is a no-op that returns the live run id
_RUN_ID: str | None = None


class _SessionFilter(logging.Filter):
    """Stamp every record with the run's run_id / command / platform."""

    def __init__(self, run_id: str, command: str, platform: str | None):
        super().__init__()
        self.run_id, self.command, self.platform = run_id, command, platform

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        record.command = self.command
        record.platform = self.platform or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with any extra=... fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "agent": AGENT_NAME,
            "run_id": getattr(record, "run_id", None),
            "platform": getattr(record, "platform", None),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and k not in payload:
                try:
                    json.dumps(v)  # keep only JSON-serialisable extras
                    payload[k] = v
                except (TypeError, ValueError):
                    payload[k] = repr(v)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


class _PrettyFormatter(logging.Formatter):
    """Aligned, human-readable console line; colours only on a TTY."""

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        lvl = record.levelname
        name = record.name
        msg = record.getMessage()
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STD_ATTRS
            and k not in ("run_id", "command", "platform")
            and not k.startswith("_")
        }
        if extras:
            msg += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if self.color:
            c = _ANSI.get(lvl, "")
            line = f"{_DIM}{ts}{_RESET} {c}{lvl:<7}{_RESET} {_DIM}{name}{_RESET}  {msg}"
        else:
            line = f"{ts} {lvl:<7} {name}  {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(command: str, platform: str | None = None, level: int = logging.INFO) -> str:
    """Configure root logging for this process. Idempotent — returns the run_id.

    command:  short verb for the log filename, e.g. "run", "once", "status".
    platform: optional platform tag ("instagram", "x", "youtube") for filename + records.
    """
    global _RUN_ID
    if _RUN_ID is not None:
        return _RUN_ID

    start = time.strftime("%Y-%m-%dT%H-%M-%S")
    run_id = f"{start}_{os.getpid()}"
    _RUN_ID = run_id

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{start}_{command}" + (f"_{platform}" if platform else "") + ".log"
    logfile = LOG_DIR / fname

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers decide what surfaces
    for h in list(root.handlers):  # own the output
        root.removeHandler(h)

    sess = _SessionFilter(run_id, command, platform)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(_PrettyFormatter(color=sys.stderr.isatty()))
    console.addFilter(sess)
    root.addHandler(console)

    filehandler = logging.FileHandler(logfile, encoding="utf-8")
    filehandler.setLevel(logging.DEBUG)  # file keeps the full detail
    filehandler.setFormatter(_JsonFormatter())
    filehandler.addFilter(sess)
    root.addHandler(filehandler)

    logging.captureWarnings(True)

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("uncaught").critical(
            "uncaught exception", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _excepthook

    logging.getLogger("logsetup").debug(
        "logging initialised", extra={"run_id": run_id, "logfile": str(logfile)}
    )
    return run_id


def get_run_id() -> str | None:
    """The current run's id, or None if setup_logging hasn't run yet."""
    return _RUN_ID


def hub_log(
    event: str,
    *,
    level: str = "info",
    msg: str | None = None,
    platform: str | None = None,
    content_id: str | None = None,
    data: dict | None = None,
    timeout: float = 5.0,
) -> bool:
    """POST one curated LIFECYCLE event to the hub's central log (§10.1).

    Use for run start/end, per-item done, errors, and eval scores — NOT every debug
    line (those stay in the local JSONL file). Best-effort: a hub outage must never
    crash the agent, so failures are logged locally and swallowed. Returns True on 2xx.

    Schema posted: {ts, agent, run_id, platform, level, event, content_id?, msg, data}.
    """
    body = {
        "agent": AGENT_NAME,
        "run_id": _RUN_ID,
        "platform": platform,
        "level": level,
        "event": event,
        "content_id": content_id,
        "msg": msg,
        "ts": time.time(),
        "data": data or {},
    }
    # mirror the lifecycle event into the local log too, so nothing is lost if the hub is down
    logging.getLogger("hub").log(
        getattr(logging, level.upper(), logging.INFO),
        event,
        extra={"event": event, "content_id": content_id, **(data or {})},
    )
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND_API}/api/logs",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as e:  # hub down / unreachable — never fatal
        logging.getLogger("hub").warning("hub_log failed", extra={"event": event, "err": str(e)})
        return False


def item_stage(run_id: str, content_id: str, stage: str) -> bool:
    """Convenience wrapper for the `item.stage` lifecycle event (workflow-board event vocab).

    Emits a stage-transition event for one in-flight item, e.g. right before self-eval:
        logsetup.item_stage(run_id, content_id, "Self-eval")

    `stage` MUST be one of this agent's declared `workflow_stages` (agent.json) — the hub's
    per-agent board (`GET /api/agents/{name}/board`) keys the item's current lane off `data.stage`.

    Note: `hub_log` already stamps the *active* run's `run_id` from `setup_logging()`'s module
    state on every call. `run_id` is accepted here for call-site clarity/parity with the other
    `item.start` / `item.done` calls (which all share one run_id per invocation) — it is not
    itself re-sent as a separate field.
    """
    return hub_log("item.stage", content_id=content_id, data={"stage": stage})


if __name__ == "__main__":
    # smoke test: `python3 logsetup.py` — writes a local log line, attempts one hub_log.
    rid = setup_logging("selftest", platform="instagram")
    logging.getLogger("agent.selftest").info("logsetup self-test ok", extra={"run_id": rid})
    hub_log("selftest", msg="scaffold logsetup reachable-check", data={"ok": True})
    print(f"run_id={rid}  agent={AGENT_NAME}  backend={BACKEND_API}")
