"""Print a Fleet pool's status and its claims.  .venv/bin/python scripts/fleet_status.py <pool>"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402


async def main(name: str) -> None:
    fleet.configure_auth()
    from cua_sandbox.pool import Pool, _FleetClient

    pool = await Pool.get(name)
    r = pool.resource
    print("pool:", r.metadata.name, "spec.replicas=", getattr(r.spec, "replicas", None))
    print("status:", r.status)
    client = _FleetClient()
    try:
        for c in await client.list_claims(r.metadata.namespace):
            print("claim:", c.metadata.name, "status:", c.status)
        try:
            for s in await client.list_sandboxes(r.metadata.namespace):
                print("sandbox:", s.metadata.name, "status:", s.status)
        except AttributeError:
            pass
    finally:
        await client.close()


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else fleet.DEFAULT_POOL))
