#!/bin/sh
# docker/entrypoint.sh — PID 2, under tini.
#
# Two modes:
#
#   CONTAINER START (`hub`, the CMD) — preflight, then supervise the hub:
#     0. is the checkout mounted at /app, and is its .venv the right architecture
#     1. refuse to start if the image's dependencies do not match the checkout's lockfiles
#     2. materialise the built dashboard into a tmpfs (the bind mount would shadow it)
#     3. materialise the built docs, if this image has them
#     4. writable-data preflight
#     5. start the hub and STAY, so container shutdown becomes a cooperative stop (see §5)
#
#   EXEC-TIME DISPATCH (`agent`, `shell`, anything else) — NO preflight.
#     `./cr agent` / `./cr shell` run through `docker compose exec` against a LIVE container.
#     Re-running step 2 there would copy over the tmpfs the running hub is serving from,
#     which is the exact 404 window scripts/deploy-dashboard.sh exists to avoid. The preflight
#     has already run, at start; running it again is all cost.
#
# Deliberately /bin/sh, not bash. Not because bash is absent — python:3.12-slim-bookworm is
# Debian 12 based and bash is Essential — but because nothing here needs bash and a start-up
# script should have the shortest possible dependency list. No colour, no spinner: this file
# has to be readable by someone debugging a container that will not start.
set -eu

APP=/app
HUB="$APP/ReelScraper"
PYBIN="$HUB/.venv/bin/python"

say() { printf '%s\n' "$*" >&2; }
die() { printf 'tcr-entrypoint: %s\n' "$*" >&2; exit 78; }   # 78 = EX_CONFIG

# ------------------------------------------------------------------------------------------
# Dispatch anything that is not a container start BEFORE the preflight. See the header.
# ------------------------------------------------------------------------------------------
case "${1:-hub}" in
  hub) : ;;                       # fall through to the preflight below
  shell)
    exec /bin/sh
    ;;
  agent)
    # ./cr agent <name> <args...>  ->  entrypoint agent <name> <args...>
    #
    # This verb exists because `cd SimilarContent && uv run cli.py propose` — printed by
    # `init` as a next step, and in the README and documentation/docs/cli.md — no longer works
    # from a host shell in container mode. The first thing that breaks there is not the
    # missing venv: it is the agent's own foreign-checkout guard
    # (SimilarContent/engine/hub.py:104,129 returns the mismatching root; cli.py:107 raises
    # SystemExit(2)), because the containerized hub reports root=/app/ReelScraper. Running the
    # agent HERE means both sides see /app and the guard passes untouched.
    shift
    name="${1:?usage: agent <analysis-engine|auto-search|similar-content> <args...>}"
    shift
    case "$name" in
      analysis-engine) dir=AnalysisEngine ;;
      auto-search)     dir=AutoSearch ;;
      similar-content) dir=SimilarContent ;;
      *) die "unknown agent '$name' (analysis-engine | auto-search | similar-content)" ;;
    esac
    cd "$APP/$dir"
    exec uv run cli.py "$@"
    ;;
  *)
    # Anything else runs verbatim, so `docker compose run --rm hub python3
    # scripts/check-keys.py` works. Running check-keys in here is strictly better than on the
    # host: it proves the CONTAINER's egress path, which is the one that matters for scraping
    # and for the Gemini/Anthropic calls.
    exec "$@"
    ;;
esac

# ------------------------------------------------------------------------------------------
# 0. Sanity: is the checkout mounted where we think, and is its .venv OUR architecture?
#
# THREE separate mechanisms compare the absolute path /app:
#   - app.py:1971-1972  ANALYSIS_ENGINE_DIR / AUTO_SEARCH_DIR = ROOT.parent / "<Name>"
#   - app.py:1986-2012  _producer_dir asserts the declared dir is a direct sibling
#   - the three agents' foreign_checkout(): Path(__file__).resolve().parents[2]/"ReelScraper"
#     vs the hub's reported root from GET /api/hub (app.py:778). The method lives in each
#     agent's engine/hub.py (AnalysisEngine:94, AutoSearch:91, SimilarContent:104 — three
#     SIMILAR files, not byte-identical) and only RETURNS the mismatching root; the
#     SystemExit(2) is in each agent's cli.py (:119, :143, :107).
# A mount at the wrong path produces three different confusing failures instead of one clear
# one.
#
# The interpreter probe is the other day-one failure. app.py:333-337 _interpreter() returns
# the first of (.venv/bin/python, venv/bin/python) that EXISTS — it checks existence, never
# architecture. On an Apple Silicon host that file is a Mach-O binary; bind-mounted into a
# Linux container it exists, so PY becomes it, and every STAGE_CMD entry using PY (scrape,
# analyze, media — app.py:1974-1977) fails with OSError: [Errno 8] Exec format error, recorded
# as rc -1 at app.py:2214, from a container that started perfectly. The masking .venv volumes
# in docker-compose.yml prevent it; this check is what turns "someone deleted those lines"
# into one sentence instead of three red nodes on the Board.
# ------------------------------------------------------------------------------------------
for d in ReelScraper AnalysisEngine AutoSearch SimilarContent; do
  [ -d "$APP/$d" ] || die "$APP/$d is missing.
The four projects must be siblings under /app. If you bind-mounted the checkout, mount it at
/app exactly — the hub, the producer-directory guard and all three agents compare that
absolute path and will each fail differently if it is wrong."
done

for d in ReelScraper AnalysisEngine AutoSearch SimilarContent; do
  py="$APP/$d/.venv/bin/python"
  [ -x "$py" ] || die "$py is missing or not executable.
docker-compose.yml must mount a named volume over each project's .venv. Without it the host's
own .venv shows through the bind mount, and on macOS that is a Mach-O binary that this Linux
container cannot exec. Check the 'venv-*' volume lines in docker/docker-compose.yml."
  "$py" -c '' 2>/dev/null || die "$py exists but will not run in this container.
Almost certainly it is a HOST virtualenv showing through the bind mount — a macOS Mach-O
python, or a Linux venv built against a different glibc. The 'venv-*' named volumes in
docker/docker-compose.yml exist to mask exactly this. Do not delete them; if you already
rebuilt, drop them with  ./cr rebuild."
done

# ------------------------------------------------------------------------------------------
# 1. Lockfile staleness gate.
#
# Source comes from the bind mount; dependencies come from the image. They can disagree — a
# `git pull` that bumps a uv.lock, or a rebuild someone forgot. Without this gate the failure
# is an opaque uv error (UV_OFFLINE=1 -> "no solution found: offline") in the middle of a
# pipeline stage, minutes later, attributed to the wrong thing.
#
# HONEST LIMIT: this catches DEPENDENCY skew only. It does not catch source-vs-dist skew — a
# Dashboard/src edit against a stale /opt/tcr/frontend-dist looks fine and serves the old UI.
# See RISKS.md R7. Accepted gap.
# ------------------------------------------------------------------------------------------
if [ "${TCR_SKIP_STAMP_CHECK:-0}" != "1" ] && [ -f /opt/tcr/lockstamp ]; then
  # Same recipe as the Dockerfile: hash each lock, sort, hash the hashes. `sort` because
  # sha256sum's output order follows argv, and argv order must not be load-bearing.
  now="$(sha256sum "$HUB/uv.lock" "$APP/AnalysisEngine/uv.lock" \
                   "$APP/AutoSearch/uv.lock" "$APP/SimilarContent/uv.lock" 2>/dev/null \
         | awk '{print $1}' | sort | sha256sum | awk '{print $1}')"
  want="$(cat /opt/tcr/lockstamp)"
  if [ "$now" != "$want" ]; then
    die "the checkout's uv.lock files do not match the ones this image was built from.

  image was built from: $want   (build ${TCR_BUILD_ID:-unknown})
  checkout has:         $now

The image carries the resolved dependencies; the mount carries the source. They have drifted,
which usually means a git pull changed a lock. uv cannot fix it at runtime (UV_OFFLINE=1 by
design, so a stage never silently re-resolves mid-pipeline).

  Fix:  ./cr rebuild

Override for a deliberate experiment only:  TCR_SKIP_STAMP_CHECK=1"
  fi
fi

# ------------------------------------------------------------------------------------------
# 2. The dashboard.
#
# ReelScraper/frontend/dist is NOT git-tracked (`git ls-files ReelScraper/frontend` is empty;
# .gitignore:50 is `dist/`). On a fresh clone the directory does not exist, so bind-mounting
# the checkout over /app shadows the image's copy with an empty host directory and app.py:
# 3509-3511 serves the self-polling "building" page at HTTP 503.
#
# WHY A TMPFS AND NOT A NAMED VOLUME: a named volume seeds from the image only when it is
# EMPTY, so after a rebuild the OLD dist survives and the container silently serves the
# previous UI forever. The payload is ~1 MB, so copying it into RAM on every start costs
# milliseconds and removes an entire class of staleness bug.
#
# A tmpfs does NOT solve ownership — it has exactly one. Docker mounts a tmpfs root:root and
# `mode=` sets bits, not owner, so with `user:` set the mode must be 1777 or this copy fails.
#
# `cp -R --preserve=timestamps`, NOT `cp -a`. This is a real bug the drafts had: `cp -a`
# implies --preserve=all, which includes OWNERSHIP, and a non-root process copying files owned
# by uid 1000 while running as (say) uid 501 gets EPERM from chown and cp exits non-zero. That
# would make every Linux run with TCR_UID != 1000 die here. Timestamps are the only preserved
# attribute that matters (stable Last-Modified/ETag across restarts, so the browser does not
# refetch 1 MB on every container start).
# ------------------------------------------------------------------------------------------
if [ -d /opt/tcr/frontend-dist ]; then
  mkdir -p "$HUB/frontend/dist" 2>/dev/null || true
  cp -R --preserve=timestamps /opt/tcr/frontend-dist/. "$HUB/frontend/dist/" \
    || die "could not write $HUB/frontend/dist as uid $(id -u).

Almost always this is tmpfs OWNERSHIP, not a missing mount. Docker mounts a tmpfs as
root:root; 'mode=' sets permission bits and not the owner, and this container runs non-root on
purpose. In docker/docker-compose.yml the entry must be

    - /app/ReelScraper/frontend/dist:mode=1777,size=64m

Do NOT 'fix' this by deleting the 'user:' line or setting user: \"0:0\". That gives back the
one real gain containerizing provides and leaves root-owned files in your checkout that your
own ./clean cannot delete."
  [ -f "$HUB/frontend/dist/index.html" ] \
    || die "dashboard copy produced no index.html; the image build is broken"
else
  say "tcr-entrypoint: warning — no built dashboard in this image; / will serve the"
  say "                'building' placeholder at HTTP 503."
fi

# ------------------------------------------------------------------------------------------
# 3. Docs. app.py:3396-3398 mounts ROOT.parent/documentation/site AT IMPORT, and only
# `if DOCS_SITE.exists()`, so this must happen before the hub starts or /documentation 404s
# for the life of the process. Absent unless built with --build-arg WITH_DOCS=1.
#
# THE TARGET IS ON THE HOST BIND MOUNT. documentation/site is not tmpfs'd, so this writes a
# few MB into the user's checkout on every start. A deliberate, narrow exception — the
# alternative is a fourth tmpfs for a feature that is off by default — but it must not be
# silent, and it must not be `|| true`: a half-copied docs tree that then gets mounted is
# worse than no docs.
# ------------------------------------------------------------------------------------------
if [ -d /opt/tcr/docs-site ]; then
  say "tcr-entrypoint: materialising documentation/site into the checkout (WITH_DOCS=1)"
  mkdir -p "$APP/documentation/site" \
    || die "cannot create $APP/documentation/site — rebuild with WITH_DOCS=0, or fix the
ownership of the checkout on the host."
  cp -R --preserve=timestamps /opt/tcr/docs-site/. "$APP/documentation/site/" \
    || die "could not write $APP/documentation/site as uid $(id -u). This path is on the
bind-mounted checkout, not a tmpfs, so it needs to be writable by the container's uid."
fi

# ------------------------------------------------------------------------------------------
# 4. Writable-data preflight.
#
# Every one of these is created with .mkdir() by the hub at import or first use (media,
# renders, frontend, config, and core/logsetup.py's log dir). Under `user: <uid>:<gid>` on
# native Docker Engine, a bind-mounted host directory owned by someone else makes those
# mkdir/write calls fail — and several of them fail at IMPORT, which presents as a container
# that exits before logging anything useful. Check early, name the path, exit clean.
#
# HOME is checked too: a TCR_UID with no /etc/passwd entry gets HOME=/ , and uv and anything
# calling expanduser() then fail obscurely. The image and compose both set HOME=/tmp.
# ------------------------------------------------------------------------------------------
[ -w "${HOME:-/}" ] || die "HOME=${HOME:-<unset>} is not writable by uid $(id -u).
Set HOME=/tmp in the service environment (docker/docker-compose.yml does this); a uid other
than 1000 has no /etc/passwd entry in this image and falls back to /."

for d in "$HUB/media" "$HUB/logs" "$HUB/config" "$HUB/renders"; do
  mkdir -p "$d" 2>/dev/null || true
  [ -w "$d" ] || die "$d is not writable by uid $(id -u).
On Linux the container runs as your uid so files it writes stay deletable by your own
./clean. If you changed TCR_UID/TCR_GID, or the checkout is owned by another user, fix the
ownership on the host:  sudo chown -R \$(id -u):\$(id -g) <checkout>"
done

# ------------------------------------------------------------------------------------------
# 5. Start the hub, and SUPERVISE it. This section is the fix for DOCKER-PLAN §7.1 / R8.
#
# THE PROBLEM, walked through. `docker stop` sends SIGTERM to PID 1 only. tini forwards it to
# this script (its direct child). Nothing signals the STAGE process groups: _run_job spawns
# each stage with `start_new_session=_POSIX` (app.py:2184) precisely so that a killpg can
# target it, the pgid registry is _PROCS (app.py:1957) and the cancel path is _signal_group ->
# os.killpg (app.py:2259,2278) — but that path is reachable from the Stop button ONLY. There
# is no @app.on_event("shutdown") in app.py (:363 is the only on_event and it is startup), and
# cli.py:110-113 records from measurement that "uvicorn's signal handling exits the process
# without unwinding back through this frame". _run_job runs on a DAEMON thread (app.py:2382-
# ish), and Python does not join daemon threads. So the hub exits, PID 1 exits, the runtime
# tears down the cgroup, and the stage is SIGKILLed mid-write — the exact hard kill that
# core/stopflag.py and core/atomicio.py exist to prevent. `stop_grace_period` alone is
# decoration.
#
# THE FIX, without touching application source. Do not `exec` the hub. Run it as a child, keep
# this shell as PID 2, and on SIGTERM send SIGTERM to EVERY process in this PID namespace
# before waiting.
#
#   In a PID namespace, everything except PID 1 (tini) and this shell IS the hub plus its
#   stages, so enumerating /proc and signalling each pid reaches all of them without needing
#   to know any pgid. Signalling every PROCESS rather than every process GROUP is deliberately
#   more thorough than the Stop button's killpg: it also reaches a `uv run` grandchild whose
#   parent has already gone. (`kill -TERM -1` would be one call instead of a loop, but the
#   loop is legible, is trivially auditable, and does not depend on how a particular /bin/sh
#   builtin parses "-1".)
#
#   The three scrapers install core/stopflag.install_stop_handler() (instagram:362,
#   youtube:409, x:386) and check stop_requested() at the top of the per-creator loop, and
#   AnalysisEngine/cli.py:449,473 does the same. For those stages this converts container
#   shutdown into the identical cooperative path the Stop button uses: the corpus scraped so
#   far is saved, and the process exits.
#
#   HONEST LIMIT: `analyze`, `media`, `auto-search` and `propose` do NOT install a stop
#   handler, so they take the default SIGTERM disposition and die where they stand. That is
#   still strictly better than SIGKILL — and it is exactly what the host lane's Stop button
#   already does to them, so it is not a container-specific regression.
#
# WHAT THIS COSTS: one extra /bin/sh in the container, and the loss of `exec`. Worth it —
# without it, `docker stop` is a hard kill dressed up as a graceful one.
#
# WHAT IT STILL DOES NOT FIX: uvicorn's own graceful shutdown waits on open connections. A
# dashboard tab holding the /api/events SSE stream (app.py:3358) can make the hub take a long
# time to exit. The bounded loop below is why that cannot exceed the container's grace period
# and turn into a runtime SIGKILL of everything at once. The proper fix upstream is a FastAPI
# shutdown hook that walks _PROCS and killpg(SIGTERM), plus a uvicorn
# timeout_graceful_shutdown; that is a source change and is NOT owned by this file.
# ------------------------------------------------------------------------------------------

# Seconds to wait for everything to exit after SIGTERM. Must be COMFORTABLY BELOW compose's
# stop_grace_period (120s), or the runtime SIGKILLs the cgroup before this loop escalates and
# the whole mechanism is bypassed.
GRACE="${TCR_STOP_GRACE:-100}"

_sleeper=""

# A zombie still has a /proc entry, and the hub is our own child, so it WILL be a zombie
# between its exit and our `wait`. Counting it as alive would burn the entire grace period
# every single time. The third whitespace-separated field of /proc/<pid>/stat is the state
# character; field 2 is the parenthesised comm, so strip through the last ") ".
_is_zombie() {
  s=$(cat "/proc/$1/stat" 2>/dev/null) || return 0    # gone between glob and read
  s=${s##*) }
  case "$s" in Z*) return 0 ;; esac
  return 1
}

# Is anything alive besides PID 1 (tini), this shell, and the `sleep` we are waiting on?
# /proc rather than ps, because procps is deliberately not installed in the runtime image.
_others_alive() {
  for d in /proc/[0-9]*; do
    p=${d#/proc/}
    case "$p" in
      1|"$$"|"${_sleeper:-x}") continue ;;
    esac
    [ -d "$d" ] || continue
    _is_zombie "$p" || return 0
  done
  return 1
}

_nap() { sleep 1 & _sleeper=$!; wait "$_sleeper" 2>/dev/null || true; _sleeper=""; }

# _signal_all <SIGNAME> — signal every process in this namespace except PID 1 and ourselves.
_signal_all() {
  for d in /proc/[0-9]*; do
    p=${d#/proc/}
    case "$p" in
      1|"$$"|"${_sleeper:-x}") continue ;;
    esac
    kill -"$1" "$p" 2>/dev/null || true
  done
}

_shutdown() {
  trap '' TERM INT     # a second signal must not re-enter this
  say "tcr-entrypoint: stop requested — asking the hub and every running stage to finish"
  _signal_all TERM

  n=0
  while [ "$n" -lt "$GRACE" ] && _others_alive; do
    _nap
    n=$((n + 1))
  done

  if _others_alive; then
    say "tcr-entrypoint: still running after ${GRACE}s — SIGKILL."
    say "                If this happens every time, a dashboard tab is probably holding the"
    say "                /api/events SSE stream open and uvicorn is waiting on it."
    _signal_all KILL
  else
    say "tcr-entrypoint: stopped cleanly after ${n}s"
  fi
  exit 0
}
trap _shutdown TERM INT

cd "$HUB"
# The VENV PYTHON DIRECTLY, not `uv run`:
#   - one less process between tini and uvicorn, and no question about whether `uv run` execs
#     or forks when a signal arrives
#   - .venv/bin/python is also what app.py:333-337 _interpreter() picks for PY, so the hub and
#     its scrape/analyze/media children run the same interpreter
#   - --no-browser unconditionally: cli.py:79-84 spawns a thread that calls webbrowser.open()
#     1.5s after start; in a Linux container that either no-ops or finds a text browser on
#     PATH, and the URL it would open is the CONTAINER's loopback, which is meaningless as a
#     link. Browser-opening belongs to the host launcher (./cr up).
#   - --strict-port so cli.py:49-64's fallback cannot silently move the hub to an OS-assigned
#     port the published mapping does not point at. Nothing else in this netns can hold 8787,
#     so a bind failure here means something is genuinely wrong.
#   (Both flags verified present: cli.py:144-146.)
"$PYBIN" cli.py start --no-browser --strict-port &
HUB_PID=$!

# `wait` returns >128 when a trapped signal interrupts it; the trap runs and exits, so the
# loop below only ever iterates on a spurious wakeup.
while :; do
  if wait "$HUB_PID"; then rc=0; else rc=$?; fi
  kill -0 "$HUB_PID" 2>/dev/null || break
done
say "tcr-entrypoint: the hub exited (rc=$rc)"
exit "$rc"
