"""Run bench/bootstrap_guest.sh as root on a sandbox through its computer-server.

pi-cua's `cua` user may have no sudo; computer-server (port 8000, root) can do the
privileged setup (apt deps, bench_ui/cua-bench, tmpfs, X access, sudoers for cua)
before a pi session starts.

  .venv/bin/python scripts/guest_bootstrap.py --api-url http://<tailscale-ip>:8000
  .venv/bin/python scripts/guest_bootstrap.py --sandbox fps-c      # resolves the live tailnet IP
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fps_bench.runner import api_url_session, ensure_guest_bootstrap  # noqa: E402


async def main(a) -> int:
    api_url = a.api_url
    if not api_url:
        from pi_sandbox import live_tailscale_ip

        ip = live_tailscale_ip(a.sandbox)
        if not ip:
            raise SystemExit(f"no online tailnet node for {a.sandbox}")
        api_url = f"http://{ip}:8000"
    s = await api_url_session(api_url)
    try:
        r = await s.run_command("id -un; test -f /etc/sudoers.d/90-cua-fps-bench && echo SUDOERS_OK || echo NO_SUDOERS", check=False)
        print("before:", (r.get("stdout") or "").strip().replace("\n", " "))
        # Force a (re)run when the sudoers entry is missing even if deps look present.
        force = "NO_SUDOERS" in (r.get("stdout") or "")
        out = await ensure_guest_bootstrap(s, timeout=a.timeout, force=force)
        print("bootstrap:", out.strip()[-300:])
        r = await s.run_command("sudo -n -u cua sudo -n true && echo CUA_SUDO_OK; sudo -u cua python3 -c 'import bench_ui, cua_bench; print(\"py-ok\")'; mountpoint -q /mnt/fps-target && echo TMPFS_OK", check=False)
        print("after:", (r.get("stdout") or "").strip().replace("\n", " "), (r.get("stderr") or "")[-200:])
    finally:
        await s.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--api-url")
    g.add_argument("--sandbox")
    p.add_argument("--timeout", type=float, default=1500)
    raise SystemExit(asyncio.run(main(p.parse_args())))
