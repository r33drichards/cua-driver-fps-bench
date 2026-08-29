#!/usr/bin/env bash
# Idempotent guest setup for a pi-cua Linux sandbox (Ubuntu 24.04 VM, user `cua`):
# X11 desktop deps for bench_ui/pywebview, Rust deps for cua-driver, python deps.
# Safe to run on every measure.sh invocation (fast when already done).
set -euo pipefail
# systemd-run / computer-server may not set HOME
export HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
STAMP="$HOME/.cache/fps-bench/bootstrap.v1"
[ -f "$STAMP" ] && exit 0
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO=sudo; fi

# When run as root (e.g. through computer-server before a pi session starts), let the
# `cua` user sudo without a password: measure.sh needs it for the tmpfs mount and
# pi-cua sandboxes may ship with cua's password locked and no sudoers entry.
if [ "$(id -u)" -eq 0 ] && id cua >/dev/null 2>&1; then
  echo "cua ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-cua-fps-bench && chmod 0440 /etc/sudoers.d/90-cua-fps-bench
fi

export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends \
  build-essential pkg-config git ca-certificates curl \
  libx11-dev libxi-dev libxtst-dev libxext-dev libwayland-dev libxkbcommon-dev \
  python3-gi gir1.2-webkit2-4.1 gir1.2-gtk-3.0 python3-pip python3-psutil xdotool >/dev/null

# cua-bench: fps_bench/agent.py imports cua_bench.agents.base (BaseAgent).
# --ignore-installed: Debian's typing_extensions has no RECORD file, so pip cannot
# uninstall it and aborts the whole install otherwise.
python3 -m pip install --break-system-packages --ignore-installed --quiet cua-bench-ui pywebview "cua-bench==0.2.11"

# The desktop (Xtigervnc :1) runs as root with /root/.Xauthority; let this user
# open windows on it (pywebview/bench_ui + cua-driver run as this user).
if [ "$(id -u)" -ne 0 ]; then
  $SUDO env DISPLAY=:1 XAUTHORITY=/root/.Xauthority xhost "+SI:localuser:$(id -un)" >/dev/null 2>&1 || \
    { $SUDO cp /root/.Xauthority "$HOME/.Xauthority" && $SUDO chown "$(id -un)" "$HOME/.Xauthority"; }
fi

# rustup exists from pi-cua bootstrap (1.88); cua-driver pins its toolchain via rust-toolchain.toml.
if ! command -v cargo >/dev/null 2>&1; then
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path
fi
# Small VM disks (pi-cua Fleet VMs have ~10 GB root) cannot hold a cua-driver
# release target dir; keep it on a RAM-backed tmpfs (sandboxes have 16 GB).
# measure.sh exports CARGO_TARGET_DIR to this path.
TARGET_DIR=/mnt/fps-target
if ! mountpoint -q "$TARGET_DIR" 2>/dev/null; then
  $SUDO mkdir -p "$TARGET_DIR"
  $SUDO mount -t tmpfs -o size=5G,mode=0777 tmpfs "$TARGET_DIR"
fi
# Reclaim root-owned toolchains left by other bootstrap paths (the build runs as this user).
$SUDO rm -rf /root/.rustup /root/.cargo 2>/dev/null || true
$SUDO apt-get clean >/dev/null 2>&1 || true

mkdir -p "$(dirname "$STAMP")" && touch "$STAMP"
echo "bootstrap done"
