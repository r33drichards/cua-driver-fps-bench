"""Fleet helpers: gVisor container pool, claims, and long-running exec.

cua_sandbox's ``Pool.apply`` cannot set the sandbox runtime, and our benchmark
image is a plain container (not a KubeVirt containerDisk), so the template and
pool are reconciled directly through ``fleet_sdk`` with ``runtime = GVISOR``.
Claims then go through ``Sandbox.create(pool=...)`` as usual.

Fleet's pool-admission policy only admits images from an allowlist of
repositories (see libs/fleet/backend/auth/pool_admission.rego); the benchmark
image is pushed as a tag of ``cua-gymdriver-dev`` for that reason.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import time
from typing import Any

DEFAULT_POOL = os.environ.get("FPS_BENCH_POOL", "fps-bench-cua-driver")
KEYCHAIN_SERVICE = "cua-sandbox-fleet-api"
CUA_DRIVER_SRC = "/opt/cua/libs/cua-driver"
PREBUILD_STAMP = "/opt/fps-bench/prebuild.done"


def _keychain(account: str) -> str | None:
    try:
        return subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def configure_auth() -> None:
    """Populate CUA_CLIENT_ID/SECRET from the macOS Keychain (same entry pi-cua uses)."""
    for env, account in (("CUA_CLIENT_ID", "client-id"), ("CUA_CLIENT_SECRET", "client-secret")):
        if not os.environ.get(env):
            value = _keychain(account)
            if value:
                os.environ[env] = value
    missing = [e for e in ("CUA_CLIENT_ID", "CUA_CLIENT_SECRET") if not os.environ.get(e)]
    if missing:
        raise RuntimeError(f"Fleet credentials missing: {missing} (env or Keychain '{KEYCHAIN_SERVICE}')")


def _needs_pull_secret(image: str) -> bool:
    return ".dkr.ecr." in image


async def ensure_pool(
    image: str,
    *,
    name: str = DEFAULT_POOL,
    cpu: int = 4,
    memory_mb: int = 8192,
    min_size: int = 0,
    initial_size: int = 2,
    max_size: int = 8,
) -> Any:
    """Reconcile a gVisor-runtime pool (and its template) for ``image``; returns cua_sandbox.Pool."""
    from cua_sandbox.pool import Pool, Template
    from fleet_sdk import (
        CreatePoolRequestBuilder,
        CreateTemplateRequestBuilder,
        OsGymSandboxTemplateSpecBuilder,
        OsGymSandboxWarmPoolSpecBuilder,
        PreservedJson,
        RuntimeKind,
        SandboxServiceBuilder,
        SandboxTemplateRefBuilder,
        ServiceProtocol,
        VmTemplateBuilder,
        WarmPoolAutoscaling,
    )

    services = [
        SandboxServiceBuilder().name("server").target_port(8000).protocol(ServiceProtocol.TCP).build(),
        SandboxServiceBuilder().name("mcp").target_port(3000).protocol(ServiceProtocol.TCP).build(),
        SandboxServiceBuilder().name("novnc").target_port(6080).protocol(ServiceProtocol.TCP).build(),
    ]
    vm = (
        VmTemplateBuilder()
        .container_disk_image(image)
        .runtime(RuntimeKind.GVISOR)
        .cpu_cores(cpu)
        .memory(f"{memory_mb}Mi")
        .probes(PreservedJson.from_json(json.dumps({"readinessProbe": {"tcpSocket": {"port": 8000}}})))
        .services(services)
    )
    if _needs_pull_secret(image):
        vm = vm.image_pull_secret("ecr-credentials")
    template_req = (
        CreateTemplateRequestBuilder()
        .namespace(name)
        .name(name)
        .spec(OsGymSandboxTemplateSpecBuilder().vm_template(vm.build()).build())
        .build()
    )
    autoscaling = WarmPoolAutoscaling(
        min_pool_size=min_size, initial_pool_size=initial_size, max_pool_size=max_size
    )
    pool_req = (
        CreatePoolRequestBuilder()
        .namespace(name)
        .spec(
            OsGymSandboxWarmPoolSpecBuilder()
            .replicas(initial_size)
            .sandbox_template_ref(SandboxTemplateRefBuilder().name(name).build())
            .autoscaling(autoscaling)
            .build()
        )
        .build()
    )
    last_error: Exception | None = None
    for attempt in range(12):
        try:
            pool = await Pool.reconcile(pool_req)
            template = await Template.reconcile(template_req)
            pool._owned_template = template.resource
            return pool
        except Exception as e:  # namespace converging / transient 403 right after creation
            last_error = e
            text = repr(e)
            if "NamespaceTerminating" in text or "status=403" in text or "not allowed" in text:
                print(f"[fleet] pool {name} reconcile attempt {attempt + 1} failed transiently: {text[:160]}", flush=True)
                await asyncio.sleep(10)
                continue
            raise
    raise RuntimeError(f"pool {name} did not reconcile: {last_error!r}")


async def claim(pool: Any, name: str, *, time_to_start: float = 900) -> Any:
    """Claim a sandbox from ``pool``; returns a connected cua_sandbox.Sandbox."""
    from cua_sandbox import Sandbox

    return await Sandbox.create(pool=pool, name=name, service="server", time_to_start=time_to_start)


async def release(sb: Any) -> None:
    try:
        await sb.close()  # idempotent claim release
    except Exception as e:
        print(f"[fleet] release failed for {getattr(sb, 'claim_name', '?')}: {e!r}", flush=True)


async def run_detached(sb: Any, script: str, *, name: str, timeout: float, poll: float = 5.0) -> tuple[int, str]:
    """Run a long shell script in the sandbox and poll for completion.

    The Fleet exec gateway drops any single run_command after ~30 s, so the job
    is started with nohup/setsid and polled through short commands.
    """
    base = f"/tmp/fps-job-{name}"
    await sb.files.write_text(f"{base}.sh", script)
    launch = (
        f"rm -f {base}.rc; setsid nohup bash -c 'bash {base}.sh >{base}.log 2>&1; echo $? >{base}.rc' "
        f">/dev/null 2>&1 < /dev/null & echo started"
    )
    res = await sb.shell.run(launch, timeout=20)
    if res.returncode != 0 or "started" not in (res.stdout or ""):
        # Some guests reject backgrounded children from run_command; fall back to the pty path.
        await sb.shell.run(f"bash -c 'bash {base}.sh >{base}.log 2>&1; echo $? >{base}.rc'", background=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = await sb.shell.run(f"cat {base}.rc 2>/dev/null || echo RUNNING", timeout=15)
        out = (r.stdout or "").strip()
        if out and out != "RUNNING":
            log = await sb.shell.run(f"tail -c 20000 {base}.log", timeout=15)
            return int(out.splitlines()[-1]), log.stdout or ""
        await asyncio.sleep(poll)
    log = await sb.shell.run(f"tail -c 20000 {base}.log", timeout=15)
    return 124, (log.stdout or "") + "\n[timeout]"


async def wait_prebuild(sb: Any, *, timeout: float = 1800) -> str:
    """Wait for the image's boot-time cua-driver build to finish; returns the log tail."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = await sb.shell.run(f"test -f {PREBUILD_STAMP} && echo DONE || echo WAIT", timeout=15)
        if "DONE" in (r.stdout or ""):
            log = await sb.shell.run("tail -n 5 /opt/fps-bench/prebuild.log", timeout=15)
            return log.stdout or ""
        await asyncio.sleep(10)
    raise TimeoutError("cua-driver prebuild did not finish in time")


def build_script(diff: str) -> str:
    """Shell script that applies ``diff`` (may be empty) and rebuilds/installs cua-driver."""
    return f"""set -euo pipefail
export CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup PATH=/usr/local/cargo/bin:$PATH
cd {CUA_DRIVER_SRC}
git checkout -- . && git clean -fdq -- rust/crates || true
if [ -s /tmp/fps-patch.diff ]; then
  git apply --whitespace=nowarn /tmp/fps-patch.diff
  echo "patch applied: $(git diff --stat | tail -n1)"
else
  echo "no patch (baseline)"
fi
cd rust && cargo build --release -p cua-driver
install -m 0755 target/release/cua-driver /usr/local/bin/cua-driver
supervisorctl restart cua-driver-mcp >/dev/null 2>&1 || true
cua-driver --version
echo BUILD_OK
"""


async def apply_and_build(sb: Any, diff: str, *, name: str, timeout: float = 1500) -> tuple[bool, str]:
    await sb.files.write_text("/tmp/fps-patch.diff", diff or "")
    rc, log = await run_detached(sb, build_script(diff), name=f"build-{name}", timeout=timeout)
    return rc == 0 and "BUILD_OK" in log, log


def sandbox_ref_json(sb: Any) -> str:
    return json.dumps(sb.to_dict())


def q(s: str) -> str:
    return shlex.quote(s)
