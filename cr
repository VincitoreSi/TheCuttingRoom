#!/bin/sh
#
# ./cr — the container lane's only host-side entry point.
#
#   ./cr up                start the hub and open the browser (does NOT rebuild)
#   ./cr down              stop this checkout's container, leave the data
#   ./cr build             build the images
#   ./cr rebuild           rebuild from scratch AND drop the masking volumes
#   ./cr logs [-f]         hub logs
#   ./cr status            what this checkout is running
#   ./cr agent <name> ...  run an agent CLI inside the container
#   ./cr health [args]     run ./health in the dev image
#   ./cr verify-loopback   prove the published port is NOT reachable off loopback
#   ./cr keys              check-keys.py, from inside the container's network
#   ./cr shell             a shell in the running hub container
#   ./cr docsite           docs live-reload on http://127.0.0.1:8000
#   ./cr demo              load the demo dataset (unzips INSIDE the container)
#
# ------------------------------------------------------------------------------------------
# THERE IS NO cr.cmd, AND THERE WILL NOT BE ONE.
#
# DECISIONS.md D1: Windows support is WSL2 ONLY. The supported layout is a clone INSIDE the
# WSL2 filesystem (~/src/TheCuttingRoom, never /mnt/c/...), Docker Desktop on the WSL2
# backend, and THIS script run from a WSL2 bash/sh. A native NTFS clone is not supported and
# is not tested.
#
# A cmd.exe/PowerShell twin would have to bind-mount \\wsl.localhost paths through the Windows
# engine context and derive the per-checkout compose project name without sha256sum — two
# independent ways to silently fork one checkout into two compose projects and two sets of
# venv volumes, which is precisely the failure the project-name hash below exists to prevent.
#
# ------------------------------------------------------------------------------------------
# DESIGN CONSTRAINT 1: POSIX sh, and NO python3.
#
# The whole promise of the container lane is "the host needs only Docker". That promise is
# void the moment this script sources scripts/_common.sh, which needs python3 in five places
# (check_python, free_port, hub_state's JSON parse, port_free, and every check-keys.py call).
# So ./cr deliberately shares NO code with the six bash entry points. It is a few dozen lines
# of real logic; that is the price of the promise.
#
# DESIGN CONSTRAINT 2: identity comes from docker, never from GET /api/hub.
#
# _common.sh's hub_state decides "is this hub mine?" by asking GET /api/hub first and
# comparing the hub's reported root against $ROOT/ReelScraper, falling back to lsof cwd
# matching. Both branches are wrong for a container, and the first one is the one that fires:
# a containerized hub DOES answer, and reports root=/app/ReelScraper for EVERY checkout, which
# never equals the host's $ROOT/ReelScraper — so hub_state calls the container this checkout
# just started "foreign". Here, "mine" means "in my compose project". That is exact, not
# inferred.
# ------------------------------------------------------------------------------------------
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$ROOT/docker/docker-compose.yml"
ENVFILE="$ROOT/ReelScraper/.env"
IGNOREFILE="$ROOT/.dockerignore"

die() { printf './cr: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*" >&2; }

# Help first, before anything that probes Docker or writes to ReelScraper/.env. `./cr help`
# must work on a machine with no Docker at all — that is the first thing someone runs when
# they are trying to find out what they need.
case "${1:-}" in
  -h|--help|help) sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

# ./cr is a HOST script. Inside the container the four projects are siblings under /app and
# the hub is already running; a nested `docker compose` here would either fail confusingly or
# reach a docker socket that must never be mounted in the first place.
[ -z "${TCR_CONTAINER:-}" ] || die "./cr is a host-side launcher and you are inside the
container. Use the tools directly here (cd /app/SimilarContent && uv run cli.py ...), or exit
to the host."

[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE is missing — is this a full checkout?"

command -v docker >/dev/null 2>&1 || die "docker is not installed.
  macOS / Windows: Docker Desktop  https://docs.docker.com/desktop/
  Linux:           follow https://docs.docker.com/engine/install/ for your distro.
                   ('apt install docker.io' gives you an engine but NOT the compose plugin,
                   and 'docker-compose-plugin' is published by download.docker.com, not by
                   Debian/Ubuntu. Ubuntu >= 23.10 also ships 'docker-compose-v2'.)"
docker compose version >/dev/null 2>&1 || die "the 'docker compose' plugin is missing.
  Install it from https://docs.docker.com/engine/install/ — 'docker-compose' (with a hyphen,
  v1) is end-of-life and is not supported here."

# --- version floors -----------------------------------------------------------------------
# Two floors, for two different reasons.
#
# COMPOSE >= 2.20: docker-compose.yml uses the top-level `name:` key.
#
# ENGINE: the entire Linux security argument is "the 127.0.0.1: publish prefix is the control,
# because ufw cannot help you". That is true on a recent engine. Older engines have depended
# on net.ipv4.conf.*.route_localnet=1 for loopback publishing, which disables martian
# filtering for 127.0.0.0/8; newer ones add raw-table DROP rules for exactly that case.
# WHICH VERSIONS ARE SAFE HAS NOT BEEN ESTABLISHED BY ANYONE — see RISKS.md R20. The floor
# below is therefore a PLACEHOLDER that must be set from a real off-loopback test that records
# the engine version, the distro and the route_localnet sysctl it passed on. Until then it is
# deliberately loud rather than deliberately precise.
vercmp() {  # vercmp <a> <b> -> true if a >= b
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]
}

COMPOSE_VER=$(docker compose version --short 2>/dev/null | tr -d 'v') || COMPOSE_VER=""
if [ -z "$COMPOSE_VER" ]; then
  say "warning: could not read the docker compose version; assuming it is new enough."
elif ! vercmp "$COMPOSE_VER" 2.20.0; then
  die "docker compose $COMPOSE_VER is too old; 2.20 or newer is required (the compose file
uses the top-level 'name:' key).  https://docs.docker.com/engine/install/"
fi

ENGINE_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || ENGINE_VER=""
ENGINE_FLOOR="${TCR_ENGINE_FLOOR:-24.0.0}"
if [ -z "$ENGINE_VER" ]; then
  die "cannot reach the Docker daemon. Is Docker Desktop running (macOS/Windows), or is the
docker service started and your user in the 'docker' group (Linux)?"
fi
if ! vercmp "$ENGINE_VER" "$ENGINE_FLOOR"; then
  die "Docker Engine $ENGINE_VER is below the floor $ENGINE_FLOOR.
This project publishes the hub on 127.0.0.1 only, and that is the ONLY thing standing between
an unauthenticated subprocess launcher and your LAN. On older engines loopback publishing has
depended on net.ipv4.conf.all.route_localnet=1, which turns off martian filtering for
127.0.0.0/8. Check it:
    sysctl net.ipv4.conf.all.route_localnet
Belt, if you must stay on this engine:
    iptables -t raw -A PREROUTING ! -i lo -d 127.0.0.0/8 -j DROP
Then re-run with TCR_ENGINE_FLOOR=$ENGINE_VER, and run ./cr verify-loopback."
fi

# --- read/write a key in ReelScraper/.env -------------------------------------------------
# ReelScraper/.env, and not docker/.env, because that is where _common.sh's claim_port already
# pins HUB_PORT and where `health` and `stop` read it from. One file, one truth.
#
# The \r strip mirrors _common.sh's read_key. It is not decoration: a .env authored on Windows
# and read under WSL2 keeps its carriage returns, and HUB_PORT="8788\r" makes the compose
# publish spec invalid in a way whose error message names neither the file nor the key.
readkey() {
  [ -f "$ENVFILE" ] || return 0
  sed -n "s/^[[:space:]]*$1=//p" "$ENVFILE" 2>/dev/null | tail -1 | tr -d '\r "'
}
setkey() {
  # awk + mv rather than sed -i, for the same BSD/GNU portability reason as _common.sh:
  # `sed -i` takes an argument on BSD and not on GNU.
  mkdir -p "$(dirname "$ENVFILE")"
  touch "$ENVFILE"
  tmp="$ENVFILE.tmp.$$"
  awk -v k="$1" -v v="$2" '
    $0 ~ "^[[:space:]]*"k"=" { print k"="v; found=1; next } { print }
    END { if (!found) print k"="v }
  ' "$ENVFILE" > "$tmp" && mv "$tmp" "$ENVFILE"
}

# --- the per-checkout compose project name ------------------------------------------------
# A hash of the ABSOLUTE path, so two clones can never collide. sha256sum on Linux, shasum on
# macOS. If neither exists we must NOT fall back to the directory basename — see the
# COMPOSE_PROJECT_NAME note in docker-compose.yml — so we refuse instead.
project_name() {
  if command -v sha256sum >/dev/null 2>&1; then
    h=$(printf '%s' "$ROOT" | sha256sum | cut -c1-8)
  elif command -v shasum >/dev/null 2>&1; then
    h=$(printf '%s' "$ROOT" | shasum -a 256 | cut -c1-8)
  else
    die "neither sha256sum nor shasum is available, so ./cr cannot derive a unique compose
project name for this checkout. Set COMPOSE_PROJECT_NAME by hand in ReelScraper/.env —
anything unique per clone. Do NOT let it default to the directory name: two clones would then
share one compose project, and 'down' in one would stop the other and wipe its volumes."
  fi
  printf 'cuttingroom-%s' "$h"
}

PROJECT=$(readkey COMPOSE_PROJECT_NAME)
if [ -z "$PROJECT" ]; then
  PROJECT=$(project_name)
  setkey COMPOSE_PROJECT_NAME "$PROJECT"
  say "pinned COMPOSE_PROJECT_NAME=$PROJECT in ReelScraper/.env"
fi

PORT=$(readkey HUB_PORT)
if [ -z "$PORT" ]; then PORT=8787; setkey HUB_PORT "$PORT"; fi

# uid/gid: real values on Linux so bind-mount writes stay deletable by the user's own ./clean.
# Docker Desktop does its own translation, so 1000 is correct there and overriding it causes
# more problems than it solves.
case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux) TCR_UID=$(id -u); TCR_GID=$(id -g) ;;
  *)     TCR_UID=1000;     TCR_GID=1000 ;;
esac

BUILD_ID=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)
if ! git -C "$ROOT" diff --quiet 2>/dev/null; then BUILD_ID="$BUILD_ID-dirty"; fi

export COMPOSE_PROJECT_NAME="$PROJECT" HUB_PORT="$PORT" TCR_UID TCR_GID
export TCR_BUILD_ID="$BUILD_ID"
if [ -z "${TZ:-}" ]; then TZ=$(readkey TZ); fi
[ -n "${TZ:-}" ] || TZ=UTC
export TZ

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

url="http://127.0.0.1:$PORT"

open_browser() {
  # Host-side only, and never fatal.
  if command -v open          >/dev/null 2>&1; then open "$1"          >/dev/null 2>&1 && return 0; fi
  if command -v xdg-open      >/dev/null 2>&1; then xdg-open "$1"      >/dev/null 2>&1 && return 0; fi
  # WSL2: wslview comes from wslu and is the right answer when present. Otherwise the interop
  # binary on PATH is powershell.exe WITH the extension — probing a bare `powershell` never
  # matches, which is how an earlier draft opened no browser on the one platform where the
  # browser lives on the far side of a VM boundary.
  if command -v wslview       >/dev/null 2>&1; then wslview "$1"       >/dev/null 2>&1 && return 0; fi
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile Start "$1" >/dev/null 2>&1 && return 0
  fi
  return 0
}

# host_get <url> [timeout] — a GET from the HOST, without assuming curl.
#
# Nothing here may quietly assume curl: the promise is "the host needs only Docker", and a
# first-run test is supposed to happen on a machine that has none of the rest. /dev/tcp is a
# bashism and this is /bin/sh, so this uses whichever of curl/wget exists — and if NEITHER
# does, it returns 2 rather than pretending. Callers must distinguish 2 (could not test) from
# 1 (tested, refused); a check that silently degrades to "pass" is how a security assertion
# evaporates.
host_get() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf -o /dev/null --max-time "${2:-3}" "$1"; return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null -T "${2:-3}" "$1"; return $?
  fi
  return 2
}

cmd=${1:-up}
if [ $# -gt 0 ]; then shift; fi

case "$cmd" in

  build)
    # The .dockerignore guard. docker-compose.yml sets `context: ..`, so BuildKit reads
    # <repo root>/.dockerignore; a copy that lands in docker/ next to the Dockerfile is
    # SILENTLY IGNORED and the first `COPY ReelScraper/ ...` bakes real creator video, every
    # .env and platforms/x/session.txt into an immutable layer. And the file must be
    # deny-`**`-then-allow, never a denylist: ReelScraper/renders/, studio/<p>/, evals/ and
    # discovery/ do not exist on a fresh clone, so a denylist written against today's tree
    # misses them the moment someone builds after running the pipeline.
    [ -f "$IGNOREFILE" ] || die "$IGNOREFILE is missing.
The build context is the REPO ROOT, so the ignore file must be there. Without it the image
would contain scraped media, every agent .env, platforms/x/session.txt and the whole .git
history — permanently, in an immutable layer."
    first=$(grep -v '^[[:space:]]*#' "$IGNOREFILE" | grep -v '^[[:space:]]*$' | head -1)
    [ "$first" = "**" ] || die "$IGNOREFILE does not start with '**'.
It must DENY EVERYTHING and then allow, not maintain a denylist — the directories that hold
scraped data do not exist until someone runs the pipeline, so a denylist cannot name them.
First non-comment line found: '$first'"
    dc build "$@"
    ;;

  up)
    # NO --build. `up -d --build` on every invocation makes the entrypoint's lockfile
    # staleness gate unreachable on the only supported path — it could then only ever fire for
    # someone driving compose by hand. Building is `./cr build` / `./cr rebuild`; `up` starts
    # what you built, and the gate tells you when those have diverged.
    #
    # TCR_MODE=container is the flag the six bash entry points' container-mode guard reads.
    # NOTHING WROTE IT in the earlier draft, which made all six guards dead letters. Pinned on
    # `up` and deliberately NOT cleared by `down`: a checkout that runs in container mode is
    # still a container-mode checkout while its container is stopped, and clearing it would
    # make ./stop misreport the moment you brought the hub back. Clearing it is a deliberate
    # act — remove the line from ReelScraper/.env.
    #
    # (The guard does not exist in the entry points yet — `grep -rn TCR_MODE init demo stop
    # clean health docsite scripts/` returns nothing. Writing the key is harmless until it
    # lands, and it is the contract for whoever adds it.)
    if [ -z "$(readkey TCR_MODE)" ]; then
      setkey TCR_MODE container
      say "pinned TCR_MODE=container in ReelScraper/.env — once the entry-point guard lands,"
      say "the six bash scripts will redirect you to ./cr instead of guessing about a"
      say "containerized hub."
    fi

    dc up -d hub || die "docker compose up failed.

If the message mentions 'Bind for 127.0.0.1:$PORT failed: port is already allocated', another
process — very possibly another checkout's container — holds that host port. Pick a free one:
    ./cr down
    (edit HUB_PORT in ReelScraper/.env to $((PORT+1)))
    ./cr up
(the container always serves on 8787 internally; only the host side moves)

If instead it mentions a bind failure on '[::1]', this host has IPv6 disabled. Delete the
    - \"[::1]:\${HUB_PORT}:8787\"
line from docker/docker-compose.yml. DO NOT delete the '127.0.0.1:' prefix from the other
line — that one is the entire security boundary, and ./cr up opens http://127.0.0.1:\$PORT,
which does not need the IPv6 mapping.

If the image does not exist yet, run ./cr build."

    # Poll rather than sleep. A cold start (image pull, four venv volumes seeding, the
    # entrypoint's dashboard copy) is much slower than `uv run cli.py start`, so 180s. Break
    # early on a container that has died: with restart: "no" and the entrypoint's fail-closed
    # exit 78, the common failure is deterministic and already has a good message, and making
    # someone wait 180s to see it is the wrong trade.
    i=0
    while [ "$i" -lt 180 ]; do
      state=$(dc ps --format '{{.State}}' hub 2>/dev/null | head -1) || state=""
      case "$state" in
        exited|dead)
          say "the hub container exited. Its message:"
          dc logs --tail 40 hub >&2
          exit 1 ;;
      esac
      if dc exec -T hub /app/ReelScraper/.venv/bin/python -c \
           "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/platforms',timeout=2)" \
           >/dev/null 2>&1; then
        # That probe proves the hub is listening INSIDE its netns. It says nothing about the
        # PUBLISH, which is the half that can silently be wrong (a dropped 127.0.0.1: prefix,
        # an IPv6-only browser, a port collision reported in a way we did not parse). Check
        # the host side too, and say WHICH half failed.
        host_get "$url/api/platforms" 3 && hg=0 || hg=$?
        if [ "$hg" -eq 0 ]; then
          say ""
          say "  The Cutting Room is up:  $url"
          say "  logs:  ./cr logs -f     stop: ./cr down"
          say "  verify the loopback boundary:  ./cr verify-loopback"
          open_browser "$url"
          exit 0
        fi
        if [ "$hg" -eq 2 ]; then
          say ""
          say "  The Cutting Room is up:  $url"
          say "  (no curl or wget on this host, so the published port could not be verified"
          say "   from here — the hub itself answered inside the container.)"
          open_browser "$url"
          exit 0
        fi
        say "the hub is running, but the PUBLISHED port $PORT is not reachable from this host."
        say "The container is fine; the publish is not. Check the 'ports:' block in"
        say "docker/docker-compose.yml and 'docker compose -f $COMPOSE_FILE ps'."
        exit 1
      fi
      i=$((i + 1))
      sleep 1
    done
    say "the hub did not answer within 180s. Last 40 lines:"
    dc logs --tail 40 hub >&2
    exit 1
    ;;

  verify-loopback)
    # THE ONE CHECK THAT MUST RUN ON THE HOST.
    #
    # In container mode the hub binds 0.0.0.0 (Docker DNATs to eth0, never to the container's
    # loopback), so the loopback property lives entirely in the publish spec. That has to be
    # OBSERVED rather than inferred — and it cannot be observed from the `health` service,
    # which is network_mode: service:hub: inside the hub's netns the only non-loopback address
    # is the container's own eth0, where the hub really IS listening. A check written there
    # either always fails or gets weakened until it asserts nothing, which is RISKS.md R2
    # recreated one layer down. So it lives here, where the host-side listener is.
    #
    # LIMITS, stated rather than hidden. On Docker Desktop (macOS) "the host" is the Mac and
    # the forwarder is a host process, so this is a real test. On WSL2 it tests the WSL VM's
    # view, which under networkingMode=mirrored is NOT the same as the Windows host's view —
    # RISKS.md R9, still UNVERIFIED by anyone. A second machine on the same LAN remains the
    # only unconditional test.
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
      die "this check needs curl or wget on the HOST and neither is installed.
It cannot be run inside the container: there, the hub's own eth0 is a non-loopback address the
hub is legitimately listening on, so the check would either always fail or have to be weakened
until it proved nothing. Install curl, or run this from a second machine on the LAN:
    curl --max-time 2 http://<this-host-ip>:$PORT/api/platforms     # must FAIL"
    fi

    ips=""
    if command -v ip >/dev/null 2>&1; then
      ips=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
    elif command -v ifconfig >/dev/null 2>&1; then
      ips=$(ifconfig 2>/dev/null | awk '/inet /{print $2}' | grep -v '^127\.' || true)
    fi
    [ -n "$ips" ] || die "could not enumerate this host's non-loopback IPv4 addresses (no 'ip'
and no 'ifconfig'). Run the check from a second machine on the LAN instead:
    curl --max-time 2 http://<this-host-ip>:$PORT/api/platforms     # must FAIL"

    host_get "$url/api/platforms" 3 \
      || die "the hub is not answering on $url — start it with ./cr up first."

    bad=""
    for ip in $ips; do
      if host_get "http://$ip:$PORT/api/platforms" 2; then bad="$bad $ip"; fi
    done
    if [ -n "$bad" ]; then
      die "THE HUB IS REACHABLE OFF LOOPBACK on:$bad
All 55 routes on it are unauthenticated, and one of them launches a subprocess whose argv
another unauthenticated route supplies. Stop it now:  ./cr down
Then check the 'ports:' block in docker/docker-compose.yml — every entry must begin with
'127.0.0.1:' or '[::1]:'. On Linux also check that nothing else forwards to it:
    sudo iptables -t nat -L DOCKER -n"
    fi
    say "loopback boundary OK: $url answers; none of [$ips ] does."
    say "(Not a proof for WSL2 mirrored networking, and not a proof for a second host —"
    say " see RISKS.md R9. A machine on the LAN is the only unconditional test.)"
    ;;

  down)
    # `down`, not `stop`: it removes the container but NOT the named volumes (no -v), so the
    # four masking venv volumes survive and the next `up` is fast. Data is on the bind mount
    # and is never touched.
    #
    # --timeout 120 matches stop_grace_period. The entrypoint escalates to SIGKILL itself at
    # TCR_STOP_GRACE (100s by default) INSIDE that window, so a mid-scrape stop gets the
    # cooperative save path rather than a hard kill.
    dc down --timeout 120 "$@"
    ;;

  rebuild)
    # The masking volumes MUST be dropped, or a rebuilt image's new .venv is silently masked
    # by the old one and the container runs yesterday's dependencies while claiming today's
    # build id. This is why they are NAMED rather than anonymous: anonymous volumes cannot be
    # dropped by name and survive `down` forever.
    #
    # `down -v` drops every volume in the project, which is BOTH sets — venv-*-runtime and
    # venv-*-dev. That is what we want: they seed from two different build targets and a
    # rebuild invalidates both. It also drops node-modules, which is correct after a
    # package-lock change and merely slow otherwise.
    dc down --timeout 120 -v
    "$0" build --no-cache hub
    dc --profile dev build --no-cache health
    say "rebuilt. run ./cr up"
    ;;

  logs)   dc logs "$@" hub ;;

  status)
    printf 'checkout : %s\nproject  : %s\nhost port: %s\nbuild    : %s\n\n' \
      "$ROOT" "$PROJECT" "$PORT" "$BUILD_ID"
    dc ps || true
    # What else is publishing on this port, named directly rather than inferred from a working
    # directory — which is what ./stop tries to do and gets wrong under Docker Desktop, where
    # the listener is a forwarder process with cwd /.
    other=$(docker ps --filter "publish=$PORT" --format '{{.Names}} ({{.Image}})' 2>/dev/null || true)
    if [ -n "$other" ]; then printf '\nalso publishing :%s\n%s\n' "$PORT" "$other"; fi
    ;;

  agent)
    [ $# -ge 1 ] || die "usage: ./cr agent <analysis-engine|auto-search|similar-content> <args...>
This replaces 'cd SimilarContent && uv run cli.py propose ...' from the README and from
./init's next steps. The agent's venv lives in the image, so the host command no longer works
— and running it here also means both sides see /app, so the foreign-checkout guard in
SimilarContent/engine/hub.py:104,129 + cli.py:107 passes untouched."
    dc exec hub /usr/local/bin/tcr-entrypoint agent "$@"
    ;;

  demo)
    # THE DEV IMAGE, not `dc exec hub`. An earlier draft ran
    #   dc exec hub tcr-entrypoint sh -c 'cd /app && ./demo --no-launch --in-container'
    # and every part of that was wrong: ./demo requires curl (demo:80) and unzip (demo:54),
    # neither of which is in the runtime image by design; --no-launch and --in-container do
    # not exist (demo:25-34 accepts only --keep/--port/-h and dies on anything else); and if
    # it had got past those it would have started a SECOND hub inside the container.
    #
    # --no-launch is what makes this verb possible, and it now exists (demo:26). Without it
    # the script would reach start_hub and boot a second hub inside this throwaway
    # `compose run --rm` container — on a port nothing publishes, dying with the container.
    #
    # Running it in the container is the right shape: unzip and the dataset tree copy then
    # happen inside, which is what keeps zip/unzip off the host requirement list. The data
    # lands on the bind mount, so the hub started by `./cr up` sees it on reload.
    dc --profile dev run --rm health ./demo --no-launch "$@" \
      || die "demo failed.
If the error is about demodataset.zip, it needs to be in the checkout root — it ships
separately and is never committed. See demo-data/README.md."
    say "demo data loaded. reload $url in the browser."
    ;;

  health)
    # ARGS ARE FORWARDED VERBATIM, and nothing is added — including --strict.
    #
    # An earlier draft of this script passed --strict itself. It is deliberately NOT added
    # back now that the flag exists, because --strict turns the four git-dependent skips and
    # the unverified off-loopback check into hard failures, and a maintainer running
    # `./cr health` to see where they stand should not get a red run for a property they
    # never opted into checking. CI and the release gate ask for it explicitly:
    #
    #   ./cr health --strict          what CI runs
    #   ./cr health --strict --live   the release gate
    #
    # verify-loopback runs FIRST, on the HOST, and its result is handed in: inside the health
    # container the off-loopback check cannot see the host listener at all.
    if "$0" verify-loopback >/dev/null 2>&1; then
      TCR_LOOPBACK_VERIFIED=1; export TCR_LOOPBACK_VERIFIED
    else
      say "warning: ./cr verify-loopback did not pass or could not run, so ./health will have"
      say "         no host-side evidence for the loopback boundary. Run"
      say "         './cr verify-loopback' on its own to see why."
    fi
    # working_dir is /app in the compose service; ./health lives there.
    dc --profile dev run --rm health ./health "$@"
    ;;

  docsite)
    # DOCS_PORT through readkey, like HUB_PORT, so the port this script opens in a browser and
    # the port compose publishes cannot disagree.
    dport=$(readkey DOCS_PORT)
    [ -n "$dport" ] || dport="${DOCS_PORT:-8000}"
    DOCS_PORT="$dport"; export DOCS_PORT
    dc --profile docs up -d docs
    open_browser "http://127.0.0.1:$dport"
    say "docs on http://127.0.0.1:$dport  (./cr down stops it)"
    ;;

  shell)  dc exec hub /bin/sh ;;

  keys)
    # check-keys.py runs INSIDE the container on purpose: it then proves the CONTAINER's
    # egress path to generativelanguage.googleapis.com / api.anthropic.com, which is the one
    # that matters. Running it on the host proves the wrong network.
    dc exec hub python3 /app/scripts/check-keys.py "$@"
    ;;

  # -h/--help/help are handled at the top of the file, before the Docker probes.

  *) die "unknown command '$cmd' — try ./cr help" ;;
esac
