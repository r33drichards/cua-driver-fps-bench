"""Inspect a kept Fleet sandbox (from Sandbox.to_dict() JSON): prebuild state, cargo, resources.

  .venv/bin/python scripts/sandbox_inspect.py results/runs/smoke.sandbox.json [--watch N]
  .venv/bin/python scripts/sandbox_inspect.py results/runs/smoke.sandbox.json --sh "cmd"
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402

CHECKS = [
    ("uptime/load", "uptime; nproc; free -m | sed -n 2p"),
    ("disk", "df -h / /opt /tmp 2>/dev/null | tail -3"),
    ("supervisor", "supervisorctl status 2>&1 | head -8"),
    ("prebuild stamp/log", "ls -la /opt/fps-bench/ 2>&1; tail -n 8 /opt/fps-bench/prebuild.log 2>&1 | cut -c1-200"),
    ("cargo procs", "ps -eo pid,etime,pcpu,rss,args --sort=-pcpu | grep -E 'cargo|rustc|build-script|cc1|ld' | grep -v grep | head -8 | cut -c1-160"),
    ("target progress", "ls /opt/cua/libs/cua-driver/rust/target/release/deps 2>/dev/null | wc -l; du -sh /opt/cua/libs/cua-driver/rust/target 2>/dev/null"),
    ("cgroup limits", "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max 2>/dev/null; cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null"),
    ("kernel", "uname -r; cat /proc/version | cut -c1-80"),
]


async def run(sb, cmd, timeout=25):
    r = await sb.shell.run(cmd, timeout=timeout)
    return (r.stdout or "") + (("\n[stderr] " + r.stderr[-300:]) if r.stderr else "")


async def main(a) -> int:
    fleet.configure_auth()
    from cua_sandbox import Sandbox

    sb = await Sandbox.from_dict(json.loads(Path(a.ref).read_text()))
    try:
        if a.bg:
            # The guest command server refuses backgrounded children ('&', nohup, setsid);
            # the SDK's pty path is the sanctioned way to start a long job.
            r = await sb.shell.run(a.bg, background=True)
            print("started pid", r.stdout)
            return 0
        if a.sh:
            print(await run(sb, a.sh, timeout=a.timeout))
            return 0
        for i in range(max(1, a.watch)):
            print(f"===== {time.strftime('%H:%M:%S')} =====")
            for name, cmd in CHECKS:
                out = await run(sb, cmd)
                print(f"--- {name}\n{out.strip()}")
            if i + 1 < a.watch:
                await asyncio.sleep(a.interval)
    finally:
        await sb.disconnect()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("ref")
    p.add_argument("--watch", type=int, default=1, help="repeat N times")
    p.add_argument("--interval", type=float, default=60)
    p.add_argument("--sh", default="", help="run a single command instead of the checks")
    p.add_argument("--bg", default="", help="start a long-running command via the pty path and return")
    p.add_argument("--timeout", type=int, default=25)
    raise SystemExit(asyncio.run(main(p.parse_args())))
