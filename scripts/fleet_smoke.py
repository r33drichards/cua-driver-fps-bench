"""Smoke-test the Fleet path: gVisor pool admission, a claim, shell.run, release.

  .venv/bin/python scripts/fleet_smoke.py --image public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:docker-latest --pool fps-bench-smoke
  .venv/bin/python scripts/fleet_smoke.py --image <bench image> --pool fps-bench-smoke --bench   # also runs 1 episode
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    fleet.configure_auth()
    t0 = time.monotonic()
    pool = await fleet.ensure_pool(args.image, name=args.pool, initial_size=1, max_size=2)
    print(f"pool ok: {pool.name} ({time.monotonic() - t0:.0f}s)", flush=True)
    sb = await fleet.claim(pool, f"smoke-{int(time.time())}")
    print(f"claimed: {sb.claim_name} sandbox={sb.name} ({time.monotonic() - t0:.0f}s)", flush=True)
    try:
        r = await sb.shell.run("uname -a; id; cua-driver --version; ls /opt; python3 -c 'import bench_ui' && echo bench_ui-ok", timeout=25)
        print("stdout:", r.stdout)
        print("stderr:", r.stderr[-500:])
        if args.bench:
            from fps_bench.runner import FleetSession, run_benchmark

            print("waiting for prebuild...", flush=True)
            print(await fleet.wait_prebuild(sb))
            summary = await run_benchmark(FleetSession(sb), episodes=1, label="smoke")
            print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))
    finally:
        if args.keep:
            Path("results/runs").mkdir(parents=True, exist_ok=True)
            Path("results/runs/smoke.sandbox.json").write_text(json.dumps(sb.to_dict()))
            await sb.disconnect()
            print("kept claim; reference in results/runs/smoke.sandbox.json")
        else:
            await fleet.release(sb)
            print("released", flush=True)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--pool", default="fps-bench-smoke")
    p.add_argument("--bench", action="store_true")
    p.add_argument("--keep", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args())))
