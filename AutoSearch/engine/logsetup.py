#!/usr/bin/env python3
"""engine/logsetup.py — per-run logging, mirroring ReelScraper's core/logsetup.py and
AnalysisEngine's engine/logsetup.py (PIPELINE.md §10.1, tier 1 "local, full fidelity").

Every AutoSearch command calls setup_logging(command, platform) ONCE at startup. It installs:

  * a pretty, level-coloured CONSOLE handler (plain when stderr isn't a TTY), and
  * a machine-readable JSON-LINES FILE handler, one file per invocation, named by start
    time:  logs/<ISO-start>_<command>[_<platform>].log

Modules do `log = logging.getLogger("as.<mod>")` and pass structured fields via
`extra={...}`; they land as real keys in the JSONL so logs stay queryable.

The CENTRAL tier (PIPELINE.md §10.1, tier 2 "curated lifecycle events") is a separate
concern: HubClient.post_log() POSTs run start/end, per-item events, and errors to
`POST /api/logs` with the `run_id` returned here — never every debug line.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"

_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
}

_ANSI = {
    "DEBUG": "\033[38;5;244m", "INFO": "\033[38;5;39m", "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;196m", "CRITICAL": "\033[48;5;196m\033[97m",
}
_RESET = "\033[0m"
_DIM = "\033[38;5;244m"

_SESSION_ID: str | None = None


class _SessionFilter(logging.Filter):
    """Stamp every record with the run's session_id / command / platform."""

    def __init__(self, session_id: str, command: str, platform: str | None):
        super().__init__()
        self.session_id, self.command, self.platform = session_id, command, platform

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id
        record.command = self.command
        record.platform = self.platform or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with any extra=... fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "session_id": getattr(record, "session_id", None),
            "command": getattr(record, "command", None),
            "platform": getattr(record, "platform", None),
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and k not in payload:
                try:
                    json.dumps(v)
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
            and k not in ("session_id", "command", "platform")
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
    """Configure root logging for this process. Idempotent — returns the run_id/session_id.

    The returned id is used as the `run_id` on every central `POST /api/logs` event so a hub
    log line links back to this run's local file (PIPELINE.md §10.1).
    """
    global _SESSION_ID
    if _SESSION_ID is not None:
        return _SESSION_ID

    start = time.strftime("%Y-%m-%dT%H-%M-%S")
    session_id = f"{start}_{os.getpid()}"
    _SESSION_ID = session_id

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{start}_{command}" + (f"_{platform}" if platform else "") + ".log"
    logfile = LOG_DIR / fname

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    sess = _SessionFilter(session_id, command, platform)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(_PrettyFormatter(color=sys.stderr.isatty()))
    console.addFilter(sess)
    root.addHandler(console)

    filehandler = logging.FileHandler(logfile, encoding="utf-8")
    filehandler.setLevel(logging.DEBUG)
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
        "logging initialised", extra={"session_id": session_id, "logfile": str(logfile)}
    )
    return session_id


def get_session_id() -> str | None:
    """The current run's session/run id, or None if setup_logging hasn't run yet."""
    return _SESSION_ID
