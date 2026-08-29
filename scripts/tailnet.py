"""Tailnet hygiene for pi-cua sandboxes.

When a sandbox named X is re-created, Tailscale keeps the dead node "X" and
registers the new VM as "X-1", but pi-cua keeps SSHing to "X" (dead). This
deletes offline nodes named X / X-N and renames the live one back to X.

  scripts/tailnet.py list [prefix]
  scripts/tailnet.py fix <name>

Uses the OAuth client pi-cua stores in Keychain (cua-sandbox-tailscale-oauth).
"""

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request

API = "https://api.tailscale.com/api/v2"


def keychain(account: str) -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-s", "cua-sandbox-tailscale-oauth", "-a", account, "-w"], text=True
    ).strip()


def token() -> str:
    data = urllib.parse.urlencode({
        "client_id": keychain("client-id"), "client_secret": keychain("client-secret"),
        "grant_type": "client_credentials",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{API}/oauth/token", data=data)) as r:
        return json.load(r)["access_token"]


def call(tok: str, method: str, path: str, body=None):
    req = urllib.request.Request(
        f"{API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def devices(tok: str):
    st, data = call(tok, "GET", "/tailnet/-/devices")
    if st != 200:
        raise SystemExit(f"list devices failed: {st} {data}")
    return data["devices"]


def short(dev) -> str:
    return dev["name"].split(".")[0]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list"); l.add_argument("prefix", nargs="?", default="")
    f = sub.add_parser("fix"); f.add_argument("name")
    a = p.parse_args()
    tok = token()
    devs = devices(tok)
    if a.cmd == "list":
        for d in devs:
            if short(d).startswith(a.prefix):
                print(f"{short(d):20} {d['addresses'][0]:16} online={d.get('online')} id={d['id']} last={d.get('lastSeen')}")
        return 0
    name = a.name
    family = [d for d in devs if short(d) == name or short(d).startswith(name + "-") and short(d)[len(name) + 1:].isdigit()]
    live = [d for d in family if d.get("online")]
    dead = [d for d in family if not d.get("online")]
    for d in dead:
        st, out = call(tok, "DELETE", f"/device/{d['id']}")
        print(f"delete {short(d)} ({d['id']}): {st} {out if st >= 300 else ''}")
    if len(live) == 1 and short(live[0]) != name:
        st, out = call(tok, "POST", f"/device/{live[0]['id']}/name", {"name": name})
        print(f"rename {short(live[0])} -> {name}: {st} {out if st >= 300 else ''}")
    elif len(live) > 1:
        print("multiple live nodes, not renaming:", [short(d) for d in live])
    elif not live:
        print("no live node for", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
