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
    """Reconcile a gVisor-runtime pool (and its template) for ``image``; returns cua_sandbox.Pool.

    History (2026-08-29): this path was broken twice in the osgym pool-operator —
    gVisor pods had no ``resources`` (BestEffort → runsc sandbox recycled ~80 s after
    start; fixed by trycua/cloud#7268) and pod-runtime sandboxes got no per-service
    k8s Services, so the gateway's ``/api/svc`` 502'd (fixed by #7269). Both are
    merged and rolled out; e2e: pods stay Ready for hours, claims bind in ~3 min,
    ``shell.run`` works. The "prebuild timeout" seen on 2026-08-29 was the image's
    fault, not gVisor's: the base image's supervisord.conf has no ``[include]``, so the
    conf.d program never ran. Fixed in image tag ``cua-driver-bench-20260830-*``; the
    full release build + install takes ~60 s on a gVisor sandbox (16 vCPU).
    """
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


VM_IMAGE = os.environ.get(
    "FPS_BENCH_VM_IMAGE", "public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:main-38352d34"
)
BOOTSTRAP_STAMP = "/opt/fps-bench/bootstrap.done"


async def ensure_vm_pool(
    image: str = VM_IMAGE,
    *,
    name: str = DEFAULT_POOL,
    cpu: int = 4,
    memory_mb: int = 8192,
    min_size: int = 0,
    initial_size: int = 2,
    max_size: int = 8,
) -> Any:
    """Reconcile a KubeVirt VM pool (the path that works today; see ensure_pool docstring)."""
    from cua_sandbox import Image, WarmPoolAutoscaling
    from cua_sandbox.pool import Pool

    last_error: Exception | None = None
    for attempt in range(12):
        try:
            return await Pool.apply(
                Image.from_registry(image, os_type="linux", kind="vm"),
                name=name, cpu=cpu, memory_mb=memory_mb, services={"server": 8000},
                autoscaling=WarmPoolAutoscaling(
                    min_pool_size=min_size, initial_pool_size=initial_size, max_pool_size=max_size
                ),
            )
        except Exception as e:
            last_error = e
            text = repr(e)
            if "NamespaceTerminating" in text or "status=403" in text or "not allowed" in text:
                print(f"[fleet] pool {name} reconcile attempt {attempt + 1} failed transiently: {text[:160]}", flush=True)
                await asyncio.sleep(10)
                continue
            raise
    raise RuntimeError(f"pool {name} did not reconcile: {last_error!r}")


def bootstrap_script(cua_ref: str) -> str:
    """Idempotent VM bootstrap: build deps, bench_ui, Rust, sparse clone of cua-driver, first build.

    Mirrors what image/Dockerfile bakes in for the container image; on the VM
    path it runs once per sandbox (≈10-15 min) and stamps BOOTSTRAP_STAMP.
    """
    return f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
export PATH=/usr/local/cargo/bin:$PATH
mkdir -p /opt/fps-bench
if ! command -v cargo >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y --no-install-recommends build-essential pkg-config git ca-certificates curl \\
    libx11-dev libxi-dev libxtst-dev libxext-dev libwayland-dev libxkbcommon-dev \\
    python3-gi gir1.2-webkit2-4.1 gir1.2-gtk-3.0 python3-pip xdotool
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path --default-toolchain 1.97.1 --profile minimal
fi
python3 -c 'import bench_ui' 2>/dev/null || python3 -m pip install --break-system-packages --no-cache-dir cua-bench-ui pywebview
if [ ! -d {CUA_DRIVER_SRC} ]; then
  git clone --filter=blob:none --sparse https://github.com/trycua/cua.git /opt/cua
  cd /opt/cua && git sparse-checkout set libs/cua-driver && git checkout --detach {cua_ref}
fi
cd {CUA_DRIVER_SRC}/rust && cargo build --release -p cua-driver
install -m 0755 target/release/cua-driver /usr/local/bin/cua-driver
cua-driver --version
touch {BOOTSTRAP_STAMP}
echo BOOTSTRAP_OK
"""


async def ensure_bootstrapped(sb: Any, cua_ref: str, *, timeout: float = 2400) -> str:
    """Run bootstrap_script once per sandbox (no-op when the stamp exists)."""
    r = await sb.shell.run(f"test -f {BOOTSTRAP_STAMP} && echo DONE || echo NEED", timeout=15)
    if "DONE" in (r.stdout or ""):
        return "already bootstrapped"
    rc, log = await run_detached(sb, bootstrap_script(cua_ref), name="bootstrap", timeout=timeout, poll=15)
    if rc != 0 or "BOOTSTRAP_OK" not in log:
        raise RuntimeError(f"bootstrap failed rc={rc}: {log[-2000:]}")
    return log[-300:]


async def run_detached(sb: Any, script: str, *, name: str, timeout: float, poll: float = 5.0) -> tuple[int, str]:
    """Run a long shell script in the sandbox and poll for completion.

    The Fleet exec gateway drops any single run_command after ~30 s, and the
    guest command server rejects commands that leave a backgrounded child, so
    the job is detached through systemd-run (falling back to setsid/nohup) and
    polled with short commands — the same contract pi-cua uses.
    """
    base = f"/tmp/fps-job-{name}"
    unit = f"fps-{name}"
    await sb.files.write_text(f"{base}.sh", script)
    sudo = 'if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO=sudo; fi; '
    launch = (
        sudo + f"$SUDO systemctl stop {unit}.service 2>/dev/null; $SUDO systemctl reset-failed {unit}.service 2>/dev/null; "
        f"rm -f {base}.rc {base}.log; "
        f"$SUDO systemd-run --collect --unit={unit} sh -c 'bash {base}.sh >{base}.log 2>&1; echo $? >{base}.rc' && echo started"
    )
    res = await sb.shell.run(launch, timeout=20)
    if res.returncode != 0 or "started" not in (res.stdout or ""):
        fallback = (
            f"rm -f {base}.rc; setsid nohup bash -c 'bash {base}.sh >{base}.log 2>&1; echo $? >{base}.rc' "
            f">/dev/null 2>&1 < /dev/null & echo started"
        )
        res = await sb.shell.run(fallback, timeout=20)
        if res.returncode != 0 or "started" not in (res.stdout or ""):
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
