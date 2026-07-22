#!/usr/bin/env python3
"""
cli.py — the single entry point for the whole app.

  python cli.py start      # boot the API hub on localhost + open the web app (default)
  python cli.py analyze <platform>
  python cli.py scrape  <platform>
  python cli.py media   <platform>

`start` runs everything locally (127.0.0.1:8787): the FastAPI hub serves the API and,
if built, the React frontend (frontend/dist). Opens the browser automatically.
Ship this as the console command `viralitylab` (see pyproject/entry point).
"""
import atexit, os, socket, sys, subprocess, threading, time, webbrowser, argparse, logging
import ipaddress
from pathlib import Path

from core.logsetup import setup_logging

ROOT = Path(__file__).parent


def _interpreter():
    """The Python used to run child scripts: this interpreter (works under `uv run`),
    else a local .venv/venv if present."""
    for cand in (ROOT / ".venv" / "bin" / "python", ROOT / "venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


PY = _interpreter()
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8787
HOST = os.environ.get("HUB_HOST", DEFAULT_HOST)
PORT = int(os.environ.get("HUB_PORT", DEFAULT_PORT))
log = logging.getLogger("cli")


def port_is_free(host: str, port: int) -> bool:
    """True if we could bind (host, port) right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(host: str, preferred: int, allow_fallback: bool = True) -> int:
    """The port to actually serve on.

    uvicorn dies with a bare `OSError: Address already in use` if the port is taken, which
    is a poor first-run experience when something unrelated owns 8787. Probe first, and
    fall back to an OS-assigned free port rather than refusing to start.
    """
    if port_is_free(host, preferred):
        return preferred
    if not allow_fallback:
        raise SystemExit(f"port {preferred} on {host} is already in use")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        chosen = s.getsockname()[1]
    log.warning("port %d is busy — falling back to %d", preferred, chosen)
    return chosen


def hub_url(bind_host: str, port: int) -> str:
    """The URL to ADVERTISE — print, log, open, and hand to sibling agents as BACKEND_API.

    Not the same thing as the address we bind, and conflating the two was a real bug: a plain
    `docker run` logged the IPv4 wildcard as the hub's URL, and that link opened `about:blank`
    in the browser, while `localhost:8787` typed by hand worked fine. A wildcard bind means
    "listen on every interface"; it is not a destination, and Chrome refuses to navigate to it
    at all — so the failure presented as a dead page rather than a connection error.

    The container binds the wildcard ON PURPOSE (`HUB_HOST` is set to it in docker/Dockerfile
    — it is the only address compose can forward a published port to), so the bind is right
    and it is only the advertisement that has to be translated back to something dialable.

    `HUB_ADVERTISE` overrides all of it, for a hub reachable under a name or address it has no
    way to infer from its own socket. docker-compose.yml has documented that knob, commented
    out, since before there was anything here to read it.

    DETECTED VIA `is_unspecified`, NOT BY COMPARING STRINGS — and this docstring avoids
    spelling the address for the same reason. `./health` greps this file for that literal and
    fails the "hub binds loopback only" invariant on any hit (health:304, RISKS.md R2). The
    check is a security control and is deliberately blunt: it cannot tell prose from a bind,
    which is precisely why it has no false negatives. The semantic test is the better one
    anyway — it catches the IPv6 wildcard too, which a string compare would have missed.
    """
    host = os.environ.get("HUB_ADVERTISE", "").strip() or bind_host
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return f"http://{host}:{port}"      # a hostname — already dialable, leave it alone
    if addr.is_unspecified:
        addr = ipaddress.ip_address("::1" if addr.version == 6 else "127.0.0.1")
    # An IPv6 literal is not a legal URL host unless it is bracketed.
    return f"http://[{addr}]:{port}" if addr.version == 6 else f"http://{addr}:{port}"


def cmd_start(args):
    import uvicorn
    setup_logging("hub")
    host = getattr(args, "host", None) or HOST
    port = resolve_port(host, getattr(args, "port", None) or PORT,
                        allow_fallback=not getattr(args, "strict_port", False))
    # `host` is what we BIND. `url` is what we tell everyone else to CONNECT to, which is not
    # always the same string — see hub_url().
    url = hub_url(host, port)
    # Every sibling agent resolves the hub from BACKEND_API. Export it here so that stage
    # runners spawned by the hub (AnalysisEngine, AutoSearch, a producer's render command)
    # inherit the port we actually got instead of defaulting to 8787.
    os.environ["BACKEND_API"] = url

    if not getattr(args, "no_browser", False):
        def _open():
            time.sleep(1.5)
            log.info("opening browser", extra={"url": url})
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    log.info("pipeline app up — Ctrl+C to stop", extra={"url": url, "docs": f"{url}/docs"})
    # Printed unconditionally: a launcher script needs to discover the chosen port, and the
    # structured log line above may be filtered or JSON-formatted.
    print(f"HUB_URL={url}", flush=True)

    # Write the pid file HERE rather than in the launcher script. `uv run` spawns this
    # interpreter as a child and then exits, so a shell capturing `$!` records the wrapper's
    # pid — which is dead moments later, while the server keeps listening. `kill $(cat
    # .hub.pid)` then silently does nothing, which is exactly the sort of thing you only
    # discover when a restart appears to work and doesn't. os.getpid() is the server.
    pidfile = ROOT.parent / ".hub.pid"
    try:
        pidfile.write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError as e:
        log.warning("could not write pid file", extra={"path": str(pidfile), "err": str(e)})

    def _clear_pidfile():
        # Only remove it if it is still ours — a newer hub may have replaced it.
        try:
            if pidfile.read_text().strip() == str(os.getpid()):
                pidfile.unlink()
        except (OSError, ValueError):
            pass

    # Best effort only. Measured: uvicorn's signal handling exits the process without
    # unwinding back through this frame, so neither a `finally` here nor this atexit hook
    # actually fires on SIGTERM — and nothing survives SIGKILL. The pid file is therefore
    # ADVISORY: it goes stale on shutdown, and a recycled pid could point at an unrelated
    # process. Readers must check liveness, and the launchers tell users to stop the hub by
    # PORT (`lsof -ti tcp:<port> -sTCP:LISTEN | xargs kill`) rather than by this file.
    atexit.register(_clear_pidfile)

    # log_config=None so uvicorn keeps OUR root handlers (its loggers propagate to them)
    uvicorn.run("api.app:app", host=host, port=port, log_config=None)


def _passthrough(platform, script_args, cwd):
    subprocess.run([PY, *script_args], cwd=str(cwd))


def cmd_scrape(args):
    _passthrough(args.platform, ["scrape.py", "--file", "pages.txt"], ROOT / "platforms" / args.platform)

def cmd_analyze(args):
    _passthrough(args.platform, ["run.py", "analyze"], ROOT / "platforms" / args.platform)

def cmd_media(args):
    subprocess.run([PY, "download_media.py", args.platform], cwd=str(ROOT))


def main():
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd")
    start = sub.add_parser("start")
    start.add_argument("--port", type=int, default=None,
                       help=f"port to serve on (default {DEFAULT_PORT}, or $HUB_PORT)")
    start.add_argument("--host", default=None,
                       help=f"host to bind (default {DEFAULT_HOST}, or $HUB_HOST)")
    start.add_argument("--strict-port", action="store_true",
                       help="fail if the port is taken instead of picking a free one")
    start.add_argument("--no-browser", action="store_true", help="don't open a browser")
    start.set_defaults(fn=cmd_start)
    for name, fn in (("scrape", cmd_scrape), ("analyze", cmd_analyze), ("media", cmd_media)):
        s = sub.add_parser(name); s.add_argument("platform"); s.set_defaults(fn=fn)
    args = ap.parse_args()
    (args.fn if getattr(args, "fn", None) else cmd_start)(args) if args.cmd else cmd_start(args)


if __name__ == "__main__":
    main()
