"""Autoresearch / hill-climb loop for cua-driver on Fleet.

Each worker repeatedly: asks Claude for a patch to cua-driver (given the current
best patch and the experiment history), claims a sandbox from the pool, applies
the patch, rebuilds cua-driver, runs the FPS benchmark, records the result,
releases the claim. A patch becomes the new best when its score beats the best.

  python -m fps_bench.autoresearch --image <ecr image> --workers 3 --iterations 12 --episodes 3
  python -m fps_bench.autoresearch --image <ecr image> --baseline-only   # one baseline run

State lives in results/: experiments.jsonl (every run), best.diff, best.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fps_bench import fleet
from fps_bench.runner import FleetSession, run_benchmark

RESULTS = Path(__file__).resolve().parents[1] / "results"
CUA_MAIN = Path(os.environ.get("FPS_BENCH_CUA_SRC", Path.home() / "cua"))  # for source excerpts
MODEL = os.environ.get("FPS_BENCH_MODEL", "claude-opus-5")

SYSTEM = """You are improving cua-driver, a Rust computer-use driver, so that its `press_key`
tool reliably delivers key presses to a pywebview (WebKitGTK) window on an X11 XFCE desktop
running inside a Linux container (gVisor). The benchmark is a first-person game that moves
one step per keydown; the score is the fraction of episodes where the goal is reached, and
`delivery_ratio` is keydowns observed by the page divided by press_key calls.

You will be shown: the pinned source excerpts (Linux input path), the current best patch,
and the history of experiments with their scores and build/bench logs. Propose ONE focused
change as a unified diff against the pinned source (paths relative to the cua repo root,
e.g. `libs/cua-driver/rust/crates/platform-linux/src/...`). The diff must be a complete
replacement for the current best patch (include everything you want kept from it). It must
apply cleanly with `git apply` and compile with `cargo build --release -p cua-driver`.

Reply with a short rationale, then exactly one fenced block:
```diff
...unified diff...
```
If you believe no further change is worthwhile, reply with the single word NO_CHANGE."""


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(CUA_MAIN), *args], text=True)


def source_excerpts(ref: str, max_bytes: int = 160_000) -> str:
    """Key Linux input-path files at ``ref`` from the local trycua/cua clone."""
    root = "libs/cua-driver/rust/crates/platform-linux/src"
    try:
        files = _git("ls-tree", "-r", "--name-only", ref, root).split()
    except subprocess.CalledProcessError:
        return "(no local cua checkout available for excerpts)"
    wanted = [f for f in files if re.search(r"key|input|xtest|x11|focus|deliver|window", f, re.I)]
    out, used = [], 0
    for f in wanted:
        body = _git("show", f"{ref}:{f}")
        if used + len(body) > max_bytes:
            continue
        used += len(body)
        out.append(f"### {f}\n```rust\n{body}\n```")
    listing = "\n".join(files)
    return f"### file list ({root})\n{listing}\n\n" + "\n\n".join(out)


def extract_diff(text: str) -> str | None:
    if "NO_CHANGE" in text and "```diff" not in text:
        return None
    m = re.search(r"```diff\n(.*?)```", text, re.S)
    return m.group(1) if m else None


async def propose(client: Any, *, ref: str, best_diff: str, history: list[dict[str, Any]]) -> tuple[str | None, str]:
    hist = "\n".join(
        json.dumps({k: v for k, v in h.items() if k in ("id", "score", "delivery_ratio", "ok", "note", "log_tail")})
        for h in history[-12:]
    ) or "(none yet)"
    user = (
        f"Pinned ref: {ref}\n\n## Source excerpts\n{source_excerpts(ref)}\n\n"
        f"## Current best patch (score {max([h['score'] for h in history], default=0.0):.2f})\n"
        f"```diff\n{best_diff or '(empty: baseline)'}\n```\n\n## Experiment history (newest last)\n{hist}\n"
    )
    async with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "xhigh"},
    ) as stream:
        msg = await stream.get_final_message()
    if msg.stop_reason == "refusal":
        return None, "refusal"
    text = "".join(b.text for b in msg.content if b.type == "text")
    return extract_diff(text), text


def record(row: dict[str, Any]) -> None:
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "experiments.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


def load_history() -> list[dict[str, Any]]:
    p = RESULTS / "experiments.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class Best:
    def __init__(self) -> None:
        self.diff = (RESULTS / "best.diff").read_text() if (RESULTS / "best.diff").exists() else ""
        meta = RESULTS / "best.json"
        self.score = json.loads(meta.read_text())["score"] if meta.exists() else -1.0
        self.lock = asyncio.Lock()

    async def consider(self, diff: str, score: float, exp_id: str) -> bool:
        async with self.lock:
            if score > self.score:
                self.diff, self.score = diff, score
                RESULTS.mkdir(exist_ok=True)
                (RESULTS / "best.diff").write_text(diff)
                (RESULTS / "best.json").write_text(json.dumps({"score": score, "experiment": exp_id, "time": time.time()}))
                return True
            return False


async def prepare_sandbox(sb: Any, *, runtime: str, ref: str) -> str:
    """Make sure cua-driver source + toolchain are present and built once on this sandbox."""
    if runtime == "gvisor":
        return await fleet.wait_prebuild(sb)  # image/Dockerfile prebuild at boot
    return await fleet.ensure_bootstrapped(sb, ref)  # VM path: install + clone + build in-guest


async def run_experiment(
    pool: Any, *, exp_id: str, diff: str, episodes: int, ref: str, note: str,
    runtime: str = "vm", sb: Any = None,
) -> dict[str, Any]:
    """Run one experiment. If ``sb`` is given it is reused (and not released here)."""
    row: dict[str, Any] = {"id": exp_id, "ref": ref, "note": note, "diff": diff, "ok": False, "score": -1.0, "time": time.time()}
    t0 = time.monotonic()
    own_sandbox = sb is None
    try:
        if own_sandbox:
            sb = await fleet.claim(pool, exp_id)
        row["claim"] = sb.to_dict()
        (RESULTS / "runs").mkdir(parents=True, exist_ok=True)
        (RESULTS / "runs" / f"{exp_id}.sandbox.json").write_text(json.dumps(sb.to_dict()))
        await prepare_sandbox(sb, runtime=runtime, ref=ref)
        ok, log = await fleet.apply_and_build(sb, diff, name=exp_id)
        row["log_tail"] = log[-1500:]
        if not ok:
            row["note"] = f"{note} | build failed"
            return row
        summary = await run_benchmark(FleetSession(sb), episodes=episodes, label=exp_id)
        (RESULTS / "runs" / f"{exp_id}.json").write_text(json.dumps(summary, indent=2))
        row.update(ok=True, score=summary["score"], delivery_ratio=summary["delivery_ratio"],
                   mean_progress=summary["mean_progress"], cua_driver_version=summary["cua_driver_version"])
        return row
    except Exception as e:
        row["note"] = f"{note} | error: {e!r}"[:500]
        return row
    finally:
        row["seconds"] = time.monotonic() - t0
        if own_sandbox and sb is not None:
            await fleet.release(sb)
        record(row)
        print(f"[{exp_id}] score={row['score']} ok={row['ok']} {row.get('note','')} ({row['seconds']:.0f}s)", flush=True)


async def worker(
    idx: int, pool: Any, client: Any, best: Best, *, iterations: int, episodes: int, ref: str, run_id: str, runtime: str
) -> None:
    # One claim per worker, reused across its experiments: on the VM path the
    # in-guest bootstrap (toolchain + first build) costs 10-15 min, so a fresh
    # claim per experiment would dominate the loop.
    sb = await fleet.claim(pool, f"w-{run_id}-{idx}")
    try:
        await prepare_sandbox(sb, runtime=runtime, ref=ref)
        for i in range(iterations):
            exp_id = f"exp-{run_id}-w{idx}-{i}"
            history = load_history()
            diff, text = await propose(client, ref=ref, best_diff=best.diff, history=history)
            (RESULTS / "runs").mkdir(parents=True, exist_ok=True)
            (RESULTS / "runs" / f"{exp_id}.proposal.md").write_text(text)
            if diff is None:
                print(f"[{exp_id}] proposer returned no diff; stopping worker {idx}", flush=True)
                return
            row = await run_experiment(
                pool, exp_id=exp_id, diff=diff, episodes=episodes, ref=ref, note="proposed", runtime=runtime, sb=sb
            )
            if row["ok"] and await best.consider(diff, row["score"], exp_id):
                print(f"[{exp_id}] NEW BEST score={row['score']:.2f}", flush=True)
    finally:
        await fleet.release(sb)


async def amain(args: argparse.Namespace) -> int:
    fleet.configure_auth()
    ref = args.ref or subprocess.check_output(
        ["bash", "-c", f"grep -m1 '^ARG CUA_REF=' {Path(__file__).resolve().parents[1] / 'image/Dockerfile'} | cut -d= -f2"],
        text=True,
    ).strip()
    if args.runtime == "gvisor":
        # gVisor container pools work since trycua/cloud#7268 (pod resources) and #7269
        # (per-service Services) rolled out on 2026-08-29: pods stay up, claims bind in
        # ~3 min, shell.run works, and the boot-time build takes ~60 s with image tag
        # cua-driver-bench-20260830-* (earlier tags never started it: the base image's
        # supervisord.conf lacks [include]). `vm` stays the default only because it
        # needs no custom image; use --runtime gvisor with the bench image for speed.
        pool = await fleet.ensure_pool(args.image, name=args.pool, initial_size=args.workers, max_size=max(args.workers, 2))
    else:
        pool = await fleet.ensure_vm_pool(args.image or fleet.VM_IMAGE, name=args.pool, initial_size=args.workers, max_size=max(args.workers, 2))
    print(f"[fleet] pool {pool.name} ({args.runtime}) ready for {args.image or fleet.VM_IMAGE}", flush=True)
    run_id = uuid.uuid4().hex[:6]
    best = Best()
    if args.baseline_only or best.score < 0:
        row = await run_experiment(
            pool, exp_id=f"baseline-{run_id}", diff="", episodes=args.episodes, ref=ref, note="baseline", runtime=args.runtime
        )
        if row["ok"]:
            await best.consider("", row["score"], row["id"])
        if args.baseline_only:
            return 0 if row["ok"] else 1
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    await asyncio.gather(*(
        worker(i, pool, client, best, iterations=args.iterations, episodes=args.episodes, ref=ref, run_id=run_id, runtime=args.runtime)
        for i in range(args.workers)
    ))
    print(f"done. best score={best.score:.2f}; see {RESULTS / 'best.diff'}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runtime", choices=["vm", "gvisor"], default="vm",
                   help="vm: KubeVirt VM pool + in-guest bootstrap (default, no custom image); gvisor: container-image pool with the bench image (boot-time build ~60 s; needs tag cua-driver-bench-20260830 or later)")
    p.add_argument("--image", default=os.environ.get("FPS_BENCH_FLEET_IMAGE"),
                   help="container image (gvisor) or containerDisk (vm; default fleet.VM_IMAGE)")
    p.add_argument("--pool", default=fleet.DEFAULT_POOL)
    p.add_argument("--workers", type=int, default=3, help="parallel claims / experiments")
    p.add_argument("--iterations", type=int, default=5, help="experiments per worker")
    p.add_argument("--episodes", type=int, default=3)
    p.add_argument("--ref", help="pinned cua ref (default: from image/Dockerfile)")
    p.add_argument("--baseline-only", action="store_true")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
