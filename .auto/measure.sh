#!/usr/bin/env bash
# pi-autoresearch benchmark: rebuild cua-driver from ./cua-driver and run the FPS
# L-platform benchmark on the local X display. Emits `METRIC name=value` lines.
# Runs inside the pi-cua Linux sandbox (the workspace root is this repo).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
export DISPLAY="${DISPLAY:-:1}"

bash bench/bootstrap_guest.sh

# Fast pre-check, then release build (incremental after the first run).
( cd cua-driver/rust && cargo check -q -p cua-driver && cargo build -q --release -p cua-driver )
DRIVER="$PWD/cua-driver/rust/target/release/cua-driver"
"$DRIVER" --version

mkdir -p .auto/runs
python3 bench/run_in_sandbox.py --episodes "${EPISODES:-3}" --driver "$DRIVER" \
  --json ".auto/runs/$(date -u +%Y%m%dT%H%M%SZ).json"
