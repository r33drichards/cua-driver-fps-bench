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

## Known-good idea from a sibling loop (2026-08-29, Chromium variant, gVisor pool)
`results/pi-sessions/peer-best-xtest-when-focused.diff` reached score 1.0 (from 0.0)
on a Chromium build of this game: in `platform-linux/src/input/mod.rs::send_key`,
if `x11_focus_is_within(display, xid)` already holds, route through `send_key_xtest`
(no activation, still "background"); and drop the blanket
`unavailable_gtk_keyboard_background` refusal in `PressKeyTool`. Keys already land on
this WebKitGTK build (delivery_ratio 1.0 on main), so the open problem here is
`move_cursor` → real pointer motion (`mouse_ratio`); apply the same "when the target
owns focus, use XTest" idea to pointer motion.

## What's Been Tried
- 2026-08-29 baseline (stock cua-driver 0.4.2 on a Fleet Ubuntu 24.04 VM, XFCE on
  Xtigervnc :1, pywebview/WebKitGTK window): score 0.00, delivery_ratio 0.00 over
  180 press_key calls; move_cursor moved only the overlay cursor (page saw no
  mousemove). Control experiment on the same window: `xdotool key w` (XTest, focused
  window) → keydown delivered; `xdotool key --window <id> w` (XSendEvent) → NOT
  delivered; `xdotool mousemove` (XTest) → mousemove delivered. Conclusion: WebKitGTK
  ignores synthetic XSendEvent key events; XTest-style injection into the focused
  window works. press_key without pid/window fails with "No windows found for pid 0".
- Harness gaps fixed on the remote tip (commits 52c521f / 815b8b0 / e795f84):
  `measure.sh` now starts `cua-driver serve --dangerously-bypass-approvals` (the
  `call` subcommand does NOT auto-spawn the daemon — without this every press_key
  errors "daemon is not running"), exports XAUTHORITY, and builds on a 5G tmpfs
  with CARGO_BUILD_JOBS=4 (the VM root disk is ~10 GB and the release build OOMs
  the small Fleet VM if run naively). `bootstrap_guest.sh` grants the non-root
  `cua` user X access (the XFCE desktop runs as root, so `cua` gets "Authorization
  required / cannot open display :1" without an xauth copy + `xhost`).
- Exp 1 (committed by a parallel agent from this session's patch): press_key
  removes the `unavailable_webkit_keyboard_background` refusal and adds
  `deliver_fg = delivery.is_foreground() || (!foreground && is_webkitgtk_embedder(pid))`
  so WebKitGTK targets auto-escalate to the foreground XTest rung. **BUT this is
  currently dead code**: the very next check, `unavailable_gtk_keyboard_background`,
  fires for pywebview (WebKitGTK links libgtk → `is_gtk_process()` is true) and
  returns a `background_unavailable` refusal BEFORE `deliver_fg` ever runs. So
  press_key still errors out every call → delivery_ratio stays 0. Confirmed by
  reading the handler; a live measurement is pending sandbox availability.
- Exp 2 (this iteration): gate `unavailable_gtk_keyboard_background` on
  `!is_webkit_target` (hoisted `is_webkitgtk_embedder(pid)` once). Now WebKitGTK
  embedders skip the GTK refusal and fall through to the auto-escalation, so the
  foreground XTest key actually fires. Plain (non-WebKit) GTK apps keep the
  refusal. Minimal, unblocks the already-committed intent. Live measurement pending.
- Exp 3 (seeded, 9fad87f): move_cursor default `scope=window` only moves the
  synthetic overlay, so the page's PointerLockControls sees no mousemove
  (mouse_ratio=0). Added: when the target pid is a WebKitGTK embedder, also inject
  a REAL absolute XTest motion via `send_move_xtest_desktop(xi, yi)`. Non-WebKit
  targets keep the "don't move the user's pointer" contract.
- Exp 4 (KEPT, this loop): the initial `click` on the canvas was a synthetic
  XSendEvent (route=global_input), which the browser does NOT count as a user
  gesture → `requestPointerLock()` was always denied → `locked=false` for the
  whole episode, so PointerLockControls never rotated from move_cursor's
  mousemove (mouse_ratio=0.036, score=0.0). Fix: ClickTool auto-escalates
  WebKitGTK embedders to the foreground XTest rung (`effective_fg =
  delivery.is_foreground() || (!fg && is_webkitgtk_embedder(pid))`), giving a
  REAL XTest button event (send_event=false) so pointer lock engages. Mirrors
  press_key's existing auto-escalation. Result (EPISODES=5): score 0.0→0.6,
  mouse_ratio 0.036→0.319, progress 0.37→0.957, moves 44→18. Keys already 1.0.
  2/5 failures hit max_actions at progress 0.95: under pointer lock the browser
  recenters the OS pointer each frame, so an ABSOLUTE XTest warp to (nx,cy)
  yields movementX = nx - recenter_pos, not nx - prev_agent_pos → only ~32% of
  sent pixels register → imprecise yaw → oscillation near the goal.
- Exp 5/6 (DISCARDED): relative XTest motion (query pointer, warp to cur+dx;
  or cache the lock anchor once and warp to anchor+dx). Delivers full
  movementX (mouse_ratio 0.036→1.087, locked=True) BUT score COLLAPSED to 0.0
  (progress 0.96→0.79): per-move delivered_px is asymmetric (+99 forward /
  -235 back for ±110 intent) → net drift yaw away from target → agent never
  faces goal. Exp6==Exp5 exactly, so the bias is NOT a query race; it is
  inherent to XTest warps under pointer lock on Xtigervnc/WebKitGTK (the
  browser's per-frame recenter to the lock anchor is partially counted by the
  page's mousemove handler, asymmetrically). DEAD END — do not retry
  relative/anchor motion on this stack; need true XI2 relative valuator motion.
- Exp 7 (DISCARDED): added a sync round-trip BEFORE the XTest warp to let the
  prior recenter settle. mouse_ratio IDENTICAL (0.3193) — the recenter races
  AFTER our warp, not before, so syncing first is a no-op. Reverted.
- Final state: Exp 4 stands. Score variance (0.6 on EPISODES=5, 1.0 on
  EPISODES=2) is run-to-run noise from the erratic movementX delivery, not a
  lock or key problem. The agent runs a fixed 18 moves + 42 presses (=60
  max_actions) every episode; success depends on whether the erratic yaw put it
  inside the goal radius by action 60 (progress 0.952 vs 0.962).

### Infrastructure blocker (2026-08-29)
- The Fleet warm pool `cua-pi-linux-rw` started returning **403 Forbidden** for
  `osgymsandboxwarmpools` (credentials/auth revoked), and VMs kept going offline
  mid cargo-build (OOM/reclaim). A live VM (fps-b-2) was recovered via direct
  Tailscale SSH; measurements are being retried there with CARGO_BUILD_JOBS=1.
- The pi-cua controller's `backend.py` pinned `uv run --python 3.11` while
  `cua-sandbox==0.3.4` now requires `>=3.12,<3.14` — patched locally to `3.12`
  so `cua_sandbox ensure` can re-provision.
- `bootstrap_guest.sh` does NOT pip-install `cua-bench` (only `cua-bench-ui` +
  `pywebview`); `fps_bench/agent.py` imports `cua_bench.agents.base`. Provisioning
  must `pip install cua-bench` after bootstrap (done manually per VM).
