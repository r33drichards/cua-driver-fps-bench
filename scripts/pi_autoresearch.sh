#!/usr/bin/env bash
# Launch one headless pi session pinned to a pi-cua sandbox and run the
# pi-autoresearch loop there (the sandbox executes pi's tools in a workspace
# synced from this repo's origin + local overlay).
#
#   scripts/pi_autoresearch.sh <sandbox-name> [iterations]
#
# Run several in parallel (one per sandbox) for parallel hill-climbs:
#   for s in fps-a fps-b fps-c; do scripts/pi_autoresearch.sh $s 20 & done; wait
#
# Logs: results/pi-sessions/<sandbox>-<session>.log ; the experiment log lives in
# the sandbox workspace (.auto/log.jsonl) and is committed by pi-autoresearch —
# pull it back with scripts/pi_collect.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
SANDBOX=${1:?sandbox name}
ITER=${2:-20}
SESSION=$(python3 -c 'import uuid; print(uuid.uuid4())')
mkdir -p results/pi-sessions
LOG="results/pi-sessions/${SANDBOX}-${SESSION:0:8}.log"

# Pin the (not yet created) session to the sandbox; pi-cua reads this at session_start.
.venv/bin/python scripts/pi_sandbox.py bind "$SESSION" "$SANDBOX" --os linux >/dev/null

PROMPT=$(cat <<EOF
You are running the autoresearch loop for this repository ON THE SANDBOX (all tools
execute there; the workspace is this repo). Follow the autoresearch-create skill:
1. Read .auto/prompt.md and .auto/measure.sh.
2. git checkout -b autoresearch/cua-driver-keys-$(date -u +%Y%m%d)-${SESSION:0:6}
3. Call init_experiment (name: cua-driver-fps, metric: score, unit: fraction, direction: higher).
4. Run the baseline with run_experiment (command: ./.auto/measure.sh, timeout 1800s — the first
   run compiles cua-driver from scratch) and log it. Then loop: edit files in scope,
   run_experiment, log_experiment with asi notes, keep/discard. Use EPISODES=1 while exploring,
   EPISODES=5 before keeping. Stop after ${ITER} experiments or when .auto/config.json maxIterations hits.
5. Keep .auto/prompt.md "What's Been Tried" updated. Never ask questions; keep going.
EOF
)

echo "session=$SESSION sandbox=$SANDBOX log=$LOG"
pi --session-id "$SESSION" --name "autoresearch-$SANDBOX" --thinking high -p "$PROMPT" 2>&1 | tee "$LOG"
