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

# Oracle route from the start pose (facing -Z toward the corner): walk 8 units
# down the short leg, turn the camera 90° left (mouse look) to face +X, walk 14 units.
# Each ("key", k) is one programmatic press (1 unit); ("look", deg) turns the camera.
ORACLE_STEPS: list[tuple[str, str | float]] = (
    [("key", "w")] * 8 + [("look", 90.0)] + [("key", "w")] * 14
)


def game_html() -> str:
    """Return the game page with three.js + PointerLockControls inlined (no internet on the desktop)."""
    html = (GUI / "index.html").read_text()
    html = html.replace("__THREE__", (GUI / "three.min.js").read_text(), 1)
    return html.replace("__POINTERLOCK__", (GUI / "PointerLockControls.js").read_text(), 1)


@cb.tasks_config(split="train")
def load():
    return [
        cb.Task(
            description=(
                "You are in a first-person 3D game on an L-shaped floating platform. "
                "Walk to the glowing green goal marker at the far end of the platform "
                "without falling off. Mouse moves the camera (look), W/A/S/D move, "
                "Space jumps."
            ),
            metadata={"os_type": "linux", "oracle_steps": ORACLE_STEPS},
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
    """Oracle: drive the game through its programmatic hooks (no OS input path)."""
    for kind, arg in ORACLE_STEPS:
        js = f"window.__press({arg!r})" if kind == "key" else f"window.__look({float(arg)})"
        await session.execute_javascript(pid, js)
