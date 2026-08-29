"""Raw Fleet gateway probe: token + arbitrary GET paths.  .venv/bin/python scripts/fleet_probe.py <path>..."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fps_bench import fleet  # noqa: E402

BASE = os.environ.get("CUA_FLEET_BASE_URL", "https://run.cua.ai")


def token() -> str:
    fleet.configure_auth()
    req = urllib.request.Request(
        "https://auth.cua.ai/realms/cyclops-cs/protocol/openid-connect/token",
        data=urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": os.environ["CUA_CLIENT_ID"],
                "client_secret": os.environ["CUA_CLIENT_SECRET"],
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.load(urllib.request.urlopen(req))["access_token"]


def get(tok: str, path: str, limit: int = 1500):
    r = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + tok})
    try:
        resp = urllib.request.urlopen(r, timeout=25)
        return resp.status, resp.read()[:limit]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:limit]
    except Exception as e:  # noqa: BLE001
        return "ERR", repr(e)[:300]


if __name__ == "__main__":
    tok = token()
    for p in sys.argv[1:]:
        print(p, get(tok, p))
