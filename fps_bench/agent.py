"""Minimal cua-driver agent for the L-platform task.

Perception is privileged (the live game state is read through
``session.execute_javascript``); every *action* goes through ``cua-driver call``
executed inside the environment, so the score isolates cua-driver's input path.

The policy is deterministic and closed-loop: after each key press it re-reads
the state and re-plans, so dropped or duplicated keys are recovered from.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cua_bench.agents.base import AgentResult, BaseAgent, FailureMode

# Geometry mirrored from tasks/fps_lshape/gui/index.html
START = (1.5, 7.0)
GOAL = (15.0, -1.5)
TURN = 15
DRIVER_BIN = "cua-driver"
DRIVER_SESSION = "fps-bench"


class GameSession(Protocol):
    async def execute_javascript(self, pid: int | str, javascript: str) -> Any: ...
    async def run_command(self, command: str, *, check: bool = True) -> dict[str, Any]: ...


@dataclass
class EpisodeRecord:
    reached: bool = False
    progress: float = 0.0
    presses: int = 0
    keydowns: int = 0
    falls: int = 0
    seconds: float = 0.0
    failure: str = ""
    driver_errors: int = 0
    key_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def delivery_ratio(self) -> float:
        return self.keydowns / self.presses if self.presses else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["delivery_ratio"] = self.delivery_ratio
        return d


def plan_key(state: dict[str, Any]) -> str | None:
    """Return the next key for the current state, or None when the goal is reached.

    Route: face -Z (heading 180) and walk to the corner row, then face +X
    (heading 90) and walk to the goal. Headings are multiples of TURN.
    """
    if state.get("reached"):
        return None
    x, z, heading = float(state["x"]), float(state["z"]), int(state["heading"]) % 360
    on_long_leg_row = z <= -1.0  # inside the long leg's z band [-3, 0]

    def turn_toward(target: int) -> str:
        delta = (target - heading + 540) % 360 - 180
        return "ArrowRight" if delta > 0 else "ArrowLeft"

    if not on_long_leg_row:
        return "w" if heading == 180 else turn_toward(180)
    if heading != 90:
        return turn_toward(90)
    if x < GOAL[0] - 0.5:
        return "w"
    return None  # within goal radius but not flagged yet; caller re-reads


def driver_call(tool: str, args: dict[str, Any], session_id: str = DRIVER_SESSION) -> str:
    payload = dict(args)
    if session_id:
        payload["session"] = session_id
    return f"{DRIVER_BIN} call {tool} {shlex.quote(json.dumps(payload))}"


class CuaDriverAgent(BaseAgent):
    """Drives the L-platform game with ``cua-driver call press_key`` inside the env."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.max_presses = int(kwargs.get("max_presses", 80))
        self.time_budget = float(kwargs.get("time_budget", 240.0))
        self.settle_ms = int(kwargs.get("settle_ms", 150))
        self.window_pid: int | None = kwargs.get("window_pid")
        self.window_title = kwargs.get("window_title", "L-Platform")
        self.last_record: EpisodeRecord | None = None

    @staticmethod
    def name() -> str:
        return "cua-driver-fps"

    async def _state(self, session: GameSession, pid: int) -> dict[str, Any]:
        raw = await session.execute_javascript(pid, "JSON.stringify(window.__state)")
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def _resolve_window(self, session: GameSession) -> dict[str, Any]:
        """Find the game window via cua-driver so presses can be targeted at it."""
        res = await session.run_command(driver_call("list_windows", {}), check=False)
        try:
            data = json.loads(res.get("stdout") or "{}")
        except json.JSONDecodeError:
            return {}
        windows = data.get("windows") or data.get("structuredContent", {}).get("windows") or []
        for w in windows:
            if self.window_title.lower() in str(w.get("title", "")).lower():
                return {"pid": w.get("pid"), "window_id": w.get("window_id") or w.get("id")}
        return {}

    async def _press(self, session: GameSession, key: str, target: dict[str, Any]) -> dict[str, Any]:
        args: dict[str, Any] = {"key": key}
        if target.get("pid") is not None:
            args["pid"] = target["pid"]
        if target.get("window_id") is not None:
            args["window_id"] = target["window_id"]
        res = await session.run_command(driver_call("press_key", args), check=False)
        return {
            "key": key,
            "rc": res.get("return_code", res.get("returncode")),
            "out": (res.get("stdout") or "")[-300:],
            "err": (res.get("stderr") or "")[-300:],
        }

    async def run_episode(self, session: GameSession, pid: int) -> EpisodeRecord:
        rec = EpisodeRecord()
        t0 = time.monotonic()
        await session.run_command(driver_call("start_session", {}), check=False)
        target = await self._resolve_window(session)
        try:
            state = await self._state(session, pid)
            while rec.presses < self.max_presses and time.monotonic() - t0 < self.time_budget:
                key = plan_key(state)
                if key is None:
                    if state.get("reached"):
                        break
                    key = "w"
                before = int(state.get("keydowns", 0))
                info = await self._press(session, key, target)
                rec.presses += 1
                if info["rc"] not in (0, None):
                    rec.driver_errors += 1
                await session.run_command(f"sleep {self.settle_ms / 1000:.3f}", check=False)
                state = await self._state(session, pid)
                info["delivered"] = int(state.get("keydowns", 0)) - before
                rec.key_log.append(info)
            rec.reached = bool(state.get("reached"))
            rec.keydowns = int(state.get("keydowns", 0))
            rec.falls = int(state.get("falls", 0))
            prog = await session.execute_javascript(pid, "window.__progress()")
            rec.progress = float(prog or 0.0)
            if not rec.reached:
                rec.failure = "max_presses" if rec.presses >= self.max_presses else "timeout"
        except Exception as e:  # keep the record, surface the failure
            rec.failure = f"error: {e!r}"[:300]
        finally:
            rec.seconds = time.monotonic() - t0
            await session.run_command(driver_call("end_session", {}), check=False)
        self.last_record = rec
        return rec

    async def perform_task(
        self, task_description: str, session: Any, logging_dir: Path | None = None
    ) -> AgentResult:
        pid = self.window_pid
        if pid is None:
            # cb's runner does not hand us the window pid; the task module keeps it.
            import importlib

            pid = getattr(importlib.import_module("main"), "pid", None)
        if pid is None:
            raise RuntimeError("CuaDriverAgent needs the game window pid (window_pid kwarg)")
        rec = await self.run_episode(session, int(pid))
        if logging_dir:
            Path(logging_dir).mkdir(parents=True, exist_ok=True)
            (Path(logging_dir) / "episode.json").write_text(json.dumps(rec.to_dict(), indent=2))
        mode = FailureMode.NONE if rec.reached else (
            FailureMode.MAX_STEPS_EXCEEDED if rec.failure == "max_presses" else FailureMode.UNKNOWN
        )
        return AgentResult(total_input_tokens=0, total_output_tokens=0, failure_mode=mode)


def summarize(records: list[EpisodeRecord]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"episodes": 0, "score": 0.0}
    return {
        "episodes": n,
        "score": sum(r.reached for r in records) / n,
        "mean_progress": sum(r.progress for r in records) / n,
        "delivery_ratio": sum(r.delivery_ratio for r in records) / n,
        "mean_presses": sum(r.presses for r in records) / n,
        "mean_seconds": sum(r.seconds for r in records) / n,
        "falls": sum(r.falls for r in records),
        "driver_errors": sum(r.driver_errors for r in records),
        "failures": [r.failure for r in records if r.failure],
    }
