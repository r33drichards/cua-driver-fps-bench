#!/usr/bin/env bash
# Smoke-test a headless pi session pinned to a pi-cua sandbox:
#   scripts/pi_headless_test.sh <sandbox-name>
set -euo pipefail
cd "$(dirname "$0")/.."
SANDBOX=${1:?sandbox name}
SID=$(python3 -c 'import uuid;print(uuid.uuid4())')
.venv/bin/python scripts/pi_sandbox.py bind "$SID" "$SANDBOX" --os linux >/dev/null
echo "SID=$SID"
pi --session-id "$SID" -p "Run the bash tool with: hostname; whoami; pwd; ls. Then reply with the exact output." 2>&1 | tail -20
