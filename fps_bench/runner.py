"""Run the FPS benchmark (task + CuaDriverAgent) against a desktop session.

Two session backends:
  * local docker (cua-bench's RemoteDesktopSession, full lifecycle)
  * a Fleet sandbox (cua_sandbox.Sandbox; bench_ui is driven through shell.run)

CLI:
  python -m fps_bench.runner --local --episodes 3
  python -m fps_bench.runner --sandbox-ref results/sandbox.json --episodes 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from fps_bench.agent import CuaDriverAgent, EpisodeRecord, summarize

TASK_DIR = Path(__file__).resolve().parents[1] / "tasks" / "fps_lshape"
sys.path.insert(0, str(TASK_DIR))
import main as task_module  # noqa: E402  (tasks/fps_lshape/main.py)


class FleetSession:
    """Minimal DesktopSession-like adapter over a cua_sandbox.Sandbox.

    Uses the sandbox's computer-server ``run_command`` for everything, including
    bench_ui window control, so no extra service exposure is needed.
    """

    def __init__(self, sb: Any, *, command_timeout: int = 25):
        self.sb = sb
        self.command_timeout = command_timeout

    async def run_command(self, command: str, *, check: bool = True) -> dict[str, Any]:
        res = await self.sb.shell.run(command, timeout=self.command_timeout)
        out = {"stdout": res.stdout, "stderr": res.stderr, "return_code": res.returncode}
        if check and res.returncode != 0:
            raise RuntimeError(f"command failed ({res.returncode}): {command}\n{res.stderr}")
        return out

    async def _py(self, code: str) -> str:
        res = await self.run_command(f"DISPLAY=:1 python3 -c {shlex.quote(code)}")
        return (res["stdout"] or "").strip().splitlines()[-1] if res["stdout"] else ""

    async def launch_window(self, *, html: str, title: str, width: int, height: int) -> int:
        folder = "/tmp/fps_bench_game"
        await self.run_command(f"mkdir -p {folder}")
        await self.sb.files.write_text(f"{folder}/index.html", html)
        out = await self._py(
            "from bench_ui import launch_window; "
            f"print(launch_window(folder={folder!r}, title={title!r}, width={width}, height={height}))"
        )
        return int(out)

    async def execute_javascript(self, pid: int | str, javascript: str) -> Any:
        out = await self._py(
            "import json; from bench_ui import execute_javascript; "
            f"print(json.dumps(execute_javascript({int(pid)}, {javascript!r})))"
        )
        return json.loads(out) if out else None

    async def close_window(self, pid: int) -> None:
        await self.run_command(f"kill {int(pid)} || true", check=False)


PREBUILD_STAMP = "/opt/fps-bench/prebuild.done"


async def wait_prebuild(session: Any, *, timeout: float = 1500, poll: float = 10) -> str:
    """Wait for the image's boot-time cua-driver build (no-op on images without it)."""
    probe = await session.run_command("test -x /opt/fps-bench/prebuild.sh && echo HAS || echo NO", check=False)
    if "HAS" not in (probe.get("stdout") or ""):
        return "no prebuild in image"
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = await session.run_command(f"test -f {PREBUILD_STAMP} && echo DONE || echo WAIT", check=False)
        if "DONE" in (r.get("stdout") or ""):
            log = await session.run_command("tail -n 3 /opt/fps-bench/prebuild.log", check=False)
            return log.get("stdout") or ""
        await asyncio.sleep(poll)
    raise TimeoutError("cua-driver prebuild did not finish in time")


async def run_benchmark(
    session: Any, *, episodes: int, agent_kwargs: dict[str, Any] | None = None, label: str = ""
) -> dict[str, Any]:
    """Launch the game once, then run ``episodes`` agent episodes (reset between)."""
    agent = CuaDriverAgent(**(agent_kwargs or {}))
    print(f"[{label}] prebuild: {(await wait_prebuild(session)).strip()[-200:]}", flush=True)
    pid = await session.launch_window(
        html=task_module.game_html(),
        title=task_module.WINDOW_TITLE,
        width=task_module.WINDOW_W,
        height=task_module.WINDOW_H,
    )
    # Give the webview a moment to render and focus the game page.
    await session.run_command("sleep 2", check=False)
    version = await session.run_command("cua-driver --version", check=False)
    records: list[EpisodeRecord] = []
    for i in range(episodes):
        if i:
            await session.execute_javascript(pid, "window.__reset()")
        rec = await agent.run_episode(session, pid)
        records.append(rec)
        print(f"[{label}] episode {i + 1}/{episodes}: reached={rec.reached} progress={rec.progress:.2f} "
              f"presses={rec.presses} keydowns={rec.keydowns} falls={rec.falls} {rec.failure}", flush=True)
    summary = summarize(records)
    summary["cua_driver_version"] = (version.get("stdout") or version.get("stderr") or "").strip()
    summary["label"] = label
    summary["timestamp"] = time.time()
    summary["records"] = [r.to_dict() for r in records]
    close = getattr(session, "close_window", None)
    if close:
        await close(pid)
    return summary


async def local_session() -> Any:
    from cua_bench.computers.remote import RemoteDesktopSession

    cfg = task_module.load()[0].computer["setup_config"]
    session = RemoteDesktopSession(
        os_type="linux",
        image=cfg["image"],
        provider_type="docker",
        width=cfg["width"],
        height=cfg["height"],
    )
    await session.start()
    return session


async def api_url_session(api_url: str) -> Any:
    """Client-only session against a reachable computer-server (e.g. a pi-cua sandbox over Tailscale)."""
    from cua_bench.computers.remote import RemoteDesktopSession

    cfg = task_module.load()[0].computer["setup_config"]
    session = RemoteDesktopSession(api_url=api_url, os_type="linux", width=cfg["width"], height=cfg["height"])
    await session.start()
    return session


async def ensure_guest_bootstrap(session: Any, *, timeout: float = 1500) -> str:
    """Run bench/bootstrap_guest.sh on the env (detached via systemd-run) and wait for it."""
    script = (Path(__file__).resolve().parents[1] / "bench" / "bootstrap_guest.sh").read_text()
    probe = await session.run_command("python3 -c 'import bench_ui' >/dev/null 2>&1 && echo HAS || echo NO", check=False)
    if "HAS" in (probe.get("stdout") or ""):
        return "bench_ui present"
    await session.write_file("/tmp/fps-bootstrap.sh", script)
    launch = ("if [ \"$(id -u)\" -eq 0 ]; then S=; else S=sudo; fi; $S rm -f /tmp/fps-bootstrap.rc; "
              "$S systemd-run --collect --unit=fps-bootstrap sh -c 'bash /tmp/fps-bootstrap.sh >/tmp/fps-bootstrap.log 2>&1; echo $? >/tmp/fps-bootstrap.rc'")
    await session.run_command(launch, check=False)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = await session.run_command("cat /tmp/fps-bootstrap.rc 2>/dev/null || echo RUNNING", check=False)
        out = (r.get("stdout") or "").strip()
        if out and out != "RUNNING":
            log = await session.run_command("tail -n 5 /tmp/fps-bootstrap.log", check=False)
            if out.splitlines()[-1] != "0":
                raise RuntimeError(f"guest bootstrap failed: {log.get('stdout')}")
            return log.get("stdout") or ""
        await asyncio.sleep(10)
    raise TimeoutError("guest bootstrap timed out")


async def sandbox_session(ref_path: Path) -> FleetSession:
    from cua_sandbox import Sandbox

    from fps_bench.fleet import configure_auth

    configure_auth()
    sb = await Sandbox.from_dict(json.loads(ref_path.read_text()))
    return FleetSession(sb)


async def amain(args: argparse.Namespace) -> int:
    if args.local:
        session = await local_session()
        closer = session.close
    elif args.api_url:
        session = await api_url_session(args.api_url)
        closer = session.close
        print("bootstrap:", (await ensure_guest_bootstrap(session)).strip()[-200:], flush=True)
    else:
        session = await sandbox_session(Path(args.sandbox_ref))
        closer = session.sb.disconnect
    try:
        summary = await run_benchmark(session, episodes=args.episodes, label=args.label)
    finally:
        await closer()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--local", action="store_true", help="run in a local docker desktop")
    g.add_argument("--sandbox-ref", help="JSON file from Sandbox.to_dict() of a live Fleet sandbox")
    g.add_argument("--api-url", help="computer-server URL of a reachable env, e.g. http://100.88.180.1:8000 (pi-cua sandbox over Tailscale)")
    p.add_argument("--episodes", type=int, default=3)
    p.add_argument("--label", default="manual")
    p.add_argument("--out", default="results/runs/manual.json")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
