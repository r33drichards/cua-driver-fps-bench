"""Thin CLI over the pi-cua backend for headless sandbox management.

  scripts/pi_sandbox.py list
  scripts/pi_sandbox.py create <name> [--os linux] [--wait]
  scripts/pi_sandbox.py delete <name>
  scripts/pi_sandbox.py bind <pi-session-id> <name> [--os linux]   # pin a pi session to a sandbox
  scripts/pi_sandbox.py status <operation-id>

The backend lives in the installed pi-cua package and talks to CUA Fleet + Tailscale
using the Keychain credentials pi-cua already uses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(os.environ.get(
    "PI_CUA_BACKEND",
    Path.home() / ".pi/agent/git/github.com/injaneity/pi-cua/backend.py",
))


def pool_for(name: str) -> str:
    """One Fleet pool per sandbox.

    Fleet's reconcile_pool (what pi-cua's `create` calls) recreates the pool's
    namespace, which kills every sandbox already living in that pool — only the
    newest survives. Giving each sandbox its own pool makes parallel sandboxes safe.
    Override with PI_CUA_POOL_PREFIX (default "cua-pi-linux-").
    """
    explicit = os.environ.get("CUA_PI_LINUX_POOL_FIXED")
    if explicit:
        return explicit
    # An existing record wins (e.g. sandboxes created earlier in the shared pool).
    try:
        import sqlite3

        con = sqlite3.connect(str(Path.home() / ".cua/pi-controller/state.sqlite3"))
        row = con.execute("SELECT pool_name FROM sandboxes WHERE name = ?", (name,)).fetchone()
        con.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return f"{os.environ.get('PI_CUA_POOL_PREFIX', 'cua-pi-linux-')}{name}"


def call(request: dict, *, sandbox: str | None = None) -> dict:
    env = dict(os.environ, UV_NO_PROJECT="1")
    if sandbox:
        env["CUA_PI_LINUX_POOL"] = pool_for(sandbox)
    # cwd must not be a uv project: the backend re-execs itself via `uv run --python 3.11`
    # and would otherwise adopt this repo's pyproject (python >=3.12) and fail.
    p = subprocess.run(
        ["python3", str(BACKEND), json.dumps(request)],
        capture_output=True, text=True, cwd=str(Path.home()), env=env,
    )
    if p.returncode != 0:
        raise SystemExit(f"backend failed: {p.stderr.strip() or p.stdout.strip()}")
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    return json.loads(line)


def wait_operation(op_id: str, timeout: float = 1800) -> dict:
    t0 = time.monotonic()
    last = ""
    while time.monotonic() - t0 < timeout:
        st = call({"action": "operation_status", "operation_id": op_id})
        msg = f"{st.get('state')} {st.get('phase') or ''} {st.get('message') or ''}".strip()
        if msg != last:
            print(f"  [{op_id}] {msg}", flush=True)
            last = msg
        if st.get("state") in {"succeeded", "failed", "cancelled"}:
            return st
        time.sleep(5)
    raise SystemExit(f"operation {op_id} timed out")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create"); c.add_argument("name"); c.add_argument("--os", default="linux"); c.add_argument("--wait", action="store_true")
    d = sub.add_parser("delete"); d.add_argument("name")
    b = sub.add_parser("bind"); b.add_argument("session_id"); b.add_argument("name"); b.add_argument("--os", default="linux")
    b.add_argument("--repo", default="", help="local checkout to sync as the workspace (default: this repo)")
    s = sub.add_parser("status"); s.add_argument("operation_id")
    a = p.parse_args()

    if a.cmd == "list":
        print(json.dumps(call({"action": "list"}), indent=2))
    elif a.cmd == "create":
        r = call({"action": "create", "os": a.os, "name": a.name}, sandbox=a.name)
        print(json.dumps(r))
        if a.wait and r.get("operation_id"):
            st = wait_operation(r["operation_id"])
            print(json.dumps(st, indent=2))
            return 0 if st.get("state") == "succeeded" else 1
    elif a.cmd == "delete":
        r = call({"action": "delete", "name": a.name}, sandbox=a.name)
        print(json.dumps(r))
        if r.get("operation_id"):
            print(json.dumps(wait_operation(r["operation_id"]), indent=2))
    elif a.cmd == "bind":
        # pi-cua only honours a saved target that carries localCwd/remoteCwd, i.e. one
        # whose workspace was prepared (clone of this repo's origin at the local commit
        # + uncommitted overlay). Prepare it here (async backend op), then save.
        repo = str(Path(a.repo).resolve()) if a.repo else str(Path(__file__).resolve().parents[1])
        r = call({
            "action": "prepare_execution", "name": a.name, "source_cwd": repo,
            "workspace_id": a.session_id, "tool_packages": ["npm:pi-autoresearch"],
            "include_local_overlay": True,
        }, sandbox=a.name)
        if r.get("operation_id"):
            st = wait_operation(r["operation_id"])
            if st.get("state") != "succeeded":
                raise SystemExit(f"prepare_execution failed: {json.dumps(st)[:800]}")
            r = st.get("result") or {}
        remote_cwd = r.get("remote_cwd")
        if not isinstance(remote_cwd, str):
            raise SystemExit(f"prepare_execution returned no remote_cwd: {json.dumps(r)[:800]}")
        print(json.dumps(call({
            "action": "set_execution_target", "session_id": a.session_id,
            "target": {"kind": "sandbox", "name": a.name, "os": a.os, "localCwd": repo, "remoteCwd": remote_cwd},
        })))
    elif a.cmd == "status":
        print(json.dumps(call({"action": "operation_status", "operation_id": a.operation_id}), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
