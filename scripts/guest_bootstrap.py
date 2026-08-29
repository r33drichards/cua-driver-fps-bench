"""Run bench/bootstrap_guest.sh as root on a sandbox through its computer-server,
then inject runtime secrets (GitHub token for the private origin).

pi-cua's `cua` user may have no sudo; computer-server (port 8000, root) can do the
privileged setup (apt deps, bench_ui/cua-bench, tmpfs, X access, sudoers for cua)
before a pi session starts. Secrets follow
https://cua.ai/docs/how-to-guides/sandbox/secrets: injected at runtime over the
shell, never baked into the image.

  .venv/bin/python scripts/guest_bootstrap.py --api-url http://<tailscale-ip>:8000
  .venv/bin/python scripts/guest_bootstrap.py --sandbox fps-c      # resolves the live tailnet IP
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fps_bench.runner import api_url_session, ensure_guest_bootstrap  # noqa: E402

ORIGIN = "https://github.com/trycua/cua-driver-fps-bench.git"


async def inject_github_credentials(s) -> None:
    """A GitHub token from the host (GH_SANDBOX_TOKEN, else `gh auth token`) becomes a
    git credential-store entry (0600) for user `cua`, so pi-cua's workspace clone of the
    private origin works. Use a fine-grained read-only PAT for GH_SANDBOX_TOKEN if possible."""
    token = os.environ.get("GH_SANDBOX_TOKEN") or subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True
    ).stdout.strip()
    if not token:
        print("github creds: no token available (set GH_SANDBOX_TOKEN or `gh auth login`)")
        return
    await s.write_file("/home/cua/.git-credentials", f"https://x-access-token:{token}@github.com\n")
    await s.run_command(
        "chown cua:cua /home/cua/.git-credentials && chmod 600 /home/cua/.git-credentials && "
        "sudo -u cua git config --global credential.helper store && "
        "sudo -u cua git config --global url.https://github.com/.insteadOf git@github.com:",
        check=False,
    )
    r = await s.run_command(f"sudo -u cua git ls-remote -h {ORIGIN} main | wc -l", check=False)
    ok = (r.get("stdout") or "").strip() == "1"
    print("github creds:", "OK (private origin reachable as cua)" if ok else f"FAILED {(r.get('stderr') or '')[-200:]}")


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
        await inject_github_credentials(s)
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
