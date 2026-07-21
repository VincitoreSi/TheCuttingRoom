#!/usr/bin/env bash
# scripts/_common.sh — shared plumbing for the ./init, ./demo and ./docsite entry points.
#
# Sourced, never executed. Keeps the three launchers consistent about what they check,
# how they talk, and how they pick a port.

# ---------------------------------------------------------------- output
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  _B=$'\033[1m'; _D=$'\033[2m'; _R=$'\033[0m'
  _G=$'\033[32m'; _Y=$'\033[33m'; _C=$'\033[36m'; _E=$'\033[31m'
else
  _B=""; _D=""; _R=""; _G=""; _Y=""; _C=""; _E=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$_C" "$_R" "$_B" "$*" "$_R"; }
say()  { printf '    %s\n' "$*"; }
ok()   { printf '    %s✓%s %s\n' "$_G" "$_R" "$*"; }
warn() { printf '    %s!%s %s\n' "$_Y" "$_R" "$*"; }
die()  { printf '\n%serror:%s %s\n\n' "$_E" "$_R" "$*" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites
have() { command -v "$1" >/dev/null 2>&1; }

# require <binary> <why> [install hint]
require() {
  if have "$1"; then
    ok "$1 $( "$1" --version 2>/dev/null | head -1 | cut -c1-40 )"
  else
    die "$1 is required ($2).${3:+ Install: $3}"
  fi
}

# optional <binary> <what breaks without it> [install hint]
optional() {
  if have "$1"; then
    ok "$1"
  else
    warn "$1 not found — $2.${3:+ Install: $3}"
  fi
}

check_python() {
  have python3 || die "python3 is required (>= 3.10). Install: https://www.python.org"
  python3 - <<'PY' || die "python3 >= 3.10 is required (found $(python3 -V 2>&1))"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
  ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)"
}

check_node() {
  have node || die "node >= 20 is required (the Dashboard). Install: https://nodejs.org"
  local major; major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 20 ] || die "node >= 20 is required (found $(node -v))"
  ok "node $(node -v)"
}

# ---------------------------------------------------------------- ports
# free_port [preferred] — echo a usable port. Prefers the argument, else asks the OS.
free_port() {
  python3 - "$@" <<'PY'
import socket, sys

def free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port)); return True
        except OSError:
            return False

pref = int(sys.argv[1]) if len(sys.argv) > 1 else 0
if pref and free(pref):
    print(pref)
else:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])
PY
}

# hub_responding <url> — true if a hub is already serving there
hub_responding() { curl -sf -o /dev/null --max-time 2 "$1/api/platforms" 2>/dev/null; }

# hub_state <port> — classify whoever is listening: ours | stale | foreign
#
# "Something answered on 8787" is not "our hub is up", and answering is not the same as
# running our code. Python imports a module once and serves it from memory for the life of
# the process, so a hub started before a `git pull` (or before the tree was re-cloned over
# the top) keeps serving the OLD API with nothing to show for it. The Dashboard is served
# from disk and so IS current, which produces the worst version of the bug: a new frontend
# asking a stale backend for fields it has never heard of, and rendering `undefined`.
#
#   ours     this checkout, running the code that is on disk — reuse it
#   stale    this checkout, but the process predates the files — restart it
#   foreign  a different checkout's hub — leave it alone and take another port
#
# The hub identifies itself over HTTP (`GET /api/hub`), which is authoritative: it reports
# the ROOT it imported and whether its sources have changed underneath it. A hub too old to
# have that route is, by definition, older than this code — that 404 IS the answer. In that
# case fall back to the listener's cwd to tell "old hub of ours" from "someone else's".
hub_state() {
  local port="$1" body cwd
  body="$(curl -sf --max-time 2 "http://127.0.0.1:$port/api/hub" 2>/dev/null || true)"

  if [ -n "$body" ]; then
    # The payload goes in as an ARGUMENT, not on stdin: stdin is already carrying the
    # program itself (`python3 -` + heredoc), and piping the body in as well concatenates
    # the two into one unparseable blob.
    ROOT="$ROOT" python3 - "$body" <<'PY'
import json, os, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    print("foreign"); sys.exit(0)                    # answering nonsense: not ours to touch
root = os.path.realpath(d.get("root") or "")
mine = os.path.realpath(os.path.join(os.environ["ROOT"], "ReelScraper"))
print("foreign" if root != mine else ("stale" if d.get("stale") else "ours"))
PY
    return 0
  fi

  # No /api/hub. Either a hub older than that route, or not one of ours at all — this is
  # also the answer for a port held by some unrelated program, which claim_port must not
  # take. Only a listener whose working directory is inside THIS checkout is claimable;
  # everything else is "not ours", which is the safe way to be wrong.
  cwd="$(hub_cwd "$port" 2>/dev/null || true)"
  case "$cwd" in
    "$ROOT"|"$ROOT"/*) printf 'stale\n' ;;
    *)                 printf 'foreign\n' ;;
  esac
}

# hub_cwd <port> — echo the working directory of the process LISTENING on that port.
#
# "A hub is answering on 8787" does not mean it is THIS checkout's hub. Anyone with a second
# clone (or a worktree) can have one running, and reusing it silently tests the wrong tree —
# `./health --live` once reported two failures that belonged to an unrelated checkout whose
# Dashboard had never been built. The hub is always launched with cwd = <repo>/ReelScraper
# (start_hub below, and `uv run cli.py start` by hand), so the cwd identifies the checkout.
#
# Prints nothing and returns 1 when it cannot be determined — no lsof, no /proc, or the
# process belongs to another user. Callers must treat "unknown" as "cannot verify", never as
# "foreign", or a hardened box would stop reusing its own hub.
hub_cwd() {
  local port="$1" pid
  pid="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -n "$pid" ] || return 1
  proc_cwd "$pid"
}

# proc_cwd <pid> — the working directory of any process, or rc 1 if it cannot be read.
# -Fn prints one field per line prefixed by its type; the cwd row starts with `n`. Works on
# macOS and Linux lsof alike. /proc is the fallback when lsof is absent.
proc_cwd() {
  local pid="$1" cwd
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  [ -n "$cwd" ] || cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null)"
  [ -n "$cwd" ] || return 1
  printf '%s\n' "$cwd"
}

# owned_pids — every process belonging to THIS checkout, newest first.
#
# Matched by working directory, not by command line: the hub is launched as
# `uvicorn api.app:app` with cwd = <repo>/ReelScraper and the repo path never appears in
# argv, so a pattern match would either miss it or — far worse — match an identically-named
# process in a different clone. Three checkouts of this repo on one machine is normal, and
# stopping the wrong one is exactly the failure this project has already been bitten by.
owned_pids() {
  local pids pid cwd
  pids="$(pgrep -f 'uvicorn|cli\.py|scrape\.py|download_media\.py|run\.py|mkdocs|vite' 2>/dev/null)"
  [ -n "$pids" ] || return 0
  for pid in $pids; do
    [ "$pid" = "$$" ] && continue
    cwd="$(proc_cwd "$pid" 2>/dev/null)" || continue
    case "$cwd" in
      "$ROOT"|"$ROOT"/*) printf '%s\n' "$pid" ;;
    esac
  done
}

# ---------------------------------------------------------------- git, honestly
# git_state — can git answer questions about this tree? Echoes exactly one word:
#
#   ok       a readable work tree
#   none     no .git here at all — a downloaded archive or an extracted tarball, which is a
#            supported way to run this project and must not be treated as an error
#   broken   .git IS here and git still could not read it
#
# The three-way split is the whole point, and `broken` is why this function exists. Git
# exits 128 — indistinguishable from a plain "no" to every `if git … 2>/dev/null` — for
# "detected dubious ownership in repository at …", which is what git says about a checkout
# owned by a different uid than the one running it. That is the normal state of a bind
# mount inside a container, and it is not rare: it is what you get the first time you run
# any of this from a container against the host's clone.
#
# Two callers used to collapse `broken` into `none`, with real consequences:
#   • ./clean read "git failed" as "git does not track this" and archived and DELETED
#     tracked files, including the .gitkeep whose entire purpose is to survive ./clean.
#   • ./health turned four invariants into skips and still printed HEALTHY.
# Both now fail closed. `none` still skips, because a tarball genuinely has no answer.
git_state() {
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'ok\n'
  elif [ -e "$ROOT/.git" ]; then
    printf 'broken\n'
  else
    printf 'none\n'
  fi
}

# git_why — the first line of whatever git actually said, for the message a human reads.
git_why() {
  local msg
  msg="$(git -C "$ROOT" rev-parse --is-inside-work-tree 2>&1 | head -1)"
  printf '%s\n' "${msg:-git is not on PATH}"
}

# data_paths — every path holding generated or scraped working data.
#
# ONE list, used by ./clean and by `./init --reset`, because they had drifted: init's
# reset missed the raw scrape dumps entirely, so a "reset" install still had
# reels_raw.json on disk and the next scrape skipped every creator already in it.
#
# Deliberately EXCLUDES the per-agent .env files. They hold API keys: wiping them would
# make "start from scratch" mean "re-enter your credentials", and archiving them would put
# live secrets in a zip.
data_paths() {
  cat <<'PATHS'
ReelScraper/media
ReelScraper/renders
ReelScraper/analysis
ReelScraper/studio/instagram
ReelScraper/studio/x
ReelScraper/studio/youtube
ReelScraper/evals
ReelScraper/logs
ReelScraper/producers
ReelScraper/discovery
ReelScraper/config/agents
ReelScraper/config/pipeline_schedule.json
ReelScraper/memory/instagram/content.db
ReelScraper/memory/x/content.db
ReelScraper/memory/youtube/content.db
ReelScraper/memory/shared/insights.jsonl
ReelScraper/memory/shared/INSIGHTS.md
demo-data/data
PATHS
  # Per-platform scrape + score output. Globbed rather than listed so a new platform is
  # covered the day its directory appears.
  local d
  for d in "$ROOT"/ReelScraper/platforms/*/; do
    [ -d "$d" ] || continue
    local p="ReelScraper/platforms/$(basename "$d")"
    printf '%s\n' "$p/content.json" "$p/profiles_meta.json"
    local f
    for f in "$d"*_raw*.json "$d"*.xlsx "$d"*.csv; do
      [ -e "$f" ] && printf '%s/%s\n' "$p" "$(basename "$f")"
    done
  done
}

# Same list, de-duplicated. The per-platform globs legitimately re-emit files also named
# explicitly above them (virality_reels.csv is matched by *.csv), and a duplicate makes
# ./clean report the same file twice and archive it twice.
data_paths_unique() { data_paths | awk 'NF && !seen[$0]++'; }

# wait_for_hub <url> [seconds] — poll until it answers
wait_for_hub() {
  local url="$1" limit="${2:-45}" i=0
  while [ "$i" -lt "$limit" ]; do
    hub_responding "$url" && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

open_browser() {
  local url="$1"
  if have open; then open "$url" >/dev/null 2>&1 || true
  elif have xdg-open; then xdg-open "$url" >/dev/null 2>&1 || true
  fi
}

# ---------------------------------------------------------------- secrets
# write_key <VAR> <value> <env file> — persist a secret without ever printing its value.
#   • already has a non-empty value  -> left alone (idempotent re-runs)
#   • present but empty (a .env.example placeholder like `GEMINI_API_KEY=`) -> filled in
#     place, keeping the surrounding template. Without this, a key entered on a later run
#     is silently dropped, because the empty placeholder counts as "present".
#   • absent -> appended
write_key() {
  local var="$1" val="$2" file="$3"
  [ -n "$val" ] || return 0
  mkdir -p "$(dirname "$file")"; touch "$file"
  if [ -n "$(read_key "$var" "$file")" ]; then
    say "${var} already set in ${file#"$ROOT"/} — left alone"
  elif grep -q "^[[:space:]]*${var}=" "$file" 2>/dev/null; then
    # Fill the empty placeholder. awk with a temp file + mv, not sed -i, because sed -i's
    # syntax differs between macOS (BSD) and Linux (GNU); the value goes through -v so no
    # character in it is treated as awk syntax. API keys are [A-Za-z0-9_-] — no backslashes.
    local tmp; tmp="$(mktemp)"
    awk -v k="$var" -v v="$val" '
      !filled && $0 ~ "^[[:space:]]*" k "=" { print k "=" v; filled=1; next } { print }
    ' "$file" > "$tmp" && mv "$tmp" "$file"
    ok "set ${var} in ${file#"$ROOT"/}"
  else
    printf '%s=%s\n' "$var" "$val" >> "$file"
    ok "wrote ${var} to ${file#"$ROOT"/}"
  fi
}

# set_key <VAR> <value> <env file> — like write_key, but CORRECTS an existing value.
#
# write_key's "already set, left alone" rule is right for secrets and wrong for settings
# that this checkout owns: if the hub moves to another port, a BACKEND_API left alone is a
# stale pointer at whatever now answers on the old one — which, with two clones on one
# machine, is the other niche's hub. Silent, and it writes real data to the wrong corpus.
set_key() {
  local var="$1" val="$2" file="$3" tmp
  [ -n "$val" ] || return 0
  mkdir -p "$(dirname "$file")"; touch "$file"
  if grep -q "^[[:space:]]*#\{0,1\}[[:space:]]*${var}=" "$file" 2>/dev/null; then
    # Rewrites a commented-out line too (`# BACKEND_API=…` is how the examples ship it),
    # so the setting lands where the template documents it instead of orphaned at the end.
    tmp="$(mktemp)"
    awk -v k="$var" -v v="$val" '
      !filled && $0 ~ "^[[:space:]]*#?[[:space:]]*" k "=" { print k "=" v; filled=1; next } { print }
    ' "$file" > "$tmp" && mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$var" "$val" >> "$file"
  fi
}

# ---------------------------------------------------------------- one machine, many clones
# Two checkouts of this repo are a normal setup — one niche per clone — and they must not
# be able to touch each other. Nothing in this project writes outside its own directory, so
# the ONLY thing two clones share is the network: the port the hub listens on, and the
# BACKEND_API every sibling agent dials. Get those two right and the isolation is total.

# port_free <port> — true if we could bind it on the loopback right now.
port_free() {
  python3 - "$1" <<'PY' >/dev/null 2>&1
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

# claim_port — the port THIS checkout owns. Decided once, then remembered forever.
#
# `free_port` asks the OS for any free port, which is fine for a one-shot smoke test and
# useless as an address: it changes on every restart, so nothing can be bookmarked and no
# .env can point at it. A second clone needs a port that is stable for as long as the
# checkout exists, so the pin is written to ReelScraper/.env (gitignored — per install,
# never shared) and honoured from then on.
#
# Scanning upward from 8787 keeps the numbers memorable: first clone 8787, second 8788.
# A port already held by OUR OWN hub counts as claimable — re-running ./init against a live
# hub must reclaim the same address, not walk to the next one every time.
claim_port() {
  local envf="$ROOT/ReelScraper/.env" pinned p
  pinned="$(read_key HUB_PORT "$envf")"
  case "$pinned" in
    ''|*[!0-9]*) ;;                       # unset or not a number: fall through and claim
    *)
      # Honour the pin — unless another checkout got there first, in which case it is a
      # pointer to somebody else's hub and re-claiming beats handing over this niche's data.
      if port_free "$pinned" || [ "$(hub_state "$pinned")" != "foreign" ]; then
        printf '%s\n' "$pinned"; return 0
      fi
      ;;
  esac
  for p in $(seq 8787 8816); do
    if port_free "$p"; then
      set_key HUB_PORT "$p" "$envf"; printf '%s\n' "$p"; return 0
    fi
    case "$(hub_state "$p")" in
      ours|stale) set_key HUB_PORT "$p" "$envf"; printf '%s\n' "$p"; return 0 ;;
    esac
  done
  # 30 consecutive ports busy is not a situation to paper over with a random one.
  die "no free port in 8787-8816 — set HUB_PORT in ReelScraper/.env to pick one yourself"
}

# sync_backend_api <url> — point every agent IN THIS CHECKOUT at this checkout's hub.
#
# The hub exports BACKEND_API for the stages it spawns itself (ReelScraper/cli.py), so those
# are always correct. Agents run BY HAND are not — `cd SimilarContent && uv run cli.py
# propose` is in the README, and it resolves BACKEND_API from its own .env, which ships
# pointing at 8787. On a second clone that is the FIRST clone's hub: the proposal is written
# to another niche's studio, under that niche's corpus, and nothing anywhere reports an
# error. Writing the real address into each .env is what stops that.
sync_backend_api() {
  local url="$1" c
  for c in AnalysisEngine AutoSearch SimilarContent; do
    [ -d "$ROOT/$c" ] || continue
    set_key BACKEND_API "$url" "$ROOT/$c/.env"
  done
  [ -f "$ROOT/.env" ] && set_key BACKEND_API "$url" "$ROOT/.env"
  return 0
}

# read_key <VAR> <env file> — echo VAR's value from a KEY=VALUE .env (nothing if unset or
# blank). Mirrors scripts/check-keys.py's reader: strips surrounding quotes and whitespace
# so a hand-pasted trailing space does not read as a different key. The value is only ever
# compared or passed onward from here, never printed.
read_key() {
  local var="$1" file="$2" val
  [ -f "$file" ] || return 0
  val="$(sed -n "s/^[[:space:]]*${var}=//p" "$file" 2>/dev/null | tail -1)"
  val="${val%$'\r'}"                        # strip a Windows carriage return
  val="${val#"${val%%[![:space:]]*}"}"      # strip leading whitespace
  val="${val%"${val##*[![:space:]]}"}"      # strip trailing whitespace
  val="${val#\"}"; val="${val%\"}"          # strip one pair of double quotes
  val="${val#\'}"; val="${val%\'}"          # or single quotes
  printf '%s' "$val"
}

# container_mode_guard <script> [<./cr equivalent>] — stop a host-lane run of a containerized
# checkout, and say what to run instead.
#
# The two keys are written by different parties and mean different things, which is why the
# condition needs both. `./cr up` pins TCR_MODE=container into ReelScraper/.env — that file is
# on the bind mount, so it is EQUALLY visible from inside the container and cannot on its own
# distinguish the lanes. TCR_CONTAINER=1 is set by the image and by every compose service, and
# is true only inside. So "container mode AND not in the container" is exactly "the human is on
# the host, but this checkout's hub lives in a container".
#
# Getting this wrong is not cosmetic. ./stop identifies the hub by its working directory, which
# is meaningless across a container boundary: on the host it finds nothing, reports success, and
# leaves the container running. ./init would claim a second port and start a second hub against
# the same bind-mounted data. ./clean would delete data the container is mid-write on.
#
# TCR_FORCE_HOST=1 is the escape hatch, and it exists because this guard is a heuristic over a
# key a previous command wrote: a checkout can be legitimately switched back to the host lane,
# and a stale TCR_MODE must never be able to lock someone out of their own scripts.
container_mode_guard() {
  local script="$1" alt="${2:-}"
  [ -z "${TCR_CONTAINER:-}" ]  || return 0      # inside the container: this IS the right lane
  [ -z "${TCR_FORCE_HOST:-}" ] || return 0      # explicit opt-out
  [ "$(read_key TCR_MODE "$ROOT/ReelScraper/.env")" = "container" ] || return 0

  if [ -n "$alt" ]; then
    die "this checkout is in container mode (TCR_MODE=container in ReelScraper/.env), and
./$script is the host-lane script. It cannot see, control or safely share state with a hub
running in a container.

    run instead:  $alt

If you have gone back to running on the host, clear TCR_MODE from ReelScraper/.env, or set
TCR_FORCE_HOST=1 for this one command."
  fi
  die "this checkout is in container mode (TCR_MODE=container in ReelScraper/.env), and
./$script has no ./cr equivalent yet.

    run it inside:  ./cr shell   then  ./$script

Running it from the host would act on files a container is using. If you have gone back to
running on the host, clear TCR_MODE from ReelScraper/.env, or set TCR_FORCE_HOST=1 for this
one command."
}

# prompt_secret <VAR> <prompt> — read without echoing; empty is allowed (skip)
#
# The prompt MUST go to stderr, not stdout. This function is called as `X="$(prompt_secret
# …)"`, and command substitution captures stdout — so a prompt printed to stdout is
# swallowed into the returned value and NEVER shown, leaving the user staring at a blinking
# cursor with no idea what is being asked, while the key they type comes back glued to the
# prompt text. stderr still reaches the terminal, so only the key is echoed to stdout.
prompt_secret() {
  local var="$1" prompt="$2" val=""
  if [ ! -t 0 ]; then return 0; fi          # non-interactive: skip silently
  printf '    %s%s%s\n    (input hidden — press Enter to skip)\n    > ' "$_B" "$prompt" "$_R" >&2
  read -rs val; printf '\n' >&2
  printf '%s' "$val"
}

# ---------------------------------------------------------------- setup steps
# spun <label> <command…> — run a slow command, showing elapsed seconds while it works.
#
# The first run downloads a few hundred MB of npm and Python wheels and can take a couple
# of minutes on a cold cache. Silence for that long is indistinguishable from a hang, and
# the honest fix is to show that time is passing and something is still working.
#
# Falls back to a plain line when stdout is not a TTY (CI, piped logs), where a redrawing
# counter would just emit thousands of junk lines.
spun() {
  local label="$1"; shift
  # The X's are load-bearing. BSD/macOS `mktemp -t` takes a bare PREFIX and appends its own
  # randomness, so `-t vp-setup` works here and has always worked here. GNU coreutils treats
  # the argument as a TEMPLATE and refuses one with fewer than three trailing X's:
  # `mktemp: too few X's in template 'vp-setup'` (verified, coreutils 9.1). This form is the
  # one both accept.
  #
  # It failed quietly rather than loudly, which is why it survived: spun is always the left
  # operand of a `||` list, so bash suspends errexit for its whole body. $log ended up empty,
  # `>""` failed, spun returned 1, and sync_python_projects folded four real sync failures
  # into `warn "see: cd $p && uv sync"` before the run finally died in build_dashboard —
  # blaming npm for a broken mktemp.
  local log; log="$(mktemp -t vp-setup.XXXXXX)" || die "could not create a temp file for '$label'"
  if [ ! -t 1 ]; then
    printf '    %s… ' "$label"
    if "$@" >"$log" 2>&1; then printf 'done\n'; rm -f "$log"; return 0; fi
    printf 'FAILED\n'; tail -20 "$log" >&2; rm -f "$log"; return 1
  fi

  "$@" >"$log" 2>&1 &
  local pid=$! frames='|/-\' i=0 t=0
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r    %s %s  %ds ' "${frames:i++%4:1}" "$label" "$t"
    sleep 1; t=$((t + 1))
  done
  wait "$pid"; local rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '\r    %s✓%s %s  %ds%s\n' "$_G" "$_R" "$label" "$t" "$(printf '%*s' 12 '')"
    rm -f "$log"; return 0
  fi
  printf '\r    %s✗%s %s  (failed after %ds)%s\n' "$_E" "$_R" "$label" "$t" "$(printf '%*s' 8 '')"
  tail -20 "$log" >&2; rm -f "$log"; return 1
}

sync_python_projects() {
  local p
  for p in ReelScraper AnalysisEngine AutoSearch SimilarContent; do
    [ -f "$ROOT/$p/pyproject.toml" ] || continue
    spun "syncing $p" sh -c "cd '$ROOT/$p' && uv sync --quiet" \
      || warn "see: cd $p && uv sync"
  done
}

build_dashboard() {
  # package.json, not just the directory — a Dashboard/ with no manifest means npm ci
  # dies with a confusing error instead of us skipping cleanly.
  [ -f "$ROOT/Dashboard/package.json" ] || { warn "no Dashboard/package.json — skipping build"; return 0; }
  if [ ! -d "$ROOT/Dashboard/node_modules" ] || [ "${1:-}" = "--force" ]; then
    # The slowest thing in a first run by a wide margin: a cold npm cache means a few
    # hundred MB over the network. Warn before, not after.
    say "${_D}first run — this downloads dependencies and can take a minute or two${_R}"
    spun "installing Dashboard dependencies (npm ci)" \
      sh -c "cd '$ROOT/Dashboard' && npm ci --no-audit --no-fund" \
      || die "npm ci failed — run it in Dashboard/ to see why"
  fi
  spun "building the Dashboard" sh -c "cd '$ROOT/Dashboard' && npm run deploy" \
    || die "Dashboard build failed — run 'npm run deploy' in Dashboard/ to see why"
  ok "Dashboard built — the hub serves it at /"
}

# start_hub <port> — launch in the background; sets $HUB_URL on success.
#
# Deliberately NOT `URL=$(start_hub …)`. Command substitution reads until every writer
# closes the pipe, and the backgrounded hub keeps a descriptor open for its whole life —
# so the caller would block forever on a server that had already started successfully.
# Returning through a global is the fix; `</dev/null` and the explicit redirects keep the
# child off the parent's stdio entirely.
HUB_URL=""
start_hub() {
  # Declared separately, not as one `local a=… b=$a`: bash does not guarantee the earlier
  # assignment is visible to the later one in a single declaration, and under `set -u` the
  # self-reference aborts the script.
  local port="$1"
  local log="$ROOT/.hub.log"
  local url="http://127.0.0.1:$port"
  # The pid file is written by cli.py, NOT here. `uv run` spawns the interpreter and exits,
  # so `$!` is the wrapper's pid — dead within moments while the server keeps listening, so
  # `kill $(cat .hub.pid)` would silently do nothing. cli.py records os.getpid() instead.
  rm -f "$ROOT/.hub.pid"
  ( cd "$ROOT/ReelScraper" \
      && HUB_PORT="$port" nohup uv run cli.py start --no-browser \
           </dev/null >"$log" 2>&1 & )
  if ! wait_for_hub "$url" 60; then
    printf '\n%s--- last 25 lines of %s ---%s\n' "$_D" "${log#"$ROOT"/}" "$_R" >&2
    tail -25 "$log" >&2
    die "the hub did not come up on $url"
  fi
  if [ -s "$ROOT/.hub.pid" ]; then
    # Advisory only: a SIGKILL or a crash leaves it stale, so confirm the pid is alive
    # before telling anyone to trust it.
    kill -0 "$(cat "$ROOT/.hub.pid")" 2>/dev/null \
      || warn "stale .hub.pid — stop the hub with: lsof -ti tcp:${port} -sTCP:LISTEN | xargs kill"
  else
    warn "hub is up but wrote no pid file — stop it with: lsof -ti tcp:${port} -sTCP:LISTEN | xargs kill"
  fi
  HUB_URL="$url"
}

banner() {
  local url="$1" what="$2"
  printf '\n  %s%s%s\n' "$_B" "$what" "$_R"
  printf '  %sdashboard%s  %s\n' "$_D" "$_R" "$url"
  printf '  %sapi docs%s   %s/docs\n' "$_D" "$_R" "$url"
  # Only advertise the docs route if it is genuinely being served. The hub mounts
  # documentation/site at STARTUP, so a site built after the hub booted is not reachable
  # until a restart — printing the link regardless sends people to a 404.
  if [ -d "$ROOT/documentation/site" ]; then
    if curl -sf -o /dev/null --max-time 2 "$url/documentation/" 2>/dev/null; then
      printf '  %sdocs%s       %s/documentation\n' "$_D" "$_R" "$url"
    else
      printf '  %sdocs%s       built, but restart the hub to serve it at /documentation\n' \
        "$_D" "$_R"
    fi
  fi
  # Stop by PORT, not by pid file. uvicorn exits the process without unwinding, so neither
  # a `finally` nor an atexit hook clears .hub.pid — it goes stale on every shutdown, and a
  # recycled pid would make `kill $(cat .hub.pid)` terminate something unrelated. The port
  # is unambiguous and always current.
  #
  # -sTCP:LISTEN is not optional: a bare `lsof -ti tcp:8787` also lists every CLIENT with a
  # connection open to that port, so with the dashboard open in a browser it returns Safari's
  # and Chrome's networking processes alongside the hub — and kills them too.
  local port="${url##*:}"
  printf '\n  %sstop:%s lsof -ti tcp:%s -sTCP:LISTEN | xargs kill   %slog:%s .hub.log\n\n' \
    "$_D" "$_R" "$port" "$_D" "$_R"
}
