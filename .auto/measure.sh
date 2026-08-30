#!/usr/bin/env bash
# pi-autoresearch benchmark: rebuild cua-driver from ./cua-driver and run the FPS
# L-platform benchmark on the local X display. Emits `METRIC name=value` lines.
# Runs inside the pi-cua Linux sandbox (the workspace root is this repo).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export DISPLAY="${DISPLAY:-:1}"
# XAUTHORITY: the XFCE desktop on this sandbox runs as root, so the cua user
# needs the xauth cookie copied into ~/.Xauthority (provisioned once). Point
# Xlib at it explicitly in case the env lacks it.
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

bash bench/bootstrap_guest.sh

# Build artifacts live on a RAM-backed tmpfs (the VM root disk is ~10 GB); keep the
# build lean: no debug info, no incremental caches.
export CARGO_TARGET_DIR=/mnt/fps-target
if ! mountpoint -q "$CARGO_TARGET_DIR" 2>/dev/null; then
  sudo mkdir -p "$CARGO_TARGET_DIR" && sudo mount -t tmpfs -o size=5G,mode=0777 tmpfs "$CARGO_TARGET_DIR"
fi
export CARGO_PROFILE_RELEASE_DEBUG=0 CARGO_PROFILE_RELEASE_STRIP=true CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-4}

# Fast pre-check, then release build (incremental after the first run).
( cd cua-driver/rust && cargo check -q -p cua-driver && cargo build -q --release -p cua-driver )
DRIVER="$CARGO_TARGET_DIR/release/cua-driver"
"$DRIVER" --version

# `cua-driver call <tool>` (how the bench agent acts) talks to a running daemon
# over a unix socket; it does NOT auto-spawn one. Start `serve` in the
# background with approvals bypassed (headless automated bench) and tear it down
# on exit so each run is hermetic.
SOCK="$HOME/.cache/cua-driver/cua-driver.sock"
mkdir -p "$(dirname "$SOCK")"
rm -f "$SOCK"
"$DRIVER" serve --dangerously-bypass-approvals --no-permissions-gate \
  >"$HOME/.cache/cua-driver/serve.log" 2>&1 &
SERVE_PID=$!
cleanup() { kill "$SERVE_PID" 2>/dev/null || true; wait "$SERVE_PID" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for the daemon socket to accept a request (max ~20s).
for _ in $(seq 1 100); do
  if "$DRIVER" status --socket "$SOCK" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then
    echo "cua-driver serve exited early; log:" >&2
    cat "$HOME/.cache/cua-driver/serve.log" >&2 || true
    exit 1
  fi
  sleep 0.2
done

mkdir -p .auto/runs
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
# RECORD=1: screen-capture the desktop for the duration of the benchmark
# (.auto/runs/<stamp>${RECORD_TAG:+-$RECORD_TAG}.mp4). Needs ffmpeg on the guest.
if [ "${RECORD:-0}" = "1" ]; then
  GEOM=$(xdotool getdisplaygeometry 2>/dev/null | tr ' ' 'x'); GEOM=${GEOM:-1024x768}
  VIDEO=".auto/runs/${STAMP}${RECORD_TAG:+-$RECORD_TAG}.mp4"
  ffmpeg -loglevel error -y -f x11grab -video_size "$GEOM" -framerate 15 -i "$DISPLAY" \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p "$VIDEO" >/dev/null 2>&1 &
  FFMPEG_PID=$!
  trap 'cleanup; kill -INT "$FFMPEG_PID" 2>/dev/null; wait "$FFMPEG_PID" 2>/dev/null || true' EXIT
fi
python3 bench/run_in_sandbox.py --episodes "${EPISODES:-3}" --driver "$DRIVER" \
  --json ".auto/runs/${STAMP}.json"
if [ -n "${FFMPEG_PID:-}" ]; then
  kill -INT "$FFMPEG_PID" 2>/dev/null; wait "$FFMPEG_PID" 2>/dev/null || true
  echo "VIDEO $VIDEO"
fi