#!/usr/bin/env bash
# Runs ON a Fleet sandbox (as root, DISPLAY=:1). Launches the game window via bench_ui,
# then compares key delivery paths: xdotool (XTest) vs cua-driver press_key
# (background / pid-targeted / foreground). Also dumps what cua-driver's WebKit
# detection would see (/proc child tree of the window pid).
#   bash /tmp/gvisor_key_probe.sh /tmp/game.html
set -u
export DISPLAY=:1 HOME=/root
HTML=${1:-/tmp/game.html}
mkdir -p /tmp/fps_bench_game && cp "$HTML" /tmp/fps_bench_game/index.html
pkill -f bench_ui.child 2>/dev/null; sleep 0.5
PID=$(python3 -c "from bench_ui import launch_window; print(launch_window(folder='/tmp/fps_bench_game', title='L-Platform', width=800, height=600))")
sleep 3
kd() { python3 -c "from bench_ui import execute_javascript as e; import json; print(json.dumps(e($PID, 'JSON.stringify({k:__state.keydowns,m:__state.mousemoves,dx:__state.mouse_dx,l:__state.locked})')))"; }
echo "window pid=$PID  xid=$(xdotool search --name L-Platform | tail -1)"
echo "== /proc tree under $PID (what is_webkitgtk_embedder walks):"
ps -eo pid,ppid,args --forest | grep -A6 -E "^\s*$PID " | cut -c1-140 | head -12
echo "webkit helpers visible: $(ps -eo args | grep -c -E 'WebKitWebProcess|WebKitNetworkProcess')"
echo "initial: $(kd)"
xdotool key w; sleep 0.4; echo "after xdotool key (XTest): $(kd)"
xdotool mousemove 400 300 mousemove 480 300; sleep 0.4; echo "after xdotool mousemove: $(kd)"
CD=/usr/local/bin/cua-driver
$CD --version; $CD status 2>&1 | head -3
for args in '{"key":"w"}' "{\"key\":\"w\",\"pid\":$PID}" "{\"key\":\"w\",\"pid\":$PID,\"delivery_mode\":\"foreground\"}"; do
  out=$($CD call press_key "$args" 2>&1 | tr '\n' ' ' | cut -c1-200); sleep 0.4
  echo "press_key $args -> $out"; echo "   state: $(kd)"
done
out=$($CD call click "{\"x\":400,\"y\":300,\"pid\":$PID}" 2>&1 | tr '\n' ' ' | cut -c1-160); sleep 0.4; echo "click -> $out"; echo "   state: $(kd)"
out=$($CD call move_cursor "{\"x\":520,\"y\":300,\"pid\":$PID}" 2>&1 | tr '\n' ' ' | cut -c1-160); sleep 0.4; echo "move_cursor -> $out"; echo "   state: $(kd)"
echo "== daemon log tail:"; tail -n 12 /var/log/supervisor/cua-driver-serve.log 2>/dev/null | cut -c1-200
kill $PID 2>/dev/null
