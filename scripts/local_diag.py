"""Diagnose key delivery in the local docker image: list windows, press with/without target, xdotool control.

  FPS_BENCH_IMAGE=fps-bench-cua-driver:local .venv/bin/python scripts/local_diag.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import agent as A  # noqa: E402
from fps_bench.runner import local_session, task_module, wait_prebuild  # noqa: E402


async def sh(s, cmd):
    r = await s.run_command(cmd, check=False)
    print(f"$ {cmd}\n  rc={r.get('return_code')} out={(r.get('stdout') or '')[:1500]!r} err={(r.get('stderr') or '')[:400]!r}")
    return r


async def keydowns(s, pid):
    return await s.execute_javascript(pid, "window.__state.keydowns")


async def main():
    s = await local_session()
    try:
        print(await wait_prebuild(s, timeout=1500))
        await sh(s, "cua-driver --version; id; echo DISPLAY=$DISPLAY")
        pid = await s.launch_window(html=task_module.game_html(), title=task_module.WINDOW_TITLE, width=800, height=600)
        print("window pid", pid)
        await sh(s, "sleep 2; DISPLAY=:1 xdotool getactivewindow getwindowname 2>&1 || true; DISPLAY=:1 wmctrl -l || true")
        r = await sh(s, A.driver_call("list_windows", {}))
        await sh(s, A.driver_call("list_apps", {}))
        target = {}
        try:
            data = json.loads(r["stdout"])
            print("list_windows keys:", list(data)[:10])
        except Exception as e:
            print("list_windows not json:", e)
        k0 = await keydowns(s, pid)
        await sh(s, A.driver_call("press_key", {"key": "w"}))
        await sh(s, "sleep 0.3")
        print("keydowns after untargeted press:", await keydowns(s, pid) - k0)
        await sh(s, A.driver_call("press_key", {"key": "w", "pid": pid}))
        await sh(s, "sleep 0.3")
        print("keydowns after pid-targeted press:", await keydowns(s, pid) - k0)
        await sh(s, A.driver_call("press_key", {"key": "w", "pid": pid, "delivery_mode": "foreground"}))
        await sh(s, "sleep 0.3")
        print("keydowns after foreground press:", await keydowns(s, pid) - k0)
        await sh(s, "DISPLAY=:1 xdotool key w; sleep 0.3")
        print("keydowns after xdotool key:", await keydowns(s, pid) - k0)
        await sh(s, "tail -n 30 /opt/fps-bench/prebuild.log")
    finally:
        await s.close()


asyncio.run(main())
