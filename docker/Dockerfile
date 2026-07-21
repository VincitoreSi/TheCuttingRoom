# syntax=docker/dockerfile:1.7
#
# The Cutting Room — one image, four uv projects, one container.
#
# NOTHING IN THIS FILE HAS BEEN BUILT YET. Every size and timing figure in the comments is
# marked "estimated" where it was not measured. DECISIONS.md D2 requires that every size
# claim that survives into the plan come from a real `docker build` + `docker image ls` on
# this machine (Docker 29.6.1, arm64). The build/measure pass owns replacing them.
#
# ------------------------------------------------------------------------------------------
# WHY ONE IMAGE AND NOT FOUR
#
# ReelScraper/api/app.py:1973-1984 defines STAGE_CMD. Three of its entries are
#     ["uv", "run", "cli.py", "run",  <platform>]   cwd = ROOT.parent/"AnalysisEngine"
#     ["uv", "run", "cli.py", "run",  <platform>]   cwd = ROOT.parent/"AutoSearch"
#     ["uv", "run", "cli.py", "beat", <platform>]   cwd = ROOT.parent/"AutoSearch"
# and two more are resolved from a producer's registered manifest:
#     STAGE_CMD["render"]  = _render_stage_cmd  (app.py:2065)
#     STAGE_CMD["propose"] = _propose_stage_cmd (app.py:2117)
# all executed by subprocess.Popen(cmd, cwd=..., env=_stage_env(), start_new_session=_POSIX)
# at app.py:2183-2184. Separately _producer_dir (app.py:1986) refuses any producer whose
# declared dir is not a DIRECT SIBLING of the repo root.
#
# So the hub is simultaneously an HTTP peer of the three agents AND their process supervisor.
# Split them into separate containers and every one of those stages becomes
# `FileNotFoundError: 'uv'` -> rc -1 (app.py:2214) -> a red node on the Board, with no HTTP
# fallback. One image is the only topology that needs zero application changes.
#
# ------------------------------------------------------------------------------------------
# TARGETS
#   runtime  — what users run. No Node, no bash entry points, no .git, no dev dependency
#              groups, no node_modules.
#   dev      — runtime + Node + dev dependency groups + node_modules + bash/curl/git/zip/
#              procps/lsof. This is the ONLY place `./health` may run. Without node_modules
#              it records the whole Dashboard step as a SKIP — typecheck, vitest, eslint,
#              prettier and the production build, five checks — and without ffmpeg it records
#              ffmpeg as a SKIP, silently dropping the 12 @needs_ffmpeg tests in
#              SimilarContent/tests/test_stitch.py. A green ./health from a slim image is a
#              green-but-empty run, which is the exact failure mode health exists to prevent.
# ------------------------------------------------------------------------------------------


# ==========================================================================================
# Pins. One place to bump them.
# ==========================================================================================

# 3.12: all four .venv/pyvenv.cfg say version_info = 3.12, and .github/workflows pins 3.12.
# All four pyproject.toml say requires-python ">=3.10"; 3.10 would resolve too, but it is not
# what the maintainer or the release gate exercise.
ARG PYTHON_TAG=3.12-slim-bookworm

# 0.11.29 is the uv that WROTE all four uv.lock files (verified: every .venv/pyvenv.cfg says
# `uv = 0.11.29`). Do NOT float to :latest. A newer uv can decide a lock needs re-resolving,
# and with --frozen that fails the build — which is the good outcome, but it will then happen
# at a random time on someone else's machine rather than when you chose to bump it.
ARG UV_TAG=0.11.29

# Node 20 exactly: Dashboard/.nvmrc is "20", Dashboard/package.json engines.node is ">=20".
ARG NODE_TAG=20-bookworm-slim

# ffmpeg. See the ffmpeg-minimal stage for the measured requirement list.
#   minimal — built here from source with --disable-everything (DECISIONS.md D2.2). ESTIMATED
#             10-25 MB for the pair against ~155 MB for a static build; MUST BE MEASURED.
#   static  — the documented fallback. Flip FFMPEG_VARIANT=static if the minimal build cannot
#             produce an mp4 that plays in Safari/QuickTime (yuv420p at stitch.py:183 exists
#             for exactly that), or if a @needs_ffmpeg test fails on a codec/filter this list
#             missed. Flipping it is a legitimate outcome, not a defeat — but SAY SO in the
#             plan, because it is ~130 MB of image.
ARG FFMPEG_VARIANT=minimal
# n7.1.3, NOT n7.1.1 — and the difference is one line of upstream's libavcodec/Makefile.
# MEASURED: n7.1.1 does not link with this configure line. libavcodec/h2645_sei.c:935 calls
# ff_aom_uninit_film_grain_params() unconditionally, but in n7.1.1 the Makefile compiles
# aom_film_grain.o only for CONFIG_HEVC_SEI / CONFIG_HEVC_DECODER (:111,113,442) — so a build
# that enables the h264 decoder (which poster() at stitch.py:195-198 requires, because it
# reads the finished mp4 back) and no HEVC dies at link with
#     libavcodec.a(h2645_sei.o): undefined reference to `ff_aom_uninit_film_grain_params'
# n7.1.3 adds aom_film_grain.o to OBJS-$(CONFIG_H264_SEI) and links clean. Verified against
# upstream's Makefile at n7.1.1, n7.1.2, n7.1.3, n8.0 and n8.1: only n7.1.3+ has the fix, and
# the call site is still unconditional in 8.1, so this is a floor and not a workaround.
ARG FFMPEG_REF=n7.1.3
ARG X264_REF=stable
ARG FFMPEG_STATIC_IMAGE=mwader/static-ffmpeg:7.1

ARG WITH_FFMPEG=1     # 0 -> a smaller image for people who never render
ARG WITH_DOCS=0       # 1 -> build documentation/site for the hub's /documentation mount
ARG BUILD_ID=dev      # set by ./cr; stamped into the image next to the lockfile stamp

# ------------------------------------------------------------------------------------------
# DIGEST PINNING — READ BEFORE TAGGING A RELEASE
# These are TAG pins, not digest pins. Tags are mutable: `python:3.12-slim-bookworm` three
# months from now is a different image with the same name. Resolve each with
#     docker buildx imagetools inspect <ref> --format '{{.Manifest.Digest}}'
# and rewrite as `FROM python:3.12-slim-bookworm@sha256:...`. Pinning uv to the version that
# wrote the locks buys nothing if the base moves underneath it.
#
# The one input that is NOT content-pinned is the ffmpeg/x264 source clone below. It is
# pinned by TAG/BRANCH and the resolved commit is PRINTED into the build log; set
# FFMPEG_COMMIT / X264_COMMIT to assert it once the build/measure pass has recorded them.
# ------------------------------------------------------------------------------------------


# ==========================================================================================
# uvbin — the uv binary, pinned. Nothing else from this image is used.
# ==========================================================================================
FROM ghcr.io/astral-sh/uv:${UV_TAG} AS uvbin


# ==========================================================================================
# nodebin — Node for the TARGET platform.
#
# Deliberately separate from the `dashboard` stage below, and this separation is the fix for
# a real bug an earlier draft shipped. `dashboard` is `FROM --platform=$BUILDPLATFORM`, which
# is correct and load-bearing for `dist` (JS is arch-independent; building it under QEMU
# costs 4-8x for nothing). That makes its `node` a BUILD-platform binary. Copying THAT node
# into a $TARGETPLATFORM stage gives the arm64 image an amd64 node, and every `["node", ...]`
# invocation dies with "Exec format error" — the exact class of failure the .venv masking
# volumes exist to prevent, reintroduced one stage down.
#
# Used ONLY by the `dev` target. The runtime image carries no Node at all — see the shim in
# `runtime` and DECISIONS.md D2.1.
# ==========================================================================================
FROM node:${NODE_TAG} AS nodebin


# ==========================================================================================
# ffmpeg-minimal — ffmpeg + ffprobe built for exactly what this project does, nothing else.
#
# THE MEASURED REQUIREMENT. Re-verified against the current tree, not inherited from the
# plan — the plan's list was incomplete in four places, all of which are in here:
#
#   SimilarContent/engine/stitch.py
#     :39-40   FFMPEG/FFPROBE resolved from $FFMPEG_BIN / $FFPROBE_BIN via shutil.which
#     :64      ffmpeg_available() requires BOTH binaries
#     :95-97   ffprobe -select_streams v -show_entries stream=width,height -of csv=p=0:s=x
#              on a STILL  -> image2 demuxer + png/mjpeg/webp decode
#     :119-123 filters: scale, pad, crop, setsar, format   (yuv420p in both chains)
#     :174-175 -loop 1 -framerate N -t D -i <frame>        -> image2 demuxer, PNG/JPEG/WebP
#     :179     concat=n=N:v=1:a=0                          -> NO AUDIO AT ALL
#     :181     -r <fps>                                    -> fps filter (auto-inserted)
#     :182-185 -c:v libx264 -preset medium -crf N -pix_fmt yuv420p -movflags +faststart -an
#     :195-198 poster(): READS THE MP4 BACK and writes a .jpg
#              -> mov demuxer + h264 DECODER + mjpeg ENCODER + image2 muxer   <-- missed by
#                 the plan, and it is what the poster on every render card is made of
#     :204     ffprobe -show_entries format=duration -of default=nw=1:nk=1
#     :211     ffprobe -show_streams -of json
#   SimilarContent/engine/render.py:156 accepts .png .jpg .jpeg .webp frames
#              -> webp decoder (which needs the vp8 decoder for lossy WebP)   <-- also missed
#   scripts/seed_renders.py:51,56 runs the same poster + duration probe with the bare names
#              -> both binaries must be on PATH, not merely reachable via FFMPEG_BIN
#
# NOT NEEDED, and every one of these is a decoder family left out on purpose: audio of any
# kind (the concat filter is a:0 and the encode is -an), hardware acceleration, network
# protocols, subtitles, HLS/DASH, every non-h264 video codec.
#
# libx264 is GPL. Building it here rather than pulling a static binary does not change that,
# and it must stay documented wherever the image's licensing is stated.
#
# THE BUILD IS CACHED as its own stage, so its cost is paid once per architecture per pin
# bump — not on every source edit.
# ==========================================================================================
FROM debian:bookworm-slim AS ffmpeg-minimal
ARG FFMPEG_REF
ARG X264_REF
ARG FFMPEG_COMMIT=""
ARG X264_COMMIT=""

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates git nasm pkg-config zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# x264, static, 8-bit 4:2:0 only.
#
# `--bit-depth=8 --chroma-format=420` is not incidental: stitch.py:183 encodes yuv420p and
# nothing here ever asks for 10-bit or 4:2:2, and dropping the other depths removes whole
# duplicated assembly kernels. `--disable-cli` drops the x264 binary; only the library is
# linked into ffmpeg.
#
# Integrity: the clone is by branch, then `git rev-parse HEAD` is PRINTED and, when
# X264_COMMIT is supplied, ASSERTED. Git is content-addressed, so an asserted commit id is a
# stronger pin than a tarball checksum. The build/measure pass must record the two resolved
# ids and set them here — this file deliberately does not invent hashes it has not seen.
RUN git clone --depth 1 --branch "${X264_REF}" https://code.videolan.org/videolan/x264.git x264 \
 && cd x264 \
 && sha="$(git rev-parse HEAD)" \
 && echo "x264 commit: ${sha}" \
 && if [ -n "${X264_COMMIT}" ] && [ "${sha}" != "${X264_COMMIT}" ]; then \
        echo "x264 commit ${sha} != pinned ${X264_COMMIT}" >&2; exit 1; fi \
 && ./configure \
        --prefix=/opt/x264 \
        --enable-static --enable-pic \
        --disable-cli --disable-opencl \
        --bit-depth=8 --chroma-format=420 \
 && make -j"$(nproc)" && make install

# ffmpeg. --disable-everything turns off every codec/format/filter/protocol/parser, then each
# line below turns exactly one thing back on.
#
# Two notes for whoever has to debug this:
#  * ffmpeg's configure ERRORS on an unknown --enable-<kind>=<name>. The lists here are
#    restricted to names verified to exist in 7.x. If a @needs_ffmpeg test fails with
#    "Decoder not found" or "No such filter", ADD the name — do not switch to --enable-all.
#  * `buffer`/`buffersink`/`abuffer`/`abuffersink` and the auto-inserted `fps`, `setpts`,
#    `trim`, `format`, `scale`, `aresample` filters are listed explicitly. The ffmpeg CLI
#    inserts several of them into a graph you never wrote, and with --disable-everything they
#    are not there to insert.
RUN git clone --depth 1 --branch "${FFMPEG_REF}" https://github.com/FFmpeg/FFmpeg.git ffmpeg \
 && cd ffmpeg \
 && sha="$(git rev-parse HEAD)" \
 && echo "ffmpeg commit: ${sha}" \
 && if [ -n "${FFMPEG_COMMIT}" ] && [ "${sha}" != "${FFMPEG_COMMIT}" ]; then \
        echo "ffmpeg commit ${sha} != pinned ${FFMPEG_COMMIT}" >&2; exit 1; fi \
 && PKG_CONFIG_PATH=/opt/x264/lib/pkgconfig ./configure \
        --prefix=/opt/ff \
        --pkg-config-flags=--static \
        --extra-cflags=-I/opt/x264/include \
        --extra-ldflags=-L/opt/x264/lib \
        --disable-everything \
        --disable-autodetect \
        --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages \
        --disable-debug \
        --disable-network \
        --disable-avdevice \
        --disable-postproc \
        --disable-shared --enable-static \
        --disable-ffplay --enable-ffmpeg --enable-ffprobe \
        --enable-small \
        --enable-pthreads \
        --enable-zlib \
        --enable-gpl --enable-libx264 \
        --enable-protocol=file,pipe \
        --enable-demuxer=image2,image2pipe,png_pipe,webp_pipe,mjpeg,mov \
        --enable-muxer=mp4,mov,image2 \
        --enable-decoder=png,mjpeg,webp,vp8,h264,rawvideo \
        --enable-encoder=libx264,mjpeg,png \
        --enable-parser=png,mjpeg,h264 \
        --enable-filter=aformat,anull,aresample,buffer,abuffer,buffersink,abuffersink,concat,copy,crop,format,fps,null,pad,scale,setpts,asetpts,setsar,setdar,trim,atrim \
 && make -j"$(nproc)" && make install

# Land the two binaries at /ffmpeg and /ffprobe so this stage is drop-in interchangeable with
# the static image below (`FROM ffmpeg-${FFMPEG_VARIANT} AS ffmpegbin`).
RUN cp /opt/ff/bin/ffmpeg /ffmpeg && cp /opt/ff/bin/ffprobe /ffprobe \
 && strip /ffmpeg /ffprobe \
 && ls -l /ffmpeg /ffprobe \
 && /ffmpeg -hide_banner -version | head -1

# ==========================================================================================
# ffmpeg-static — the documented fallback. ~155 MB for the pair (upstream's own figure;
# NOT measured here). Selected with --build-arg FFMPEG_VARIANT=static.
# ==========================================================================================
FROM ${FFMPEG_STATIC_IMAGE} AS ffmpeg-static

# The alias both `runtime` and `dev` copy from. Resolved at build time from FFMPEG_VARIANT.
FROM ffmpeg-${FFMPEG_VARIANT} AS ffmpegbin


# ==========================================================================================
# dashboard — builds Dashboard/dist (Dashboard/vite.config.ts:30 outDir "dist").
#
# --platform=$BUILDPLATFORM is load-bearing for multi-arch. dist is JS and CSS: byte-identical
# on amd64 and arm64. Running npm ci + tsc + vite build under QEMU for the foreign arch costs
# 4-8x for zero benefit — ESTIMATED as roughly the difference between a 4-minute and a
# 25-minute emulated arm64 build. Only `dist` crosses out of here; see `nodebin` for why the
# binaries must not.
# ==========================================================================================
FROM --platform=$BUILDPLATFORM node:${NODE_TAG} AS dashboard
WORKDIR /build/Dashboard

# Manifests first, sources second: a .tsx edit must not invalidate the npm ci layer.
COPY Dashboard/package.json Dashboard/package-lock.json ./
# BuildKit cache mount, not a COPY of a cache dir — the npm cache never enters a layer.
# sharing=locked because npm is not safe under concurrent writers to one cache.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --no-audit --no-fund

COPY Dashboard/ ./
# `npm run build` (package.json:13 = `tsc && vite build`), NOT `npm run deploy`.
# deploy (package.json:16) shells out to scripts/deploy-dashboard.sh, whose entire reason to
# exist is a stage-dir + two-rename dance that avoids 404ing a RUNNING hub while dist is
# swapped. There is no running hub inside a build, so it would add a bash dependency to this
# stage to buy nothing.
RUN npm run build


# ==========================================================================================
# docs-1 / docs-0 — optional documentation site for app.py:3396-3398's /documentation mount.
#
# A conditional FROM *is* a thing, via the same alias trick the ffmpeg variants use, and here
# it is worth ~55 s of wall clock on every cold build. WITH_DOCS defaults to 0; the draft's
# single always-run docsbuild stage paid for an mkdocs-material install and a full mkdocs
# build on every build and then threw the output away in `app`. WITH_DOCS must be exactly
# `0` or `1` — anything else is a "failed to solve: docs-<x>: not found" at the FROM below,
# which is the intended failure.
# ==========================================================================================
FROM --platform=$BUILDPLATFORM python:${PYTHON_TAG} AS docs-1
COPY --from=uvbin /uv /usr/local/bin/uv
WORKDIR /build
# mkdocs lives in ReelScraper's dev dependency group.
COPY ReelScraper/pyproject.toml ReelScraper/uv.lock ./ReelScraper/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    cd ReelScraper && uv sync --frozen --no-install-project --group dev
COPY documentation/ ./documentation/
# `--no-sync` is REQUIRED and its absence was a real build failure, not a nicety.
#
# `docsite` runs this on a host where the project is already installed. Here the stage
# deliberately syncs with --no-install-project (sources are not copied — that is the point of
# the layer split), and a bare `uv run` then notices the environment is missing the project
# and tries to build `the-cutting-room @ file:///build/ReelScraper`, whose setuptools backend
# immediately fails:
#     error: package directory 'core' does not exist
#     file cli.py (for module cli) not found
# --no-sync tells uv to use the environment exactly as synced. mkdocs is an entry point in
# that environment and does not import the project.
RUN cd ReelScraper && uv run --no-sync mkdocs build -f ../documentation/mkdocs.yml

# The WITH_DOCS=0 twin: same contract (a directory at /build/documentation/site), no mkdocs.
# The base image is already pulled for pybase, so this stage is one empty layer.
FROM --platform=$BUILDPLATFORM python:${PYTHON_TAG} AS docs-0
RUN mkdir -p /build/documentation/site

# WITH_DOCS is a GLOBAL ARG (declared with the other pins at the top); global ARGs are the
# only ones legal in a FROM. Do not add a stage-scoped `ARG WITH_DOCS` above this line — it
# would bind to docs-0, not to this FROM.
FROM docs-${WITH_DOCS} AS docsbuild


# ==========================================================================================
# pybase — the shared python + uv + env layer under both runtime and dev.
# ==========================================================================================
FROM python:${PYTHON_TAG} AS pybase

# uv must be on PATH at RUNTIME, not just build time: STAGE_CMD hardcodes "uv" as argv[0] for
# three stages (app.py:1978-1982) and the default render/propose argv is ["uv","run","cli.py",
# ...] (app.py:2058, :2102). Without it those stages die with
# `[Errno 2] No such file or directory: 'uv'`, caught at app.py:2207-2216 and recorded as
# rc -1 with the exception string as the tail.
COPY --from=uvbin /uv /usr/local/bin/uv

ENV \
    # Never re-resolve or re-sync at request time. Default `uv run` re-locks and re-syncs; in
    # a container that means a stage launch either reaches for the network or fails, at the
    # exact moment a user clicks "Run full pipeline". Four belts:
    #   NO_SYNC          — skip the sync entirely, use the .venv as-is
    #   FROZEN           — forbid touching uv.lock
    #   OFFLINE          — forbid the network
    #   PYTHON_DOWNLOADS — forbid fetching an interpreter
    # This is NEW surface: there is no UV_* variable anywhere in the repo today, so nothing
    # currently exercises the "uv run must not resolve" path. It must be tested with
    # `--network none` or it passes in CI and fails on a laptop.
    UV_NO_SYNC=1 \
    UV_FROZEN=1 \
    UV_OFFLINE=1 \
    UV_PYTHON_DOWNLOADS=never \
    # Deliberately NOT precompiling. The guarantee that nothing writes into site-packages at
    # runtime — which is what makes the masking .venv volumes safe to read under an arbitrary
    # uid — comes from PYTHONDONTWRITEBYTECODE, not from shipping .pyc. Shipping bytecode is
    # a startup-latency optimisation that costs image size, and DECISIONS.md D2.3 item 3 asks
    # for the size. _stage_env() (app.py:2128) hands dict(os.environ) to every spawned stage,
    # so this propagates to the children too.
    # MEASURE BOTH: if the cold-start cost of an unmodified interpreter is material, flip
    # UV_COMPILE_BYTECODE=1, drop the pyc strip in `app`, and record both numbers.
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
#
# DELIBERATELY NOT SET: UV_PROJECT_ENVIRONMENT.
#   It is global, and _stage_env() hands `dict(os.environ)` to every spawned stage — so one
#   value would point all four projects at one shared environment. The venvs must also stay
#   at <project>/.venv for a second, independent reason: app.py:333-337 _interpreter() returns
#   the first of (.venv/bin/python, venv/bin/python) that exists and falls back to
#   sys.executable otherwise, and that fallback is wrong for the [PY, "scrape.py", ...] stages
#   at app.py:1974-1977.

# Non-root by default. On native Docker Engine (Linux, not Desktop) a root container writes
# root-owned files into the bind-mounted checkout, and the user's own `clean` then cannot
# delete its own media/ and logs/. Docker Desktop hides this behind uid translation.
RUN groupadd --gid 1000 tcr && useradd --uid 1000 --gid 1000 --create-home tcr


# ==========================================================================================
# deps — four dependency layers, ordered least-churn-first.
#
# The --no-install-project split matters. All four pyprojects install THEMSELVES (setuptools
# backend; ReelScraper declares packages = ["core","api"] and py-modules = ["cli",
# "download_media"]). A bare `uv sync` after `COPY <project>/` welds the whole wheel install
# to every .py edit. Manifests -> sync --no-install-project -> sources -> project-only sync
# reduces post-edit work to a fast editable reinstall.
#
# Four separate layers, not one loop: churn in AutoSearch/uv.lock must not invalidate
# ReelScraper's wheels.
#
# --no-dev is the single biggest lever. ReelScraper's dev group carries mkdocs-material,
# babel, mkdocs, pygments and pytest; SimilarContent's `dependencies = []` means its entire
# runtime venv is stdlib plus the project itself.
#
# NOTHING HERE NEEDS A COMPILER. Every non-pure dependency across the four locks —
# pydantic-core, jiter, rpds-py, uvloop, httptools, watchfiles, websockets, pyyaml — publishes
# manylinux wheels for x86_64 AND aarch64. No build-essential, no rustc, and the arm64 leg of
# a multi-arch build is wheel-fetch rather than cross-compilation. VERIFY THIS by building
# arm64 and amd64 and confirming neither leg compiles anything; it is what makes multi-arch
# cheap for this project specifically.
# ==========================================================================================
FROM pybase AS deps
WORKDIR /app

# UV_OFFLINE=0 is set PER-RUN: the ENV above is the RUNTIME posture, and the build is the one
# place uv is allowed to reach the network.
COPY SimilarContent/pyproject.toml SimilarContent/uv.lock ./SimilarContent/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    cd SimilarContent && UV_OFFLINE=0 uv sync --frozen --no-dev --no-install-project

COPY AnalysisEngine/pyproject.toml AnalysisEngine/uv.lock ./AnalysisEngine/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    cd AnalysisEngine && UV_OFFLINE=0 uv sync --frozen --no-dev --no-install-project

COPY AutoSearch/pyproject.toml AutoSearch/uv.lock ./AutoSearch/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    cd AutoSearch && UV_OFFLINE=0 uv sync --frozen --no-dev --no-install-project

COPY ReelScraper/pyproject.toml ReelScraper/uv.lock ./ReelScraper/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    cd ReelScraper && UV_OFFLINE=0 uv sync --frozen --no-dev --no-install-project


# ==========================================================================================
# app — sources on top of deps, then the cheap project-only installs, then the strip.
# ==========================================================================================
FROM deps AS app
WORKDIR /app

# /app is a hard requirement, not a convention. Three independent mechanisms compare this
# exact absolute path:
#   1. app.py:1971-1972  ANALYSIS_ENGINE_DIR / AUTO_SEARCH_DIR = ROOT.parent / "<Name>"
#   2. app.py:1986-2012  _producer_dir asserts the declared dir is a DIRECT SIBLING
#   3. all three agents compute Path(__file__).resolve().parents[2] / "ReelScraper" and
#      compare it against the hub's reported root from GET /api/hub (app.py:778-790). The
#      comparison lives in each agent's engine/hub.py foreign_checkout()
#      (AnalysisEngine:94/119, AutoSearch:91/116, SimilarContent:104/129 — three SIMILAR
#      files, not byte-identical) and the SystemExit(2) is in each agent's cli.py
#      (AnalysisEngine:119, AutoSearch:143, SimilarContent:107).
# ROOT is Path(__file__).resolve().parents[1], so ROOT == /app/ReelScraper in the image, in
# the bind mount and in the devcontainer. All three comparisons hold.
COPY ReelScraper/        ./ReelScraper/
COPY AnalysisEngine/     ./AnalysisEngine/
COPY AutoSearch/         ./AutoSearch/
COPY SimilarContent/     ./SimilarContent/
COPY _producer-template/ ./_producer-template/
COPY scripts/            ./scripts/
COPY niches/             ./niches/
COPY demo-data/          ./demo-data/

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    set -eu; for p in ReelScraper AnalysisEngine AutoSearch SimilarContent; do \
      (cd "/app/$p" && UV_OFFLINE=0 uv sync --frozen --no-dev); \
    done

# Strip what the runtime never reads. DECISIONS.md D2.3 item 3.
#   __pycache__/*.pyc  — nothing writes them (PYTHONDONTWRITEBYTECODE=1) and nothing needs
#                        them; the interpreter recompiles in memory.
#   pip/setuptools/wheel — uv does not install them into a project venv by default, so this
#                        is usually a no-op. It is here so it stays a no-op if that changes.
#   *.dist-info is KEPT: fastapi/uvicorn/starlette read their own metadata via
#   importlib.metadata, and removing it turns a size win into an import error.
RUN set -eu; \
    find /app -type d -name '__pycache__' -prune -exec rm -rf {} + ; \
    find /app -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete ; \
    for p in ReelScraper AnalysisEngine AutoSearch SimilarContent; do \
      sp="/app/$p/.venv/lib/python3.12/site-packages"; \
      [ -d "$sp" ] || continue; \
      rm -rf "$sp/pip" "$sp/pip-"*.dist-info \
             "$sp/setuptools" "$sp/setuptools-"*.dist-info "$sp/pkg_resources" \
             "$sp/wheel" "$sp/wheel-"*.dist-info "$sp/_distutils_hack" \
             "$sp/distutils-precedence.pth"; \
    done; \
    du -sh /app/*/.venv

# The dashboard build output goes to /opt, NOT to /app/ReelScraper/frontend/dist.
#
# WHY: frontend/dist is NOT git-tracked (`git ls-files ReelScraper/frontend` is empty;
# .gitignore:50 is `dist/`). On a fresh clone the directory does not exist. Bind-mounting the
# checkout over /app therefore SHADOWS whatever the image put at that path with an empty host
# directory, and app.py:3509-3511 falls back to the self-polling "building" page — served at
# HTTP 503, not 200. This is the day-one failure of a naive single-image plan.
#
# entrypoint.sh copies this into a tmpfs at /app/ReelScraper/frontend/dist on every start:
# ~1 MB, always matches the image, never goes stale, never writes to the host checkout.
COPY --from=dashboard /build/Dashboard/dist /opt/tcr/frontend-dist

# Optional docs. app.py:3396-3398 mounts ../documentation/site AT IMPORT, and only
# `if DOCS_SITE.exists()`, so a docs rebuild needs a hub restart under any topology.
ARG WITH_DOCS
COPY --from=docsbuild /build/documentation/site /opt/tcr/docs-site
RUN if [ "$WITH_DOCS" != "1" ]; then rm -rf /opt/tcr/docs-site; fi


# ==========================================================================================
# runtime — the shipped image.
# ==========================================================================================
FROM pybase AS runtime
ARG WITH_FFMPEG
ARG BUILD_ID
ARG FFMPEG_VARIANT

# Runtime OS packages, deliberately minimal.
#   ca-certificates — outbound TLS for every agent (Gemini, Anthropic, instagram.com,
#                     youtubei, the image providers). Present in python:slim already; listed
#                     for intent so a future base swap does not silently drop it.
#   tini            — belt for a bare `docker run` without --init. Compose sets init: true.
#                     Required because _run_job's Popen (app.py:2183) reaps its DIRECT child
#                     (uv), but uv's python grandchild reparents to PID 1 if orphaned and the
#                     hub never wait()s on unknown children -> zombies. tini reaps orphans
#                     regardless of process group, so start_new_session does not weaken this.
#
# NOT installed, on purpose:
#   lsof, procps — scripts/_common.sh identifies processes by working directory (lsof -d cwd,
#                  /proc fallback, pgrep -f). That machinery is meaningless inside a PID
#                  namespace and is replaced by the compose project name. Shipping the tools
#                  invites someone to keep using them.
#   git          — the runtime image must not contain .git. Without .git, git is dead weight.
#   curl, zip    — used only by the bash entry points, which live in `dev`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------------------
# THERE IS NO NODE IN THE RUNTIME IMAGE — but `node` and `npm` still resolve, to a refusal.
#
# DECISIONS.md D2.1: Node is roughly 150 MB (ESTIMATED — measure it) of the runtime image and
# exists solely so a hypothetical third-party producer declaring `"render_cmd": ["node", ...]`
# does not fail at exec. No first-party producer uses node; SimilarContent, the reference
# producer, is Python.
#
# node/npm STAY in RENDER_LAUNCHERS (app.py:2032). That allowlist is a SECURITY control and is
# correct for the host lane — availability and permission are different things, and removing a
# name from an allowlist to express "we did not install it" is how allowlists rot.
#
# What D2.1 asks for is that the absence be a LEGIBLE REFUSAL rather than a bare exec failure.
# The proper fix is in _validate_render_cmd (app.py:2036): refuse at validation time, with a
# 400 that names the container-image limitation, when argv[0] is node/npm and it is not on
# PATH. THAT SOURCE CHANGE HAS NOT LANDED — it is not owned by this file.
#
# These two shims are the container-lane half, and they work with no source change: instead of
# `FileNotFoundError: 'node'` -> rc -1 -> a red Board node whose tail names a Python exception,
# the stage exits 127 with a tail that says what happened and what to do. `dev` overwrites
# both with the real binaries from `nodebin` a few stages down, so ./health still sees a real
# Node. Delete these the day the app.py refusal lands.
# ------------------------------------------------------------------------------------------
RUN set -eu; \
    { \
      echo '#!/bin/sh'; \
      echo '# Shim. The runtime image of The Cutting Room ships no Node.js.'; \
      echo '# See docker/Dockerfile and DECISIONS.md D2.1. Replaced in the `dev` target.'; \
      echo 'exec >&2'; \
      echo 'echo "$(basename "$0"): this image contains no Node.js, so it cannot run here."'; \
      echo 'echo'; \
      echo 'echo "A producer declared a render_cmd or propose_cmd starting with node or npm."'; \
      echo 'echo "node and npm are still PERMITTED by the hub launcher allowlist; they are"'; \
      echo 'echo "simply not installed, because no first-party producer needs them and Node"'; \
      echo 'echo "is a large fraction of the image size."'; \
      echo 'echo'; \
      echo 'echo "  * run that producer on the HOST lane, where your node is on PATH; or"'; \
      echo 'echo "  * build a derived image: FROM cuttingroom:local + your own Node; or"'; \
      echo 'echo "  * give the producer a Python entry point (uv, python, python3 all exist)."'; \
      echo 'exit 127'; \
    } > /usr/local/bin/node; \
    chmod 0555 /usr/local/bin/node; \
    cp -p /usr/local/bin/node /usr/local/bin/npm; \
    cp -p /usr/local/bin/node /usr/local/bin/npx

# ffmpeg + ffprobe. BOTH are probed: stitch.py:64 ffmpeg_available() requires both,
# SimilarContent/cli.py:440-441 refuses to render without them (before any paid API call) and
# cli.py:525 reports them in `doctor`. Both are overridable via FFMPEG_BIN / FFPROBE_BIN
# (stitch.py:39-40) — the image just puts them where `shutil.which` looks.
COPY --from=ffmpegbin /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpegbin /ffprobe /usr/local/bin/ffprobe
RUN if [ "$WITH_FFMPEG" != "1" ]; then rm -f /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; fi
ENV TCR_FFMPEG_VARIANT=${FFMPEG_VARIANT}

COPY --from=app --chown=tcr:tcr /app     /app
COPY --from=app --chown=tcr:tcr /opt/tcr /opt/tcr

# Build identity + the lockfile stamp.
#
# Source comes from the bind mount; dependencies come from the image. They CAN disagree — a
# `git pull` that bumps a uv.lock, or a rebuild someone forgot. Without this gate the failure
# is an opaque uv resolution error (UV_OFFLINE=1 -> "no solution found: offline") in the
# middle of a pipeline stage. With it, the container refuses to start and says `./cr rebuild`.
#
# LIMIT, stated honestly: this catches DEPENDENCY skew only. It does NOT catch source-vs-dist
# skew (a Dashboard/src edit against a stale /opt/tcr/frontend-dist). See RISKS.md R7.
ENV TCR_BUILD_ID=${BUILD_ID}
RUN set -eu; \
    sha256sum /app/ReelScraper/uv.lock /app/AnalysisEngine/uv.lock \
              /app/AutoSearch/uv.lock  /app/SimilarContent/uv.lock \
      | awk '{print $1}' | sort | sha256sum | awk '{print $1}' > /opt/tcr/lockstamp; \
    chmod 0444 /opt/tcr/lockstamp

COPY docker/entrypoint.sh /usr/local/bin/tcr-entrypoint
RUN chmod 0555 /usr/local/bin/tcr-entrypoint

ENV \
    # 0.0.0.0 HERE IS CORRECT AND REQUIRED, and it is not a weakening.
    #
    # Docker publishes a port by DNAT'ing host traffic to the container's eth0 address, never
    # to the container's loopback. A process bound to 127.0.0.1 inside its own netns is
    # reachable only from inside that netns, so HUB_HOST=127.0.0.1 + `-p` gives
    # connection-refused.
    #
    # The loopback-only security property therefore MOVES from the process bind to the
    # PUBLISH SPEC in docker-compose.yml:
    #     ports: ["127.0.0.1:${HUB_PORT}:8787"]   correct
    #     ports: ["8787:8787"]                    binds 0.0.0.0 on the HOST — 55 unauthenticated
    #                                             routes on the LAN, one of which
    #                                             (POST /api/studio/{p}/{f}/render, app.py:1035)
    #                                             launches a subprocess and one of which
    #                                             (POST /api/producers/register, app.py:1363)
    #                                             supplies its argv.
    #
    # cli.py:33 already reads HUB_HOST from the environment with no validation, so this needs
    # no source edit — which is ALSO why health's `hub binds loopback only` check (a grep for
    # a literal "0.0.0.0" in cli.py and api/) keeps PASSING while proving nothing about the
    # deployed artifact. That is
    # RISKS.md R2. The grep must be joined by a compose-publish lint and a HOST-SIDE live
    # off-loopback check (`./cr verify-loopback`), which cannot run in the hub's netns.
    HUB_HOST=0.0.0.0 \
    # Fixed INSIDE the container forever; only the host side of the publish varies per
    # checkout. Consequence worth knowing: cli.py:49-64's random-port fallback never fires
    # here, because nothing else in this netns can hold 8787 — the address becomes stable by
    # construction, and the whole "the hub moved to a port nothing can bookmark" class of bug
    # disappears. entrypoint.sh passes --strict-port to make that explicit.
    HUB_PORT=8787 \
    # Read by the six entry points' container-mode guard together with TCR_MODE in
    # ReelScraper/.env. The guard must be "TCR_MODE=container AND TCR_CONTAINER unset":
    # ReelScraper/.env is on the bind mount, so TCR_MODE is equally true INSIDE the container,
    # and a guard that looked only at it would make `./cr demo`, `./cr health` and `./cr clean`
    # exit non-zero on the code path they exist to run.
    # (That guard is NOT in the entry points yet — grep for TCR_MODE returns nothing. This
    # variable is the contract for whoever adds it.)
    TCR_CONTAINER=1 \
    # A uid with no /etc/passwd entry (any TCR_UID != 1000 on Linux) otherwise gets HOME=/ ,
    # which is not writable; uv and expanduser() then fail obscurely. /tmp is a tmpfs.
    HOME=/tmp \
    UV_CACHE_DIR=/tmp/uv-cache \
    XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app/ReelScraper
USER tcr
EXPOSE 8787

# Talks to the container's OWN loopback — inside the netns, 127.0.0.1 IS the hub.
# Deliberately not curl (not installed here); the venv python is guaranteed present.
#
# BOTH endpoints, not just /api/platforms. This closes the gap the drafts complained about:
# when the bind mount shadows the dashboard, /api/platforms answers 200 over a blank UI and a
# readiness poll goes green on a broken product. `/` answers 503 in that state
# (app.py:3509-3511), so asserting it makes the healthcheck notice.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD ["/app/ReelScraper/.venv/bin/python", "-c", "import urllib.request as u,sys; b='http://127.0.0.1:8787'; sys.exit(0 if u.urlopen(b+'/api/platforms',timeout=4).status==200 and u.urlopen(b+'/',timeout=4).status==200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/tcr-entrypoint"]
CMD ["hub"]


# ==========================================================================================
# dev — the image ./health runs in, and the devcontainer base. Never published.
# ==========================================================================================
FROM runtime AS dev
USER root

# Everything the six bash entry points and ./health actually shell out to.
#   bash          — the scripts genuinely need it (process substitution, `declare -a` at
#                   health, ${frames:i++%4:1} in _common.sh). LISTED FOR INTENT: bash is
#                   Essential in Debian 12, so /bin/bash is already present in `runtime` too.
#                   It was never the thing keeping the scripts out of the runtime image.
#   curl          — _common.sh, and health's endpoint loop
#   git           — clean and health. Without it health sets IS_GIT=0 and FOUR
#                   invariants become skips (no .env tracked, no secrets in git history, demo
#                   dataset committable, working data ignored) while the run still reports
#                   HEALTHY.
#   zip, unzip    — clean's "refusing to delete data I cannot archive first"; demo:54-55
#   procps, lsof  — _common.sh and stop. Meaningful ONLY inside a devcontainer, where the hub
#                   really is a normal process in this PID namespace.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash curl git zip unzip procps lsof less \
    && rm -rf /var/lib/apt/lists/*

# Real Node, for the TARGET platform, over the refusal shims from `runtime`.
# `dev` is never multi-arch and never published, so the `npm ci` RUN below emulating under
# QEMU for a foreign arch is an accepted cost that the shipped image does not pay.
COPY --from=nodebin /usr/local/bin/node          /usr/local/bin/node
COPY --from=nodebin /usr/local/lib/node_modules  /usr/local/lib/node_modules
RUN set -eu; rm -f /usr/local/bin/npm /usr/local/bin/npx; \
    ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm; \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx; \
    node --version; npm --version

# git >= 2.35.2 exits 128 with "detected dubious ownership" when the repo top level is owned
# by a different uid than the caller — exactly what a bind-mounted host checkout looks like
# under native Docker Engine. `clean` wraps `git ls-files --error-unmatch` in `2>/dev/null`,
# so a 128 reads as "not tracked": tracked files get archived and DELETED. This is the belt;
# making `clean` fail closed on a git ERROR (rather than on a non-zero exit) is the braces,
# and belongs to whoever owns `clean`.
RUN git config --system --add safe.directory '*'

# The bash entry points are COPY'd only in dev. Be clear about what that does and does not
# buy: the bind mount puts all six at /app in EVERY container, runtime included, so their
# absence from the runtime layer contains nothing at runtime. What keeps them from running in
# the wrong lane is the TCR_MODE/TCR_CONTAINER guard. This COPY exists so the dev image is
# self-contained without a mount — which is what CI needs.
COPY --chown=tcr:tcr init demo stop clean health docsite /app/
COPY --chown=tcr:tcr documentation /app/documentation

# Dev dependency groups for all four projects: httpx for ReelScraper's TestClient suite,
# pytest everywhere, mkdocs for ./docsite.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    set -eu; for p in ReelScraper AnalysisEngine AutoSearch SimilarContent; do \
      (cd "/app/$p" && UV_OFFLINE=0 uv sync --frozen); \
    done

# node_modules baked in — see the header note about health's Dashboard step. Bind-mounting
# the host's copy would be both wrong (rollup, esbuild and tailwind-oxide ship platform-
# specific native
# binaries) and slow (per-file stat over VirtioFS).
COPY --chown=tcr:tcr Dashboard/package.json Dashboard/package-lock.json /app/Dashboard/
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    cd /app/Dashboard && npm ci --no-audit --no-fund
COPY --chown=tcr:tcr Dashboard /app/Dashboard

# ./health lives at /app/health, but `runtime` sets WORKDIR /app/ReelScraper. The compose
# services override working_dir; this makes a bare `docker run` do the right thing too.
WORKDIR /app

RUN chown -R tcr:tcr /app /opt/tcr
USER tcr
# The devcontainer keeps the container alive and lets a human run ./health inside it.
CMD ["sleep", "infinity"]
