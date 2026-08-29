"""Delete a Fleet pool and its owned template.  .venv/bin/python scripts/fleet_delete_pool.py <pool>..."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402


async def main(names: list[str]) -> None:
    fleet.configure_auth()
    from cua_sandbox.pool import Pool, _FleetClient

    for name in names:
        pool = await Pool.get(name)
        await pool.delete()
        print("deleted pool:", name, flush=True)
        client = _FleetClient()
        try:
            for t in await client.list_templates(name):
                await client.delete_template(t)
                print("deleted template:", t.metadata.name, flush=True)
        except AttributeError:
            pass
        finally:
            await client.close()


asyncio.run(main(sys.argv[1:]))
