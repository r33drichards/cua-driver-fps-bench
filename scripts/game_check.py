"""Headless smoke test of the L-platform game (no desktop needed).

Run: .venv/bin/python scripts/game_check.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tasks" / "fps_lshape"))
from main import ORACLE_KEYS, game_html  # noqa: E402

from playwright.async_api import async_playwright  # noqa: E402


async def main() -> int:
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=swiftshader"])
        pg = await b.new_page(viewport={"width": 800, "height": 600})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.set_content(game_html())
        await pg.wait_for_timeout(500)
        print("page errors:", errs)
        for k in ["w", "w", "ArrowLeft"]:
            await pg.keyboard.press(k)
        after = await pg.evaluate("[__state.x,__state.z,__state.heading,__state.keydowns]")
        print("after real key events [x,z,heading,keydowns]:", after)
        assert after[3] == 3 and abs(after[1] - 5.0) < 1e-6 and after[2] == 165, after

        await pg.evaluate("window.__reset()")
        for k in ORACLE_KEYS:
            await pg.evaluate(f"window.__press({k!r})")
        oracle = await pg.evaluate("[__state.reached,__state.falls,__progress(),__state.x,__state.z]")
        print("oracle [reached,falls,progress,x,z]:", oracle)
        assert oracle[0] is True and oracle[1] == 0, oracle

        await pg.evaluate("window.__reset()")
        for _ in range(12):
            await pg.keyboard.press("w")
        fall = await pg.evaluate("[__state.fell,__state.falls,__state.x,__state.z]")
        print("fall test [fell,falls,x,z]:", fall)
        assert fall[0] is True and fall[1] == 1, fall
        out = Path(__file__).resolve().parents[1] / "results" / "game_check.png"
        out.parent.mkdir(exist_ok=True)
        await pg.screenshot(path=str(out))
        await b.close()
        print("OK, screenshot:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
