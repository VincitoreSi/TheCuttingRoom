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
#   ./cr keys --set        enter your Gemini API key (the container lane's ./init prompt)
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
# The _file variants take the path, because the per-agent .env files (AnalysisEngine/.env and
# SimilarContent/.env, where the Gemini key lives) are not this checkout's ReelScraper/.env.
readkey_file() {
  [ -f "$1" ] || return 0
  sed -n "s/^[[:space:]]*$2=//p" "$1" 2>/dev/null | tail -1 | tr -d '\r "'
}
setkey_file() {
  # awk + mv rather than sed -i, for the same BSD/GNU portability reason as _common.sh:
  # `sed -i` takes an argument on BSD and not on GNU.
  _f=$1; _k=$2; _v=$3
  mkdir -p "$(dirname "$_f")"
  touch "$_f"
  _t="$_f.tmp.$$"
  awk -v k="$_k" -v v="$_v" '
    $0 ~ "^[[:space:]]*"k"=" { print k"="v; found=1; next } { print }
    END { if (!found) print k"="v }
  ' "$_f" > "$_t" && mv "$_t" "$_f"
}
readkey() { readkey_file "$ENVFILE" "$1"; }
setkey()  { setkey_file  "$ENVFILE" "$1" "$2"; }

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

# --- automatic host-port selection --------------------------------------------------------
# The host lane solves this in _common.sh's claim_port, and we cannot call it: DESIGN
# CONSTRAINT 1 rules out python3, and claim_port's port_free IS a python socket bind. Nor may
# we imitate it with nc/lsof/ss — none of the three is guaranteed on a host whose only
# promised dependency is Docker, and all three answer the wrong question. "Is anything
# listening on 8787" is not "can the engine publish 127.0.0.1:8787": Docker Desktop's proxy
# holds ports in ways a host-side probe cannot see, so a false "free" would still need a retry
# underneath it and a false "busy" would skip a usable port.
#
# So compose is the probe. The thing that binds is the thing that tests, which makes the
# answer exact rather than inferred, needs nothing installed, and cannot drift out of sync
# with the compose file's own publish spec.

# port_taken <file> — true if a captured stderr is the port-collision failure and nothing else.
#
# Two spellings, because there are two: the engine's raw bind error, and compose's wording for
# a port held by another CONTAINER rather than by a host process.
port_taken() {
  grep -qi -e 'address already in use' -e 'port is already allocated' "$1"
}

# port_holder <port> — a human description of what is sitting on that port, or nothing.
#
# PURELY DIAGNOSTIC, and that is what licenses the tool-sniffing this file refuses everywhere
# else. The bind test above must be exact, so it may only use Docker; this one just makes the
# message useful, so a host without lsof simply gets the shorter sentence. It must never fail:
# every branch ends in return 0, because being unable to name the holder is not an error.
#
# Docker is asked first — it is the one tool ./cr may assume, and another checkout's container
# is the single likeliest holder of 8787 on a machine that has this project on it twice.
port_holder() {
  _hp=$1
  _c=$(docker ps --filter "publish=$_hp" --format '{{.Names}}' 2>/dev/null | head -1)
  if [ -n "$_c" ]; then printf 'the docker container %s' "$_c"; return 0; fi

  command -v lsof >/dev/null 2>&1 || return 0
  _pid=$(lsof -tiTCP:"$_hp" -sTCP:LISTEN 2>/dev/null | head -1)
  [ -n "$_pid" ] || return 0
  _cmd=$(ps -p "$_pid" -o comm= 2>/dev/null | sed 's|.*/||; s/^ *//')
  # The cwd identifies WHICH checkout, which is the thing you actually want to know when the
  # answer is "python3" — the host lane launches its hub with cwd = <repo>/ReelScraper.
  _cwd=$(lsof -a -p "$_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
  if [ -n "$_cwd" ]; then
    printf '%s (pid %s) running in %s' "${_cmd:-a process}" "$_pid" "$_cwd"
  else
    printf '%s (pid %s)' "${_cmd:-a process}" "$_pid"
  fi
  return 0
}

# up_on_free_port <envkey> <lo> <hi> <start> -- <compose args...>
#
# Exports <envkey>=<candidate>, runs `dc <args>`, and walks the range until the bind sticks.
# Sets PICKED_PORT to the winner and pins it in ReelScraper/.env if it moved.
#
# ONLY the port-collision failure is retried. That clause is load-bearing: a loop that treated
# every failure as "wrong port" would silently walk all 30 candidates on a missing image or on
# the [::1] bind failure of an IPv6-less host, and would then blame the ports for it. Every
# other failure replays its stderr and returns 1, so the caller's own die() message — which
# knows what it was trying to do — is the one you read. If the daemon ever rewords the error,
# the match stops firing and the behaviour degrades to exactly what it was before this
# function existed, never to a wrong action.
up_on_free_port() {
  _key=$1 _lo=$2 _hi=$3 _first=$4
  shift 4
  # `[ x ] && shift` would be a bug under `set -e`: a false test makes the AND-list return
  # non-zero, and a non-zero statement that is not a condition exits the script.
  if [ "${1:-}" = "--" ]; then shift; fi

  # The pinned value first, then the whole range with it skipped. Starting at $_first rather
  # than at $_lo is not a detail: a checkout already pinned to 8790 must retry 8790 before it
  # walks, or every run would drag it back down to 8787 and two clones would fight over one
  # port forever.
  _cands=$_first
  _n=$_lo
  while [ "$_n" -le "$_hi" ]; do
    [ "$_n" = "$_first" ] || _cands="$_cands $_n"
    _n=$((_n + 1))
  done

  # mktemp, not "$TMPDIR/cr-err.$$". A PID-derived name is predictable, and these are created
  # with `>` and `tee` — on a shared /tmp that is the shape of a symlink pre-creation attack.
  # Modern defaults (fs.protected_symlinks, a per-user TMPDIR on macOS) make it a non-event in
  # practice, but mktemp costs nothing and is what scripts/_common.sh and ./health already use.
  _err=$(mktemp) || die "cannot create a temporary file"
  _rc=$(mktemp)  || die "cannot create a temporary file"
  for _p in $_cands; do
    export "$_key=$_p"
    # stderr is tee'd, not swallowed: a cold start can take 180s and going silent for it would
    # be worse than the failure this function exists to fix. The fd dance sends stderr down the
    # pipe (2>&1 1>&3 with 3>&1 outside) while stdout goes straight through untouched. The
    # status lands in a file because `set -o pipefail` is not POSIX — the pipeline's own status
    # is tee's, which is always 0 — and it is captured inside an `if` because `set -e` would
    # otherwise kill this subshell the instant compose failed, before anything recorded why.
    { { if dc "$@" 2>&1 1>&3; then printf 0 >"$_rc"; else printf %s "$?" >"$_rc"; fi; } \
        | tee "$_err" >&2; } 3>&1

    if [ "$(cat "$_rc" 2>/dev/null)" = 0 ]; then
      PICKED_PORT=$_p
      rm -f "$_err" "$_rc"
      if [ "$_p" != "$_first" ]; then
        setkey "$_key" "$_p"
        say ""
        say "$_key $_first was already in use on this host, so ./cr moved to $_p and pinned"
        say "$_key=$_p in ReelScraper/.env — ./health, ./stop and the next ./cr up all read it"
        say "from there, so they agree. Nothing inside the container changed; it still serves"
        say "on its own fixed port, and only the host side of the publish moved."
      fi
      return 0
    fi

    port_taken "$_err" || { rm -f "$_err" "$_rc"; return 1; }
    _who=$(port_holder "$_p")
    if [ -n "$_who" ]; then
      say "./cr: host port $_p is held by $_who; trying the next free one in $_lo-$_hi..."
    else
      say "./cr: host port $_p is already taken; trying the next free one in $_lo-$_hi..."
    fi
  done

  rm -f "$_err" "$_rc"
  # 30 consecutive busy ports is not a situation to paper over with a random one — the same
  # call claim_port makes. A random port is unbookmarkable and no .env could point at it.
  die "every port from $_lo to $_hi is already in use on this host, so ./cr has nowhere to
publish. Free one, or set $_key in ReelScraper/.env to a port you know is free."
}

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

# wait_for_http <url> <seconds> — poll until the URL answers, or give up.
#
# `docker compose up -d` returns when the CONTAINER has started, which is not when the SERVER
# inside it is listening. For the docs container that gap is the whole mkdocs install-and-build
# — tens of seconds on a cold container — and opening a browser into it lands on a connection
# error that only clears if you reload by hand.
#
# Returns 0 as soon as it answers, 1 on timeout, 2 when this host has neither curl nor wget so
# the question cannot be asked at all. Callers must treat 2 as "could not verify" and open the
# browser anyway: unverifiable is not the same as broken, and refusing to open a browser on a
# machine that merely lacks curl would be a worse bug than the one this fixes.
wait_for_http() {
  _u=$1 _limit=$2 _n=0 _rc=0
  while [ "$_n" -lt "$_limit" ]; do
    # `if`, not `&&` — a bare failing AND-list is a `set -e` exit, and this one is expected to
    # fail on almost every pass.
    if host_get "$_u" 2; then return 0; else _rc=$?; fi
    [ "$_rc" -ne 2 ] || return 2
    _n=$((_n + 1))
    sleep 1
  done
  return 1
}

# register_producers — break the producer bootstrap deadlock, once, after the hub answers.
#
# Registration is LAZY BY DESIGN: an agent POSTs its manifest inside its own hub-connect
# preamble, so it registers only when its own CLI runs. That is fine for `analyze` and
# `analysis-engine` — the hub hardcodes their argv in STAGE_CMD (app.py) and the Board can
# launch them with nothing registered, after which they register themselves.
#
# It DEADLOCKS for a producer. The only two hub routes that would ever run SimilarContent are
# `propose` and `render`, and both resolve the working directory through `_producer_dir`
# (app.py), which refuses any agent that has not already registered. So on a fresh container
# the Board's Propose button could only ever answer
#     no registered producer declares proposes:true — start the producer agent once
# and the only escape was a terminal inside the container, which is the exact dependency this
# lane exists to remove. The host lane hid the same deadlock behind ./init's printed
# "cd SimilarContent && uv run cli.py propose" — an instruction, not a mechanism.
#
# `register` POSTs a manifest and exits: it reads no corpus, spends nothing, and the hub
# upserts by name, so running it on every `up` is idempotent and costs a subprocess.
#
# Never fatal. A producer that cannot register is a Propose button that explains itself; it is
# not a reason to fail a hub that is already serving.
register_producers() {
  for _p in SimilarContent; do
    dc exec -T hub sh -c "cd /app/$_p && exec uv run cli.py register" >/dev/null 2>&1 ||
      say "could not register producer $_p — Propose will say so until it registers"
  done
}

# The three agents that read the Gemini key, each from its OWN .env. The same set ./init
# writes, deliberately and in lockstep: one lane must not leave the other half-configured.
#
# AutoSearch is included even though discovery is free. Discovery standardised on this same
# key (AutoSearch/engine/gemini.py) and spends NOTHING with it: term expansion is gated behind
# `term_expansion_enabled`, default False, and returns early when off. Storing the key while
# it is idle is what turns the opt-in into one switch instead of a hunt for a file to edit.
GEMINI_AGENTS="AnalysisEngine SimilarContent AutoSearch"

# gemini_hint — one non-blocking line after a successful `up`, and nothing at all once a key
# is set. `up` must stay scriptable, so this NEVER prompts and never changes the exit status.
gemini_hint() {
  gemini_key_present && return 0
  say ""
  say "  No GEMINI_API_KEY yet. Scrape, analyze and media work without one; blueprints and"
  say "  captions/render need it.  Set it with:   ./cr keys --set"
  # The trap worth naming out loud: exporting the key in the host shell looks like it should
  # work and does nothing, because compose has no env_file on purpose. Someone who has done
  # that will otherwise read "no key" as a bug in ./cr.
  if [ -n "${GEMINI_API_KEY:-}" ]; then
    say "  (GEMINI_API_KEY is exported in this shell, but that does NOT reach the container —"
    say "   compose deliberately has no env_file, so keys are read from the per-agent .env"
    say "   files on the bind mount. './cr keys --set' writes them.)"
  fi
  return 0
}

gemini_key_present() {
  for _d in $GEMINI_AGENTS; do
    [ -z "$(readkey_file "$ROOT/$_d/.env" GEMINI_API_KEY)" ] || return 0
  done
  return 1
}

# set_gemini_key — the container lane's answer to ./init's key prompt.
#
# ./init cannot serve this lane at all: it calls check_python (init:38) and so needs python3 ON
# THE HOST, which is precisely the dependency the container lane promises you do not need. Nor
# can `./cr keys` do it — check-keys.py only verifies, it never writes. So before this existed
# a Docker-only user's only route to a working blueprint stage was to hand-author two .env
# files that nothing had told them about.
#
# THE KEY NEVER PASSES THROUGH DOCKER. It is read here, written to the two bind-mounted .env
# files here, and the container picks it up by READING THOSE FILES — the mechanism every
# presence check already uses. Handing it to `exec -e GEMINI_API_KEY=...` instead would put a
# live credential into the daemon's API call and its exec-inspect record: a smaller version of
# exactly the mistake the "THERE IS NO env_file: BLOCK" comment in docker-compose.yml exists to
# prevent. Bind mount in, file read out, nothing in between.
set_gemini_key() {
  say ""
  say "  Gemini API key — blueprints (AnalysisEngine), captions and image rendering"
  say "  (SimilarContent). Get one at https://aistudio.google.com/apikey"
  say "  Input is hidden. Leave it empty to skip."

  _old=""
  if [ -t 0 ]; then
    # POSIX sh has no `read -s`, so mute the terminal by hand — and restore it on Ctrl-C, or
    # an interrupted prompt leaves the user with an invisible shell.
    _old=$(stty -g 2>/dev/null) || _old=""
    if [ -n "$_old" ]; then
      trap 'stty "$_old" 2>/dev/null; printf "\n"; exit 130' INT
      stty -echo
    fi
  fi
  printf '  > ' >&2
  _key=""; read -r _key || true
  if [ -n "$_old" ]; then stty "$_old" 2>/dev/null || true; trap - INT; fi
  printf '\n' >&2

  if [ -z "$_key" ]; then
    say "skipped — no key written. Scrape, analyze and media do not need one; blueprints"
    say "and render do. Re-run './cr keys --set' whenever you have one."
    return 0
  fi

  for _d in $GEMINI_AGENTS; do
    setkey_file "$ROOT/$_d/.env" GEMINI_API_KEY "$_key"
  done
  say "saved to AnalysisEngine/.env, SimilarContent/.env and AutoSearch/.env (all gitignored)."
  say "discovery does NOT spend on it: term expansion is off by default. Turn it on from the"
  say "Dashboard (Discover) if you want widened search terms."
  say "the hub reads these at import, so run './cr down && ./cr up' before the next run."

  # Verify from INSIDE the container, which also proves the container's egress to Google —
  # the network path that actually matters here. Needs a running hub; when there is none, say
  # so rather than reporting a failure that is really "nothing to ask".
  _st=$(dc ps --format '{{.State}}' hub 2>/dev/null | head -1) || _st=""
  if [ "$_st" != "running" ]; then
    say "the hub is not running, so the key was not verified. './cr up' then './cr keys'."
    return 0
  fi
  printf '  checking the key with Google… ' >&2
  if dc exec -T hub python3 /app/scripts/check-keys.py --only gemini --quiet >/dev/null 2>&1; then
    say "✓"
  else
    say "✗"
    # Kept, not reverted — the same call ./init makes (init:149). An offline machine is a
    # failed CHECK, not a bad key, and making someone re-paste a good key is the worse error.
    say "the check did not pass. It is saved anyway: this may just be a network problem."
    say "Run './cr keys' for the full report, or './cr keys --set' again to replace it."
  fi
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
    # The guard is container_mode_guard in scripts/_common.sh, called by all six entry points
    # after their argument loops (so --help keeps working). TCR_FORCE_HOST=1 overrides it.
    if [ -z "$(readkey TCR_MODE)" ]; then
      setkey TCR_MODE container
      say "pinned TCR_MODE=container in ReelScraper/.env — the six bash scripts will now"
      say "redirect you to ./cr instead of guessing about a containerized hub."
    fi

    # A busy host port is handled here rather than reported: up_on_free_port walks 8787-8816,
    # pins the winner, and only the range being exhausted reaches a die(). PORT and url are
    # both recomputed from the winner — url is built before this case block runs, and the
    # host-side probe below and open_browser both read it, so a move that did not propagate
    # would poll a dead port and open a dead tab.
    up_on_free_port HUB_PORT 8787 8816 "$PORT" -- up -d hub || die "docker compose up failed.

If the message mentions a bind failure on '[::1]', this host has IPv6 disabled. Delete the
    - \"[::1]:\${HUB_PORT}:8787\"
line from docker/docker-compose.yml. DO NOT delete the '127.0.0.1:' prefix from the other
line — that one is the entire security boundary, and ./cr up opens http://127.0.0.1:\$PORT,
which does not need the IPv6 mapping.

If the image does not exist yet, run ./cr build."

    PORT=$PICKED_PORT
    url="http://127.0.0.1:$PORT"

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
        #
        # Producers register HERE, against the in-netns probe rather than the host one: the
        # deadlock this clears is inside the container, and it must be cleared even on the
        # `hg -eq 2` path below where the publish could not be verified from this host.
        register_producers
        host_get "$url/api/platforms" 3 && hg=0 || hg=$?
        if [ "$hg" -eq 0 ]; then
          say ""
          say "  The Cutting Room is up:  $url"
          say "  logs:  ./cr logs -f     stop: ./cr down"
          say "  verify the loopback boundary:  ./cr verify-loopback"
          gemini_hint
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
    # Same treatment as the hub, and needed here more: 8000 is the most contended port on any
    # developer machine, so this is the collision people actually hit.
    up_on_free_port DOCS_PORT 8000 8029 "$dport" -- --profile docs up -d docs \
      || die "docker compose could not start the docs container."
    dport=$PICKED_PORT
    durl="http://127.0.0.1:$dport"
    # Wait for mkdocs to actually serve before opening a browser at it. See wait_for_http.
    say "waiting for mkdocs to build (first run installs it; later runs are quick)..."
    _wr=0; wait_for_http "$durl" 120 || _wr=$?
    case "$_wr" in
      1) say "docs did not answer within 120s. Not opening a browser — see './cr logs docs'." ;;
      *) open_browser "$durl" ;;
    esac
    say "docs on $durl  (./cr down stops it)"
    ;;

  shell)  dc exec hub /bin/sh ;;

  keys)
    if [ "${1:-}" = "--set" ]; then
      set_gemini_key
      exit 0
    fi
    # check-keys.py runs INSIDE the container on purpose: it then proves the CONTAINER's
    # egress path to generativelanguage.googleapis.com / api.anthropic.com, which is the one
    # that matters. Running it on the host proves the wrong network.
    dc exec hub python3 /app/scripts/check-keys.py "$@"
    ;;

  # -h/--help/help are handled at the top of the file, before the Docker probes.

  *) die "unknown command '$cmd' — try ./cr help" ;;
esac
