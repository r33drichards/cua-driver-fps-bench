#!/usr/bin/env bash
# Runs ON THE SANDBOX inside a workspace clone: records two benchmark runs,
# A = cua-driver source from <base-ref> (baseline), B = current branch (patched).
#   bash scripts/record_ab.sh <base-ref> [episodes]
# Videos: .auto/runs/*-baseline.mp4 and *-patched.mp4 (printed as VIDEO lines).
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=${1:?base ref (e.g. origin/main)}
EP=${2:-2}
CUR=$(git rev-parse --abbrev-ref HEAD)
git fetch -q origin
echo "== A: baseline cua-driver from $BASE"
git checkout -q "$BASE" -- cua-driver
RECORD=1 RECORD_TAG=baseline EPISODES="$EP" ./.auto/measure.sh 2>&1 | grep -E "^episode|^METRIC score|^METRIC mouse_ratio|^METRIC mean_progress|^VIDEO|^driver"
echo "== B: patched cua-driver from $CUR"
git checkout -q "$CUR" -- cua-driver
RECORD=1 RECORD_TAG=patched EPISODES="$EP" ./.auto/measure.sh 2>&1 | grep -E "^episode|^METRIC score|^METRIC mouse_ratio|^METRIC mean_progress|^VIDEO|^driver"
git status --short | head -3 || true
ls -la .auto/runs/*.mp4
