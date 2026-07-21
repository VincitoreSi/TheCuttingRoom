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
  local log; log="$(mktemp -t vp-setup)"
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
