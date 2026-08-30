#!/usr/bin/env bash
# Boot-time native build of cua-driver from the pre-cloned source tree.
# Runs once per container under supervisord; experiments reuse the warm
# target/ directory so a patched rebuild is incremental.
set -euo pipefail
SRC="${CUA_DRIVER_SRC:-/opt/cua/libs/cua-driver}"
STAMP=/opt/fps-bench/prebuild.done
LOG=/opt/fps-bench/prebuild.log
rm -f "$STAMP"
{
  echo "prebuild start $(date -Is) ref=$(git -C "$SRC" rev-parse --short HEAD)"
  cd "$SRC/rust"
  cargo build --release -p cua-driver
  install -m 0755 target/release/cua-driver /usr/local/bin/cua-driver
  /usr/local/bin/cua-driver --version
  # The MCP gateway and the serve daemon wrap the binary we just replaced.
  supervisorctl restart cua-driver-mcp || true
  supervisorctl restart cua-driver-serve || true
  sleep 2; /usr/local/bin/cua-driver status || true
  echo "prebuild done $(date -Is)"
} >"$LOG" 2>&1
touch "$STAMP"
