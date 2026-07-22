---
title: Run it in Docker
description: The container lane — one image, one container, `./cr` as the only host-side command. What it is for, first run on macOS/Linux/WSL2, the subcommand reference, secrets, volumes, upgrades, and the loopback boundary that keeps an unauthenticated hub off your LAN.
---

# Run it in Docker

There are two ways to run The Cutting Room. The **host lane** is the six bash entry points
(`./init`, `./demo`, `./stop`, `./clean`, `./health`, `./docsite`) documented in
[Entry Points](entry-points.md) — it needs uv, Python, Node, npm and curl on your machine.
The **container lane** is one image, one container, and a single host-side launcher:

```sh
./cr up
```

The host needs **Docker and nothing else**. No uv, no Python, no Node, no ffmpeg.

## What this is for, and who should use it

The goal is one sentence: *a person who has installed nothing but Docker can clone this repo
and run the pipeline, on macOS, Linux or Windows.* Everything in the container lane is
justified by that and by nothing else.

Be clear about the size of the delta, especially on Windows:

!!! note "On Windows, WSL2 already solves most of this"
    Docker Desktop for Windows *requires* WSL2 anyway. Every Windows blocker the six entry
    points have — bash, `lsof`, `pgrep`, `ps -o command=`, `mktemp`, `nohup`, shebangs,
    `.venv/bin/python` — is resolved by **WSL2 alone**. "Clone inside WSL2 and run `./init`"
    is a one-paragraph fix with no new artifacts.

    What the container adds on top of that is real but smaller than "containers fix Windows"
    implies: a **guaranteed ffmpeg**, a **pinned toolchain** (Python 3.12, uv 0.11.29, Node
    20, ffmpeg n7.1.3, all resolved to commits), and **not having to install uv/Node/Python**
    at all.

Use the container lane if you want a zero-install run, a reproducible toolchain, or ffmpeg
without touching your system package manager. Stay on the host lane if you are developing —
see [what you give up](#known-sharp-edges).

## Prerequisites and first run

All platforms start the same way:

```sh
git clone <repo> TheCuttingRoom
cd TheCuttingRoom
```

`./cr help` works on a machine with **no Docker at all** — it is the first thing to run if you
are trying to find out what you need. Everything else probes the Docker daemon first and
refuses with an install link if it is missing.

`./cr` enforces two floors: **Compose ≥ 2.20** (the compose file uses the top-level `name:`
key) and a **Docker Engine floor** — the engine version matters for the loopback boundary, see
[Security](#security-the-loopback-boundary).

### macOS (Intel or Apple Silicon)

Docker Desktop 4.x. Nothing else.

```sh
./cr build
./cr up
```

`./cr up` starts one container, polls until `/api/platforms` answers **inside** the container,
then does one host-side GET of the published port — so it can tell "the hub is broken" from
"the publish is broken" — and opens `http://127.0.0.1:$HUB_PORT` in your browser. `HUB_PORT`
is read from `ReelScraper/.env` and defaults to **8787**.

**You never have to pick that port yourself.** If the host cannot publish on it — another
checkout's container, a host-lane hub, or some unrelated program — `./cr up` walks 8787–8816,
starts on the first one that binds, and pins it in `ReelScraper/.env` so `./health`, `./stop`,
`./cr verify-loopback` and the next `./cr up` all agree. It says so when it moves:

```
./cr: host port 8787 is held by python3 (pid 68618) running in /Users/you/dev/TheCuttingRoom/ReelScraper; trying the next free one in 8787-8816...

HUB_PORT 8787 was already in use on this host, so ./cr moved to 8788 and pinned
HUB_PORT=8788 in ReelScraper/.env — ./health, ./stop and the next ./cr up all read it
from there, so they agree. Nothing inside the container changed; it still serves
on its own fixed port, and only the host side of the publish moved.
```

Only the **host** side of the publish moves; the container always serves on 8787 internally.
This is the same scan-and-pin `claim_port` does for the host lane, over the same range and the
same `.env` key, so the two lanes cannot disagree about who owns which port. To force a
specific port, set `HUB_PORT` in `ReelScraper/.env` yourself — a value that binds is always
honoured, and never silently moved off. `./cr docsite` does the same over 8000–8029.

### Linux (Docker Engine, not Desktop)

Follow <https://docs.docker.com/engine/install/> for your distro.

!!! warning "`apt install docker.io` is not enough"
    It gives you an engine but **not** the compose plugin, and `docker-compose-plugin` is
    published by download.docker.com, not by Debian or Ubuntu — on a stock box that line
    fails with "Unable to locate package". Ubuntu ≥ 23.10 does ship `docker-compose-v2`.
    `docker-compose` with a hyphen (v1) is end-of-life and is not supported here.

```sh
sudo usermod -aG docker "$USER" && newgrp docker
./cr build
./cr up
```

On Linux `./cr` sets `TCR_UID`/`TCR_GID` from `id -u`/`id -g`, so files the container writes
into `media/` and `logs/` stay owned by you and your own `./clean` can delete them. Docker
Desktop does its own uid translation, so `./cr` leaves them at 1000 there.

Read the [Linux firewall note](#security-the-loopback-boundary) before you go further: a host
firewall does **not** protect a published Docker port.

### Windows 11 — WSL2 only

**The supported layout is a clone inside the WSL2 filesystem.** Not `C:\Users\…`, not
`/mnt/c/…`.

```powershell
wsl --install                 # if you don't already have WSL2
# then install Docker Desktop and enable the WSL2 backend
```

Then, from a WSL2 shell:

```sh
cd ~                          # the WSL2 filesystem, NOT /mnt/c
git clone <repo> TheCuttingRoom
cd TheCuttingRoom
./cr build
./cr up
```

!!! danger "There is no `cr.cmd`, and there will not be one"
    Windows support is WSL2 only. `./cr` is run from a WSL2 bash/sh; a native NTFS clone is
    unsupported and untested.

    A cmd.exe/PowerShell twin would have to bind-mount `\\wsl.localhost\…` paths through the
    **Windows** engine context (not the Linux one that "clone inside WSL2" exists to use) and
    derive the per-checkout compose project name without `sha256sum` — `certutil -hashfile`
    hashes files, not strings, so a Windows-form path would hash differently from the WSL path
    `./cr` pins. That is two independent ways to silently fork one checkout into two compose
    projects with two sets of volumes. If a Windows-side entry point is ever wanted, the only
    defensible shape is a five-line `wsl.exe -d <distro> -- ./cr %*` shim that cannot drift.

Why inside WSL2: bind-mounting an NTFS path reaches the container over 9p/drvfs and is roughly
5–20× slower than macOS VirtioFS for metadata. The masking volumes remove the two worst
offenders (`node_modules`, the four `.venv`s), but `media/` writes and
`platforms/*/content.json` still cross that boundary.

### Verifying it actually worked

`./cr up` already checks both halves (hub answering inside the container, published port
reachable from the host). Two more, worth running once on a new machine:

```sh
./cr verify-loopback
./cr shell -c "grep -qE '/assets/index-[A-Za-z0-9_-]+\.js' /app/ReelScraper/frontend/dist/index.html" \
  && echo "real dashboard"
```

The asset check runs **in the container**, deliberately: the whole promise is that the host has
only Docker, so nothing here may assume `curl`. Check the asset reference rather than
`/api/platforms` — that route answers 200 over a blank dashboard.

## `./cr` — the subcommand reference

`./cr` with no argument is `./cr up`. It is POSIX `sh`, shares no code with the six bash entry
points (they need `python3` in five places, which would put Python back on the host
requirement list), and derives a **per-checkout compose project name** from a hash of the
absolute checkout path, pinned into `ReelScraper/.env` as `COMPOSE_PROJECT_NAME`.

| Command | What it does |
|---|---|
| `./cr help` (`-h`, `--help`) | Prints the verb list. **Works with no Docker installed** — it runs before any daemon probe. |
| `./cr up` | Starts the hub. **Does not build.** Polls for up to 180 s, breaking early if the container exits (and printing its message), verifies the published port from the host, opens the browser, and pins `TCR_MODE=container` into `ReelScraper/.env`. |
| `./cr build [args]` | `docker compose build`. Refuses unless the **repo-root** `.dockerignore` exists and its first non-comment line is `**` — the build context is the repo root, and a deny-everything-then-allow file is the only filter on that lane. |
| `./cr rebuild` | `down -v` (drops **both** sets of venv volumes and `node-modules`) then `build --no-cache` for the runtime and dev targets. The `-v` is the point — see [Upgrades](#upgrades). |
| `./cr down [args]` | `docker compose down --timeout 120`. Removes the container; **keeps** the named volumes and everything in the checkout. |
| `./cr logs [-f]` | Hub logs. |
| `./cr status` | Checkout path, compose project, host port, build id, `docker compose ps` — plus any *other* container publishing the same port, named directly rather than inferred from a working directory. |
| `./cr agent <name> [args…]` | Runs an agent CLI inside the container: `analysis-engine`, `auto-search` or `similar-content`. This replaces `cd SimilarContent && uv run cli.py propose …` in container mode. |
| `./cr health [args…]` | Runs `./cr verify-loopback` on the host first and hands the verdict in, then runs `./health` in the **dev** image. Arguments are forwarded **verbatim** and nothing is added — ask for `--strict --live` yourself. |
| `./cr verify-loopback` | Enumerates the host's non-loopback IPv4 addresses and asserts each is refused while `127.0.0.1:$HUB_PORT` succeeds. Host-side only, and it needs `curl` or `wget`. |
| `./cr keys` | `scripts/check-keys.py` **inside** the container, so it proves the container's egress path to the model APIs, which is the one that matters. Read-only; spends nothing. |
| `./cr keys --set` | Prompts for your Gemini key (hidden), writes it to all three agent `.env` files and verifies it from inside the container. This lane's equivalent of `./init`'s prompt — see [Secrets](#secrets). |
| `./cr shell` | A `/bin/sh` in the running hub container. |
| `./cr docsite` | Docs live-reload on `http://127.0.0.1:$DOCS_PORT` (default 8000). Stopped by `./cr down`. |
| `./cr demo` | Loads the demo dataset in the dev image, so `unzip` and `zip` stay off the host requirement list. Starts no hub — reload the one `./cr up` is already running. |

!!! note "`./cr` is a host script"
    Run inside the container it refuses immediately (`TCR_CONTAINER=1` is set in the image).
    There, use the tools directly: `cd /app/SimilarContent && uv run cli.py …`.

### Why `./cr agent` exists

In container mode `cd SimilarContent && uv run cli.py propose …` from a host shell does not
work, and the first thing that breaks is **not** the missing venv: it is each agent's own
foreign-checkout guard. The containerized hub reports its root as `/app/ReelScraper`, which
never equals a host checkout path, so the agent exits rather than writing this niche's work
into what it believes is another checkout's corpus. Running the agent *inside* means both
sides see `/app` and the guard passes untouched.

The rule this protects is unchanged in the container lane: **agents integrate only over the
HTTP hub**, never by touching each other's files.

## Secrets

**Each agent keeps its own `.env`. The hub never stores a secret value.**

| Secret | Lives in | Reaches the container via |
|---|---|---|
| `GEMINI_API_KEY` | `AnalysisEngine/.env`, `SimilarContent/.env`, `AutoSearch/.env` | **the bind mount only** |
| `GEMINI_API_KEY` *(optional)*, Instagram burner session vars | `AutoSearch/.env` | the bind mount only |
| X `auth_token` + `ct0` | `ReelScraper/platforms/x/session.txt` | the bind mount only (file-only, no env form) |

The whole checkout is bind-mounted at `/app`, so those files are simply *there*, at the paths
each agent already reads them from. Nothing else is needed to deliver them.

### `./cr keys --set` — the container lane's key prompt

`./init` is the native lane's onboarding, and it cannot serve this one: it calls
`check_python`, so it needs Python **on the host** — the dependency the container lane exists
to avoid. `./cr keys --set` is the equivalent:

```sh
./cr keys --set     # prompts (hidden), writes both files, verifies from inside the container
./cr keys           # re-check any time; read-only, spends nothing
```

It writes `GEMINI_API_KEY` to `AnalysisEngine/.env`, `SimilarContent/.env` and
`AutoSearch/.env` — the same set
`./init` writes — then runs `check-keys.py` in the container so the check also proves the
container's egress to Google. An invalid key is reported but still saved, because an offline
machine is a failed *check*, not a bad key.

**The key never passes through Docker.** It is read on the host, written to the bind-mounted
files, and picked up by the container *reading those files*. It is never handed to
`docker exec -e` or to an `env_file`, so it appears in neither `docker inspect` nor
`docker compose config` — the same reasoning as the deliberately absent `env_file:` block,
described under **No secrets in `docker/.env`** below.

!!! warning "Exporting the key in your shell does nothing"
    `export GEMINI_API_KEY=…` on the host does **not** reach the container. Compose has no
    `env_file` on purpose, so keys travel by bind-mounted file and nothing else. `./cr up`
    points this out if it sees the variable set while the files are empty.

To do it by hand instead:

```sh
printf 'GEMINI_API_KEY=<your key>\n'    >> AnalysisEngine/.env
printf 'GEMINI_API_KEY=<your key>\n'    >> SimilarContent/.env
printf 'GEMINI_API_KEY=<your key>\n' >> AutoSearch/.env   # optional — see AutoSearch docs
chmod 600 AnalysisEngine/.env SimilarContent/.env AutoSearch/.env

./cr down && ./cr up      # the hub reads .env at import, so a restart is required
./cr keys                 # proves the CONTAINER's egress path
```

The hub reads those files for **presence only** — the value is never returned and never
logged. Presence is what drives the Dashboard's blueprint-button gate, which is why keys stay
in the agents' own `.env` files.

!!! danger "No secrets in `docker/.env`, and never in `environment:` or `env_file:`"
    `docker/.env` is for build/run knobs only (`WITH_FFMPEG`, `WITH_DOCS`, `DOCS_PORT`, `TZ`,
    `TCR_STOP_GRACE`, `TCR_IMAGE_TAG`). Copy `docker/.env.example` to `docker/.env` to use
    them.

    **There is deliberately no `env_file:` block in `docker-compose.yml`,** and its absence is
    the point. Compose materialises `env_file` into the container's `Config.Env`, which
    `docker inspect` and `docker compose config` print verbatim and which the daemon persists
    on host disk. Worse, the hub hands `dict(os.environ)` to every stage it spawns — including
    `render`, whose argv arrives over an unauthenticated route. Loading keys into the
    environment would put them in that subprocess's `environ` before it does anything, which
    widens exactly the blast radius containerizing is meant to narrow.

`ReelScraper/.env` is not a secret file. It holds `HUB_PORT`, `COMPOSE_PROJECT_NAME` and
`TCR_MODE`, and it is the single source of truth for all three — `docker/.env.example` ships
those keys empty on purpose, because a literal there would become a second source with silent
precedence.

Nothing in the container lane makes anything cost more. The paid stages are still opt-in:
`render` spends image-API credits per frame and stays behind a human click, the cascading
heartbeat **can never fire it**, and blueprint generation sits behind its own explicit
per-platform opt-in. See [Quickstart → Automatic runs](quickstart.md#automatic-runs).

## Volumes, and what survives `./cr down`

| Mount | Kind | Why |
|---|---|---|
| `../:/app` | bind, read-write | Source **and** all data. Every data path is interleaved with tracked source, `./clean` needs `git ls-files` against it, and a render's recorded `local_path` has to stay openable from the host. |
| `/app/<project>/.venv` ×4 | named volume (two sets) | Masks the host's own `.venv`. The hub picks the first `.venv/bin/python` that **exists** and never checks its architecture — on an Apple Silicon host that is a Mach-O binary, and every stage would die with `Exec format error` from a container that started perfectly. |
| `/app/Dashboard/node_modules` | named volume (dev only) | Platform-specific native binaries; the host's copy is both wrong and slow. |
| `/app/ReelScraper/frontend/dist` | tmpfs | The built dashboard is not git-tracked, so the bind mount would shadow it with an empty directory and the hub would serve the "building" placeholder at HTTP 503. The entrypoint copies ~1 MB from the image on every start: always fresh, never written to your tree. |
| `/app/AnalysisEngine/work` | tmpfs | Pure scratch — corpus video downloaded and deleted within a run. Keeps it off the bind mount. |
| `/app/.git` | tmpfs (empty), runtime only | Masks a writable `.git/hooks` out of the render subprocess's reach. Not masked in the dev image, where `./health` and `./clean` need a real `.git`. |
| `/tmp` | tmpfs | Also `HOME`, `UV_CACHE_DIR` and `XDG_CACHE_HOME`. |

**`./cr down` is `down` and not `down -v`.** It removes the container and keeps the named
volumes, so the next `./cr up` is fast. **All your data is on the bind mount and is never
touched** — the corpus, media, studio, renders, logs, and every `.env`. Stopping the container
is exactly as safe as `./stop`.

Two things persist per checkout in `ReelScraper/.env`: `HUB_PORT` and
`COMPOSE_PROJECT_NAME`. `./cr` writes both itself — the project name on first run, the port
whenever the pinned one turns out to be taken — so neither needs editing by hand. That project name is why two clones on one machine cannot collide —
compose namespaces volumes by project, and without the pin both clones would derive the same
name from the directory basename, share all four venvs, and `./cr down` in one would stop the
other. See [Niches → Running two niches at once](niches.md#running-two-niches-at-once) for the
host-lane equivalent.

`TCR_MODE=container` is also pinned on the first `./cr up`, and is deliberately **not** cleared
by `./cr down` — a checkout that runs in container mode is still a container-mode checkout
while its container is stopped. Clearing it is a deliberate act: remove the line from
`ReelScraper/.env`.

## Upgrades

```sh
git pull
./cr build       # explicit
./cr up          # starts what you built
```

**`./cr up` does not build.** That is deliberate: building on every invocation would make the
staleness gate below unreachable on the only supported path.

The image carries resolved dependencies; the bind mount carries source. They can drift — a
`git pull` that bumps a `uv.lock`, or a rebuild someone forgot. The entrypoint hashes all four
lockfiles at start and **refuses to start** if they do not match what the image was built from:

```
tcr-entrypoint: the checkout's uv.lock files do not match the ones this image was built from.

  image was built from: 3f9a…   (build a1b2c3d)
  checkout has:         77e1…

  Fix:  ./cr rebuild
```

Without that gate the failure is an opaque offline-resolution error in the middle of a pipeline
stage, minutes later, attributed to the wrong thing.

`./cr rebuild` drops **both** sets of named venv volumes plus `node-modules`, then rebuilds
with `--no-cache`. The volume drop is the part that matters: a rebuilt image's new `.venv` is
otherwise silently masked by the stale volume, and the container runs yesterday's dependencies
while reporting today's build id.

!!! warning "Honest limit: the gate catches dependency skew only"
    Edit `Dashboard/src/*.tsx`, don't rebuild, and the container serves the old UI with no
    complaint. Accepted gap. If the dashboard looks stale, `./cr build` then `./cr up`.

The container has `restart: "no"`, deliberately. `unless-stopped` would bring an
unauthenticated subprocess launcher back up on every reboot with its scheduler threads
running, and would turn every fail-closed startup refusal into a crash loop hidden behind
`./cr up`'s poll. `./cr up` is the intended trigger.

## Security: the loopback boundary

Start from the actual posture: **the hub has no authentication.** Every route is
unauthenticated. Two of them matter more than the rest — one launches a subprocess, and another
supplies that subprocess's argv. Between them stands a single launcher allowlist and an
argument regex. That is a fine design **for a service reachable only from the machine it runs
on**, and catastrophic on a LAN.

On the host lane that property comes from the process bind: the hub defaults to `127.0.0.1`.
**Inside a container it cannot.** Docker publishes a port by DNAT'ing host traffic to the
container's `eth0` address, never to the container's loopback — so a process bound to
`127.0.0.1` inside its own network namespace gives you connection-refused from the browser. The
hub *must* bind `0.0.0.0` in the container.

**So the property moves to the publish spec — one block in `docker/docker-compose.yml`:**

```yaml
ports:
  - "127.0.0.1:${HUB_PORT}:8787"     # correct — host listener on loopback only
  - "[::1]:${HUB_PORT}:8787"         # correct, and necessary
```

```yaml
  - "${HUB_PORT}:8787"               # WRONG — host listener on 0.0.0.0, LAN-reachable
  - "8787:8787"                      # WRONG — same, and pinned to the wrong host port
```

Both forms give the **container** an identical `0.0.0.0` bind. Only the **host-side listener**
differs. On Docker Desktop that listener is the VM port forwarder, and the `127.0.0.1:` prefix
makes it listen on host loopback only. On Linux it writes a DNAT rule restricted to
`-d 127.0.0.1`; without the prefix the rule has no destination restriction at all.

Changing those lines to `"8787:8787"` puts every unauthenticated route — including the
subprocess launcher and its argv supplier — on **every interface of your machine**.

The `[::1]` line is not decoration: `127.0.0.1:` does not cover `[::1]:`, and a browser that
resolves `localhost` to `::1` first gets a connection failure. On a Linux host booted with
IPv6 disabled that line can hard-fail `./cr up`; `./cr up` maps that error explicitly, and the
rule is **delete the `[::1]` line, never the `127.0.0.1:` prefix on the other one**.

### Check it on the machine in front of you

```sh
./cr verify-loopback
```

It enumerates this host's non-loopback IPv4 addresses and asserts each is refused within 2 s
while `127.0.0.1:$HUB_PORT` succeeds. It has to run on the **host**, where the listener is: the
`health` service shares the hub's network namespace, and in there the only non-loopback address
is the container's own `eth0` where the hub genuinely *is* listening — a check written there
would either always fail or get weakened until it asserted nothing.

It states its own limits rather than hiding them. On Docker Desktop for macOS "the host" is
your Mac and the forwarder is a host process, so it is a real test. On WSL2 it tests the WSL
VM's view, which under `networkingMode=mirrored` is **not** the Windows host's view — that case
is untested by anyone. **A second machine on the same LAN remains the only unconditional
test:**

```sh
curl --max-time 2 http://<this-host-ip>:8787/api/platforms     # must FAIL
```

### Rules

1. **Never publish without a host IP.** Every entry in `ports:` must begin with `127.0.0.1:`
   or `[::1]:`.
2. **Never rely on a host firewall on Linux.** Docker's `DOCKER` chain sits in
   `nat`/`PREROUTING` *ahead* of the INPUT rules `ufw` and `firewalld` write. `ufw deny 8787`
   does **not** protect a published port. And the publish prefix itself has an engine-version
   dependency: loopback publishing has historically required
   `net.ipv4.conf.*.route_localnet=1`, which disables martian filtering for `127.0.0.0/8`;
   recent engines add raw-table DROP rules for exactly that. Which versions on which distros
   are safe is **unestablished**, which is why `./cr` enforces an engine floor and why
   `verify-loopback` exists at all.
3. **Never mount `/var/run/docker.sock`.** There is no socket mount anywhere in the compose
   file and there must never be one — that hands host root to an unauthenticated HTTP API in
   one line.
4. **Never `--privileged`, never `cap_add`, never run as root with the checkout mounted
   read-write.** The container runs non-root with `cap_drop: [ALL]` and
   `no-new-privileges:true`. Every one of those lines is what converts a launcher-validation
   bypass from "code execution as you, anywhere in `$HOME`" into "code execution in a namespace
   whose only writable host path is the checkout".
5. **Never `network_mode: host`.** On Linux it gives a real `127.0.0.1` bind and the strongest
   form of the property — but on Docker Desktop it means the *VM's* network, and the browser
   cannot reach the hub at all.
6. **The docs service is not exempt.** `mkdocs serve` has no authentication either and would
   happily serve the whole tree to the LAN, over the same read-write bind mount. It publishes
   on `127.0.0.1:` and carries the same hardening.

!!! danger "A container is packaging, not authentication"
    Anyone who can reach the port can launch a subprocess. And because the checkout is
    bind-mounted read-write and contains the launchers you run next, a bypass reaches the host
    at your next `./cr up`. The ban on exposing the hub still holds — containerizing changes
    *where* the boundary lives, not *whether* you need one.

## Image size and build time

Measured **2026-07-22**, Docker 29.6.1, `linux/arm64`, 10 vCPU / 8 GiB. Nothing has been built
for `linux/amd64`.

| | Measured |
|---|---|
| Runtime image, uncompressed layer sum | **305.8 MB** |
| Runtime image, compressed (what a `docker pull` transfers) | **92 MB** |
| Runtime image, flattened rootfs | 281.2 MB |
| `dev` image (CI + devcontainer only, never published) | 1.66 GB disk usage / 850.4 MB flattened |
| **Cold build** (`docker builder prune -af` first) | **86.2 s** |
| Rebuild after editing a Python source file | **8.0 s** (29 steps cached) |
| Rebuild with no change | 2.6 s |
| Optional docs build (`WITH_DOCS=1`), warm uv cache | 12.8 s |

Where the 305.8 MB is: `debian:bookworm-slim` 108 MB, `uv` 54.6 MB, CPython 48.3 MB, base apt
layer 10.4 MB — **82 % of the image is base OS + interpreter + package manager**. Only 75.2 MB
is this project (63.1 MB of that is the four venvs). ffmpeg and ffprobe together are 3.50 +
3.35 MB.

Two decisions did the heavy lifting:

- **No Node in the runtime image** — 112.0 MB avoided. `node`, `npm` and `npx` still resolve,
  to 786-byte shell shims that explain the situation and exit 127 rather than producing a bare
  exec failure. The dev image has real Node.
- **ffmpeg built minimal from source** (`--disable-everything`, then exactly the codecs,
  demuxers, muxers and filters the renderer uses) — **6.83 MB for the pair** against **208.7 MB**
  for the prebuilt static fallback on arm64: a **201.9 MB** saving. It was verified end to end
  against the real stitch code on PNG, lossy-WebP, lossless-WebP and JPEG frames, produced an
  h264 High@4.0 / yuv420p / no-audio mp4 that QuickTime reads, and passed all 12
  ffmpeg-dependent tests (31 passed, 0 skipped). `FFMPEG_VARIANT=static` remains a documented
  fallback in `docker/.env.example` but is not needed.

The cold build's single largest step is the ffmpeg toolchain apt install (39.9 s, 46 % of it),
and it is cached as its own stage — so it is paid once per architecture per pin bump, never on
a source edit.

## Known sharp edges

**`./cr demo` starts no hub, by design.** It runs `./demo --no-launch` in a one-shot dev
container. That container is not the hub service, so letting the script reach its launch step
would start a second hub inside a throwaway container, on a port nothing publishes. The
dataset lands on the bind mount either way, so reload the browser tab against the hub `./cr up`
is already running.

**The host-lane scripts refuse to run once you are in container mode.** `./cr up` pins
`TCR_MODE=container` into `ReelScraper/.env`; the image and every compose service set
`TCR_CONTAINER=1`. "Container mode **and** not in the container" means you are on the host
while this checkout's hub lives in a container, and all six entry points stop with the `./cr`
equivalent instead of guessing:

| On the host | Run instead |
|---|---|
| `./init` | `./cr up` |
| `./demo` | `./cr demo` |
| `./stop` | `./cr down` |
| `./health` | `./cr health` |
| `./docsite` | `./cr docsite` |
| `./clean` | no `./cr` verb — `./cr shell`, then `./clean` inside |

This is not tidiness. `./stop` identifies the hub by its working directory, which is
meaningless across a container boundary: on the host it finds nothing, reports success, and
leaves the container running. `./init` would start a second hub against the same
bind-mounted data, and `./clean` would delete files the container is mid-write on.

`--help` still works on every one of them. If you have genuinely moved back to the host lane,
clear `TCR_MODE` from `ReelScraper/.env` — `./cr down` deliberately leaves it, since a stopped
container-mode checkout is still a container-mode checkout. `TCR_FORCE_HOST=1` overrides the
guard for a single command.

**A green `./health` is not by itself evidence about the boundary.** Its "hub binds loopback
only" invariant is a grep for a literal `0.0.0.0` in the hub source, and in container mode
that grep keeps passing while proving nothing about the deployed artifact — the bind address
comes from the environment, and the boundary has moved to the publish spec. Two checks cover
the gap: a static "compose publishes on loopback" lint over the `ports:` blocks, which needs no
Docker; and "off-loopback refused", which can only be *observed* from the host. `./cr health`
runs `./cr verify-loopback` first and hands the verdict in through `TCR_LOOPBACK_VERIFIED`;
run through plain `./health` inside a container it records a skip that `--strict` promotes to
a failure. **Use `./cr health`, not `./health`, in container mode.**

**VS Code / Codespaces port forwarding would be a second publish path.** No devcontainer ships
today — this is a warning for anyone who adds one. Editor port forwarding lives nowhere in
compose, so neither the `compose publishes on loopback` lint nor `./cr verify-loopback` can see
it, and in Codespaces with public port visibility auto-forwarding puts an unauthenticated
subprocess launcher on the internet. A devcontainer here must pin `forwardPorts: [8787]` and
`otherPortsAttributes: {"onAutoForward": "ignore"}`.

**You lose the best debugging affordance this project has.** On the host lane, when
`analysis-engine` fails you `cd AnalysisEngine && uv run cli.py run instagram` and read the
traceback. In container mode that becomes `./cr agent analysis-engine run instagram` — one more
layer between you and a stack trace, and every doc line showing the direct form (this site's
[CLI Reference](cli.md), the README, `./init`'s printed next steps) is written for the host
lane. `./cr shell` gets you inside, where the direct form works again.

**`WITH_DOCS=1` writes into your checkout.** The entrypoint materialises the built docs site
into `documentation/site` on the bind mount on every start. It is the one place the container
writes docs into your tree, it is off by default, and the hub only mounts `/documentation` if
the directory exists at import — so a docs rebuild always needs a hub restart.

### If something goes wrong

| Symptom | What it means |
|---|---|
| `host port 8787 is held by …; trying the next free one` | Not a failure — `./cr` handling one, and naming the holder so you can decide whether you wanted that process running. It walks 8787–8816 and pins the winner; nothing to do. `./cr` never kills the holder — it may belong to another clone, and on this machine it usually does. |
| `every port from 8787 to 8816 is already in use` | Thirty consecutive busy ports, which is a real situation and not one to paper over with a random port. Free one, or set `HUB_PORT` in `ReelScraper/.env` to a port you know is free. |
| A bind failure on `[::1]` | IPv6 is disabled on this host. Delete the `[::1]` line from `docker/docker-compose.yml`. **Never** delete the `127.0.0.1:` prefix from the other line. |
| `the checkout's uv.lock files do not match…` | Dependency skew after a `git pull`. `./cr rebuild`. |
| `…exists but will not run in this container` | A host virtualenv is showing through the bind mount — usually because a `venv-*` volume line was removed. `./cr rebuild`. |
| `/app/<Project> is missing` | The checkout was mounted somewhere other than `/app`. The hub, the producer-directory guard and all three agents compare that absolute path. |
| `could not write …/frontend/dist as uid …` | tmpfs ownership. The dist entry must be `mode=1777` — a tmpfs is mounted `root:root` and `mode=` sets bits, not owner. Do not "fix" it by running as root. |
| The dashboard is blank but the API answers | The bind mount is shadowing the built dashboard. `/api/platforms` answers 200 in that state; `/` answers 503. Run the asset check above. |
| `./cr` says it cannot derive a project name | Neither `sha256sum` nor `shasum` is present. Set `COMPOSE_PROJECT_NAME` by hand in `ReelScraper/.env` — anything unique per clone, and never the directory name. |

Stops are cooperative in the container lane too. `./cr down` gives the container 120 s, and the
entrypoint signals the hub and every running stage and escalates to `SIGKILL` itself at 100 s —
inside that window — so a mid-scrape stop still takes the save path. The scrapers finish the
creator they are on. `analyze`, `media`, `auto-search` and `propose` install no stop handler and
die where they stand, exactly as the Stop button already does to them.

## See also

- [Entry Points & Demo Data](entry-points.md) — the host lane's six scripts.
- [Quickstart & Usage](quickstart.md) — the guided first run, stage by stage.
- [CLI Reference](cli.md) — every flag on every command (written for the host lane; prefix
  agent commands with `./cr agent <name>` in container mode).
- [Architecture](architecture.md) — why the hub supervises its siblings, which is why this is
  one image and not four.
