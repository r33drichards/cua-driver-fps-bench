"""Compare input delivery paths into the game window on an env: xdotool vs cua-driver.

  .venv/bin/python scripts/key_probe.py --api-url http://100.88.180.1:8000 [--driver /path/cua-driver]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import agent as A  # noqa: E402
from fps_bench.runner import api_url_session, task_module  # noqa: E402


async def sh(s, cmd):
    r = await s.run_command(cmd, check=False)
    print(f"$ {cmd[:110]}\n  rc={r.get('return_code')} out={(r.get('stdout') or '')[:400]!r} err={(r.get('stderr') or '')[:200]!r}")
    return r


async def kd(s, pid):
    st = json.loads(await s.execute_javascript(pid, "JSON.stringify(window.__state)"))
    return st["keydowns"], st["mousemoves"], round(st["mouse_dx"], 1), st["locked"]


async def main(a) -> int:
    if a.driver:
        A.DRIVER_BIN = a.driver
    s = await api_url_session(a.api_url)
    try:
        pid = await s.launch_window(html=task_module.game_html(), title="L-Platform", width=800, height=600)
        await sh(s, "sleep 2")
        await sh(s, f"{A.DRIVER_BIN} --version")
        wid = (await sh(s, "DISPLAY=:1 xdotool search --name L-Platform | tail -1"))["stdout"].strip()
        print("window pid", pid, "xid", wid, "state", await kd(s, pid))

        await sh(s, f"DISPLAY=:1 xdotool windowactivate --sync {wid} 2>/dev/null; DISPLAY=:1 xdotool key --window {wid} w; sleep 0.3")
        print("after xdotool key --window:", await kd(s, pid))
        await sh(s, "DISPLAY=:1 xdotool key w; sleep 0.3")
        print("after xdotool key (focused):", await kd(s, pid))
        await sh(s, "DISPLAY=:1 xdotool mousemove 400 300 mousemove 500 300; sleep 0.3")
        print("after xdotool mousemove:", await kd(s, pid))

        await sh(s, A.driver_call("list_windows", {}))
        for args in ({"key": "w"}, {"key": "w", "pid": pid}, {"key": "w", "pid": pid, "delivery_mode": "foreground"}):
            await sh(s, A.driver_call("press_key", args))
            await sh(s, "sleep 0.3")
            print(f"after cua-driver press_key {args}:", await kd(s, pid))
        await sh(s, A.driver_call("click", {"x": 400, "y": 300}))
        await sh(s, A.driver_call("move_cursor", {"x": 500, "y": 300}))
        await sh(s, "sleep 0.3")
        print("after cua-driver click+move_cursor:", await kd(s, pid))
        await sh(s, f"kill {pid}")
    finally:
        await s.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", required=True)
    p.add_argument("--driver", default="")
    raise SystemExit(asyncio.run(main(p.parse_args())))
