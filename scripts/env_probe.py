"""Probe a computer-server env: who runs commands, display access, does a bench_ui window survive.

  .venv/bin/python scripts/env_probe.py --api-url http://100.88.180.1:8000
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench.runner import api_url_session, task_module  # noqa: E402


async def sh(s, cmd):
    r = await s.run_command(cmd, check=False)
    print(f"$ {cmd}\n  rc={r.get('return_code')} out={(r.get('stdout') or '')[:600]!r} err={(r.get('stderr') or '')[:300]!r}")
    return r


async def main(api_url: str) -> int:
    s = await api_url_session(api_url)
    try:
        await sh(s, "id; echo DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY HOME=$HOME; which xhost xdotool; python3 -c 'import bench_ui, webview; print(\"bench_ui ok\")'")
        await sh(s, "DISPLAY=:1 xdotool getdisplaygeometry 2>&1 || true")
        pid = await s.launch_window(html=task_module.game_html(), title="probe", width=400, height=300)
        print("launch_window pid", pid)
        await sh(s, f"sleep 3; ps -o user,pid,etime,cmd -p {pid} | tail -1; python3 -c \"import psutil;p=psutil.Process({pid});print([c.laddr for c in p.net_connections() if c.status=='LISTEN'])\" 2>&1 | tail -1")
        await sh(s, "DISPLAY=:1 xdotool search --name probe 2>&1 | head -3 || true")
        try:
            print("js:", await s.execute_javascript(pid, "1+1"))
        except Exception as e:
            print("execute_javascript failed:", repr(e)[:300])
        await sh(s, f"kill {pid} 2>/dev/null; ls -la /tmp/*.log 2>/dev/null | tail -3; journalctl --no-pager -n 5 2>/dev/null | tail -3 || true")
    finally:
        await s.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", required=True)
    raise SystemExit(asyncio.run(main(p.parse_args().api_url)))
