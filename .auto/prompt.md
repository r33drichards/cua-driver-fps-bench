# Autoresearch: cua-driver key delivery in a first-person game

## Objective
Make `cua-driver call press_key` reliably deliver key presses to a pywebview
(WebKitGTK) window on the X11 XFCE desktop of this Linux sandbox. The benchmark
(`bench/run_in_sandbox.py`) launches a three.js first-person game on an L-shaped
platform (`tasks/fps_lshape/gui/index.html`), and an agent (`fps_bench/agent.py`)
that reads the game state but can only act through cua-driver: `move_cursor`
(relative mouse motion turns the camera via three.js PointerLockControls, 0.002
rad/px) and `press_key` "w" taps (each keydown moves 0.5 units). The first
action is a `click` on the canvas (focus + pointer-lock request). The episode
succeeds when the goal is reached.

## Metrics
- **Primary**: `score` (fraction of episodes that reach the goal, 0..1, higher is better)
- **Secondary**: `delivery_ratio` (keydowns seen by the page / press_key calls) and
  `mouse_ratio` (mouse pixels the page saw / pixels sent via move_cursor) — the
  most sensitive signals; raise these first. Also `mean_progress`, `mean_presses`,
  `mean_mouse_moves`, `falls`, `driver_errors`, `bench_seconds`

## How to Run
`./.auto/measure.sh` — bootstraps guest deps (idempotent), rebuilds cua-driver
(`cua-driver/rust`, release), runs the benchmark, prints `METRIC name=value` lines.
The first run also compiles from scratch (several minutes); later runs are incremental.
Set `EPISODES=1` for a fast signal while iterating, `EPISODES=5` to confirm a keep.

Useful diagnostics inside the sandbox (DISPLAY=:1):
- `cua-driver/rust/target/release/cua-driver call list_windows '{}'` — is the
  "L-Platform" window visible to the driver, and with what pid/window_id?
- `... call press_key '{"key":"w","pid":<pid>}'` vs `'{"key":"w","delivery_mode":"foreground"}'`
- `xdotool key w` — control: does the page count a keydown from XTest at all?
- `.auto/runs/*.json` — per-press log (`key_log[].delivered`) for the last run.

## Files in Scope
- `cua-driver/rust/crates/platform-linux/src/**` — Linux backend: X11 input
  injection (XSendEvent / XTest / XInput2), window targeting, AT-SPI, focus handling.
- `cua-driver/rust/crates/cua-driver-core/src/**` — driver core / tool dispatch,
  only where the Linux press_key path needs it.
- `cua-driver/rust/crates/cua-driver/src/**` — CLI/tool plumbing for press_key
  (e.g. resolving a window target when none is given).

## Off Limits
- `bench/`, `fps_bench/`, `tasks/` — the benchmark and the agent are the fixed yardstick.
- `.auto/measure.sh` metric names.
- macOS / Windows platform crates (must still compile but do not change behavior).

## Constraints
- `cargo build --release -p cua-driver` must succeed; keep `cargo check` clean.
- No new system dependencies beyond what `bench/bootstrap_guest.sh` installs.
- Prefer the smallest change that raises `delivery_ratio`; keep the no-focus-steal
  ("background") contract as the default and only escalate to a foreground/XTest
  path when the background path provably cannot land.

## What's Been Tried
- 2026-08-29 baseline (stock cua-driver 0.4.2 on a Fleet Ubuntu 24.04 VM, XFCE on
  Xtigervnc :1, pywebview/WebKitGTK window): score 0.00, delivery_ratio 0.00 over
  180 press_key calls; move_cursor moved only the overlay cursor (page saw no
  mousemove). Control experiment on the same window: `xdotool key w` (XTest, focused
  window) → keydown delivered; `xdotool key --window <id> w` (XSendEvent) → NOT
  delivered; `xdotool mousemove` (XTest) → mousemove delivered. Conclusion: WebKitGTK
  ignores synthetic XSendEvent key events; XTest-style injection into the focused
  window works. press_key without pid/window fails with "No windows found for pid 0".
