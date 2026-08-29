"""Run the FPS benchmark *inside* the sandbox (no host orchestration).

Launches the game with bench_ui (pywebview) on the local X display, drives it
with the CuaDriverAgent through `cua-driver call ...` run as local subprocesses,
and prints pi-autoresearch `METRIC name=value` lines.

  python3 bench/run_in_sandbox.py --episodes 3 [--driver /path/to/cua-driver] [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tasks" / "fps_lshape"))

from fps_bench import agent as agent_mod  # noqa: E402
from fps_bench.agent import CuaDriverAgent, EpisodeRecord, summarize  # noqa: E402
import main as task_module  # noqa: E402


class LocalSession:
    """DesktopSession-like adapter: bench_ui in-process, shell via subprocess."""

    def __init__(self, display: str = ":1"):
        self.env = {**os.environ, "DISPLAY": display}

    async def run_command(self, command: str, *, check: bool = True) -> dict[str, Any]:
        p = await asyncio.create_subprocess_shell(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env
        )
        out, err = await asyncio.wait_for(p.communicate(), timeout=60)
        res = {"stdout": out.decode(errors="replace"), "stderr": err.decode(errors="replace"), "return_code": p.returncode}
        if check and p.returncode != 0:
            raise RuntimeError(f"command failed ({p.returncode}): {command}\n{res['stderr']}")
        return res

    async def launch_window(self, *, html: str, title: str, width: int, height: int) -> int:
        from bench_ui import launch_window

        folder = Path("/tmp/fps_bench_game")
        folder.mkdir(exist_ok=True)
        (folder / "index.html").write_text(html)
        os.environ["DISPLAY"] = self.env["DISPLAY"]
        return int(launch_window(folder=str(folder), title=title, width=width, height=height))

    async def execute_javascript(self, pid: int | str, javascript: str) -> Any:
        from bench_ui import execute_javascript

        return await asyncio.to_thread(execute_javascript, int(pid), javascript)

    async def close_window(self, pid: int) -> None:
        subprocess.run(["kill", str(pid)], check=False)


async def amain(args: argparse.Namespace) -> int:
    if args.driver:
        agent_mod.DRIVER_BIN = args.driver
    session = LocalSession(display=args.display)
    agent = CuaDriverAgent(max_presses=args.max_presses)
    version = await session.run_command(f"{agent_mod.DRIVER_BIN} --version", check=False)
    # Stale game windows from earlier runs would be matched by title; kill them first.
    await session.run_command("pkill -f bench_ui.child || true", check=False)
    await asyncio.sleep(0.5)
    pid = await session.launch_window(
        html=task_module.game_html(), title=task_module.WINDOW_TITLE,
        width=task_module.WINDOW_W, height=task_module.WINDOW_H,
    )
    await asyncio.sleep(2)
    records: list[EpisodeRecord] = []
    t0 = time.monotonic()
    try:
        for i in range(args.episodes):
            if i:
                await session.execute_javascript(pid, "window.__reset()")
            rec = await agent.run_episode(session, pid)
            records.append(rec)
            print(f"episode {i + 1}/{args.episodes}: reached={rec.reached} progress={rec.progress:.2f} "
                  f"presses={rec.presses} keydowns={rec.keydowns} falls={rec.falls} {rec.failure}", flush=True)
    finally:
        await session.close_window(pid)
    s = summarize(records)
    s["cua_driver_version"] = (version["stdout"] or version["stderr"]).strip()
    s["bench_seconds"] = time.monotonic() - t0
    s["records"] = [r.to_dict() for r in records]
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(s, indent=2))
    # pi-autoresearch contract
    print(f"METRIC score={s['score']:.4f}")
    print(f"METRIC delivery_ratio={s['delivery_ratio']:.4f}")
    print(f"METRIC mouse_ratio={s['mouse_ratio']:.4f}")
    print(f"METRIC mean_mouse_moves={s['mean_mouse_moves']:.1f}")
    print(f"METRIC mean_progress={s['mean_progress']:.4f}")
    print(f"METRIC mean_presses={s['mean_presses']:.1f}")
    print(f"METRIC falls={s['falls']}")
    print(f"METRIC driver_errors={s['driver_errors']}")
    print(f"METRIC bench_seconds={s['bench_seconds']:.1f}")
    print(f"driver: {s['cua_driver_version']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--episodes", type=int, default=3)
    p.add_argument("--max-presses", type=int, default=60)
    p.add_argument("--display", default=os.environ.get("DISPLAY", ":1"))
    p.add_argument("--driver", default=os.environ.get("CUA_DRIVER_BIN", ""))
    p.add_argument("--json", default="")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
