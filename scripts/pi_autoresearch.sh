#!/usr/bin/env bash
# Launch one headless pi session pinned to a pi-cua sandbox and run the
# pi-autoresearch loop there. Bash (and git) run on the sandbox; pi-cua keeps
# file tools on the local clone, so each session gets its own local clone
# (never this checkout). Afterwards the experiment branch is fetched from the
# sandbox over SSH and pushed to origin from the Mac (the sandbox has no creds).
#
#   scripts/pi_autoresearch.sh <sandbox-name> [iterations]
#
# Parallel hill-climbs: one call per sandbox, e.g.
#   for s in fps-c fps-d; do scripts/pi_autoresearch.sh $s 20 & done; wait
#
# Local clone: $PI_WORKSPACES/<sandbox>-<session8> (default ~/cb-explore-pi-workspaces)
# Log: results/pi-sessions/<sandbox>-<session8>.log ; experiment log = .auto/log.jsonl on the branch
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO_DIR/.venv/bin/python"
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

# 1. Make `ssh cua@<sandbox>` reach the live tailnet node (re-created VMs get a -N
#    suffix) BEFORE pi-cua health-checks it, otherwise it may "repair" the sandbox.
"$PY" "$REPO_DIR/scripts/pi_sandbox.py" ssh-fix "$SANDBOX"

# 2. Root-level guest setup through computer-server (deps, bench_ui, cua-bench, tmpfs,
#    X access, NOPASSWD sudo for cua) — the cua user alone cannot do this.
"$PY" "$REPO_DIR/scripts/guest_bootstrap.py" --sandbox "$SANDBOX"

# 3. Fresh local clone at the pushed tip of the current branch (the sandbox clones
#    the same commit from origin, so both sides agree).
git clone -q --branch "$BASE" "$ORIGIN" "$WS"
git -C "$WS" checkout -q -b "$BRANCH"

# 4. Pin the (not yet created) session to the sandbox; prepares the remote workspace.
BIND=$("$PY" "$REPO_DIR/scripts/pi_sandbox.py" bind "$SESSION" "$SANDBOX" --os linux --repo "$WS")
REMOTE_CWD=$(printf '%s\n' "$BIND" | tail -n 1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["target"]["remoteCwd"])')
POOL=$("$PY" -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); import pi_sandbox; print(pi_sandbox.pool_for('$SANDBOX'))")

PROMPT=$(cat <<EOF
You are running the autoresearch loop for this repository ON THE SANDBOX: bash and git run
there in the synced workspace $REMOTE_CWD; file edits happen in the local clone. The branch
$BRANCH already exists locally — create it on the sandbox too: git checkout -b $BRANCH.
Follow the autoresearch-create skill:
1. Read .auto/prompt.md and .auto/measure.sh.
2. Call init_experiment (name: cua-driver-fps, metric: score, unit: fraction, direction: higher).
3. Run the baseline with run_experiment (command: ./.auto/measure.sh, timeout 1800s — the first
   run compiles cua-driver from scratch on a tmpfs) and log it. Then loop: edit files in scope,
   run_experiment, log_experiment with asi notes, keep/discard. Use EPISODES=1 while exploring,
   EPISODES=5 before keeping. Stop after ${ITER} experiments or when .auto/config.json maxIterations hits.
4. Keep .auto/prompt.md "What's Been Tried" updated. Commit on the sandbox after every keep
   (git add -A && git commit). Do NOT push: the sandbox has no credentials; the launcher fetches
   your branch afterwards.
5. Never ask questions; keep going. When done, print a 10-line summary.
6. NEVER create, delete, repair or "clean up" sandboxes (no cua_sandbox tool use); other
   sessions own the other sandboxes. If the sandbox becomes unreachable, stop and report.
EOF
)

echo "session=$SESSION sandbox=$SANDBOX pool=$POOL clone=$WS remote=$REMOTE_CWD branch=$BRANCH log=$LOG"
# UV_NO_PROJECT: pi-cua's backend re-execs itself with `uv run`; without this it adopts
# the clone's pyproject (python>=3.12) and every backend call fails.
# -xt cua_sandbox: the agent must never manage sandboxes.
set +e
( cd "$WS" && UV_NO_PROJECT=1 CUA_PI_LINUX_POOL="$POOL" pi --session-id "$SESSION" --name "autoresearch-$SANDBOX" --thinking high -xt cua_sandbox -p "$PROMPT" ) 2>&1 | tee "$LOG"
set -e

# 5. Bring the experiment branch home and publish it.
if git -C "$WS" fetch -q "ssh://cua@$SANDBOX$REMOTE_CWD" "$BRANCH:refs/remotes/sandbox/$BRANCH" 2>/dev/null; then
  git -C "$WS" branch -f "$BRANCH" "refs/remotes/sandbox/$BRANCH"
  git -C "$WS" push -q -u origin "$BRANCH" && echo "pushed $BRANCH ($(git -C "$WS" rev-parse --short "$BRANCH"))"
else
  echo "no branch $BRANCH on the sandbox (nothing committed there)"
fi
