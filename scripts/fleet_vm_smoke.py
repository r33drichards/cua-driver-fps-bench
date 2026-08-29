"""Smoke-test the KubeVirt VM path: stock cua-ubuntu VM pool, claim, shell, detached job.

  .venv/bin/python scripts/fleet_vm_smoke.py --pool fps-bench-vm-smoke [--keep]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402

VM_IMAGE = "public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:main-38352d34"


async def main(args: argparse.Namespace) -> int:
    fleet.configure_auth()
    from cua_sandbox import Image, WarmPoolAutoscaling
    from cua_sandbox.pool import Pool

    t0 = time.monotonic()
    pool = await Pool.apply(
        Image.from_registry(args.image, os_type="linux", kind="vm"),
        name=args.pool, cpu=4, memory_mb=8192, services={"server": 8000},
        autoscaling=WarmPoolAutoscaling(min_pool_size=0, initial_pool_size=1, max_pool_size=2),
    )
    print(f"pool ok: {pool.name} ({time.monotonic() - t0:.0f}s)", flush=True)
    sb = await fleet.claim(pool, f"vmsmoke-{int(time.time())}", time_to_start=900)
    print(f"claimed: {sb.claim_name} sandbox={sb.name} ({time.monotonic() - t0:.0f}s)", flush=True)
    try:
        r = await sb.shell.run("uname -a; id; nproc; free -m | head -2; df -h / | tail -1; which git cargo python3 cua-driver; cua-driver --version; python3 -c 'import bench_ui' && echo bench_ui-ok; echo DISPLAY=$DISPLAY", timeout=25)
        print("rc", r.returncode, "\nstdout:", r.stdout, "\nstderr:", r.stderr[-800:])
        rc, log = await fleet.run_detached(sb, "for i in 1 2 3 4 5 6 7 8; do echo tick $i; sleep 5; done; echo DETACHED_OK", name="smoke", timeout=120)
        print("detached rc", rc, "log:", log[-300:])
    finally:
        if args.keep:
            Path("results/runs").mkdir(parents=True, exist_ok=True)
            Path("results/runs/vmsmoke.sandbox.json").write_text(json.dumps(sb.to_dict()))
            await sb.disconnect()
            print("kept claim; ref in results/runs/vmsmoke.sandbox.json")
        else:
            await fleet.release(sb)
            print("released", flush=True)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--image", default=VM_IMAGE)
    p.add_argument("--pool", default="fps-bench-vm-smoke")
    p.add_argument("--keep", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args())))
