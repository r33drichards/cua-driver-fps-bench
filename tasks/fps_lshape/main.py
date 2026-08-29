"""cua-bench task: first-person movement on an L-shaped platform.

The player spawns at the end of the short leg facing the corner and must walk
to the glowing goal at the end of the long leg. Falling off resets to the start.

Score (evaluate) returns ``[reached, progress]``:
  * ``reached``  – 1.0 if the goal was reached, else 0.0 (the benchmark metric)
  * ``progress`` – 0..1 fraction of the route covered (diagnostic only)
"""

import os
from pathlib import Path

import cua_bench as cb

GUI = Path(__file__).parent / "gui"

# Default image is the one cua-sandbox uses for local docker on trycua/cua main,
# so local runs and Fleet runs exercise the same desktop. Override with FPS_BENCH_IMAGE.
DEFAULT_IMAGE = os.environ.get(
    "FPS_BENCH_IMAGE", "public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:docker-latest"
)

WINDOW_TITLE = "L-Platform"
WINDOW_W, WINDOW_H = 800, 600

# Optimal key sequence from the start pose (heading 180 = facing the corner):
# walk 8 steps down the short leg, turn left 90° (6 x 15°) to face +X, walk 14 steps.
ORACLE_KEYS = ["w"] * 8 + ["ArrowLeft"] * 6 + ["w"] * 14


def game_html() -> str:
    """Return the game page with three.js inlined (the desktop has no internet)."""
    three = (GUI / "three.min.js").read_text()
    return (GUI / "index.html").read_text().replace("__THREE__", three, 1)


@cb.tasks_config(split="train")
def load():
    return [
        cb.Task(
            description=(
                "You are in a first-person 3D game on an L-shaped floating platform. "
                "Walk to the glowing green goal marker at the far end of the platform "
                "without falling off. W/S move forward/back one step, A/D strafe, "
                "Left/Right arrows turn 15 degrees."
            ),
            metadata={"os_type": "linux", "oracle_keys": ORACLE_KEYS},
            computer={
                "provider": "native",
                "setup_config": {
                    "os_type": "linux",
                    "image": DEFAULT_IMAGE,
                    "width": 1024,
                    "height": 768,
                    "background": "#101418",
                },
            },
        )
    ]


pid = -1


@cb.setup_task(split="train")
async def start(task_cfg: cb.Task, session: cb.DesktopSession | cb.MobileSession):
    global pid
    pid = await session.launch_window(
        html=game_html(), title=WINDOW_TITLE, width=WINDOW_W, height=WINDOW_H
    )


@cb.evaluate_task(split="train")
async def evaluate(task_cfg: cb.Task, session: cb.DesktopSession | cb.MobileSession) -> list[float]:
    try:
        reached = await session.execute_javascript(pid, "window.__state.reached === true")
        prog = await session.execute_javascript(pid, "window.__progress()")
        return [1.0 if reached else 0.0, float(prog or 0.0)]
    except Exception:
        return [0.0, 0.0]


@cb.solve_task(split="train")
async def solve(task_cfg: cb.Task, session: cb.DesktopSession | cb.MobileSession):
    """Oracle: drive the game through its programmatic hook (no OS input path)."""
    for key in ORACLE_KEYS:
        await session.execute_javascript(pid, f"window.__press({key!r})")
