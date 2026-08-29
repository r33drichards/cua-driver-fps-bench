#!/usr/bin/env bash
# Launch one headless pi session pinned to a pi-cua sandbox and run the
# pi-autoresearch loop there. Bash runs on the sandbox; pi-cua keeps file tools
# and git on the *local* workspace, so each session gets its own local clone
# (never this checkout) and pushes its branch back to origin.
#
#   scripts/pi_autoresearch.sh <sandbox-name> [iterations]
#
# Parallel hill-climbs: one call per sandbox, e.g.
#   for s in fps-a fps-b fps-c; do scripts/pi_autoresearch.sh $s 20 & done; wait
#
# Local clone: $PI_WORKSPACES/<sandbox>-<session8> (default ~/cb-explore-pi-workspaces)
# Log: results/pi-sessions/<sandbox>-<session8>.log ; experiment log = <clone>/.auto/log.jsonl
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX=${1:?sandbox name}
ITER=${2:-20}
SESSION=$(python3 -c 'import uuid; print(uuid.uuid4())')
SHORT=${SESSION:0:8}
WS_ROOT=${PI_WORKSPACES:-$HOME/cb-explore-pi-workspaces}
WS="$WS_ROOT/$SANDBOX-$SHORT"
BRANCH="autoresearch/$SANDBOX-$(date -u +%Y%m%d)-$SHORT"
ORIGIN=$(git -C "$REPO_DIR" remote get-url origin)
BASE=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)
mkdir -p "$WS_ROOT" "$REPO_DIR/results/pi-sessions"
LOG="$REPO_DIR/results/pi-sessions/$SANDBOX-$SHORT.log"

# Fresh local clone at the pushed tip of the current branch (the sandbox clones
# the same commit from origin, so both sides agree).
git clone -q --branch "$BASE" "$ORIGIN" "$WS"
git -C "$WS" checkout -q -b "$BRANCH"

# Pin the (not yet created) session to the sandbox; prepares the remote workspace.
( cd "$WS" && "$REPO_DIR/.venv/bin/python" "$REPO_DIR/scripts/pi_sandbox.py" bind "$SESSION" "$SANDBOX" --os linux --repo "$WS" >/dev/null )

PROMPT=$(cat <<EOF
You are running the autoresearch loop for this repository ON THE SANDBOX: bash runs there
(the workspace is this repo, synced), while file edits and git happen in the local clone
$WS on branch $BRANCH. Follow the autoresearch-create skill:
1. Read .auto/prompt.md and .auto/measure.sh. The branch already exists; do not create another.
2. Call init_experiment (name: cua-driver-fps, metric: score, unit: fraction, direction: higher).
3. Run the baseline with run_experiment (command: ./.auto/measure.sh, timeout 1800s — the first
   run compiles cua-driver from scratch on a tmpfs) and log it. Then loop: edit files in scope,
   run_experiment, log_experiment with asi notes, keep/discard. Use EPISODES=1 while exploring,
   EPISODES=5 before keeping. Stop after ${ITER} experiments or when .auto/config.json maxIterations hits.
4. Keep .auto/prompt.md "What's Been Tried" updated. After every keep, run: git push -u origin $BRANCH
5. Never ask questions; keep going. When done, push the branch and print a 10-line summary.
EOF
)

echo "session=$SESSION sandbox=$SANDBOX clone=$WS branch=$BRANCH log=$LOG"
# UV_NO_PROJECT: pi-cua's backend re-execs itself with `uv run --python 3.11`; without
# this it adopts the clone's pyproject (python>=3.12) and every repair/ensure fails.
( cd "$WS" && UV_NO_PROJECT=1 pi --session-id "$SESSION" --name "autoresearch-$SANDBOX" --thinking high -p "$PROMPT" ) 2>&1 | tee "$LOG"
