"""Dump the celestial hierarchy for PYRO and NYX from the RSI starmap API."""

import sys

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://robertsspaceindustries.com/api/starmap"
H = {"Content-Type": "application/json"}


def dump(system):
    r = requests.post(f"{BASE}/star-systems/{system}", headers=H, timeout=30)
    data = r.json()
    rs = data.get("data", {}).get("resultset") or []
    if not rs:
        print(f"\n=== {system}: no data (status {r.status_code}) ===")
        return
    row = rs[0]
    objs = row.get("celestial_objects") or []
    print(f"\n=== {system}  ({len(objs)} objects) ===")
    # id -> name for parent resolution
    by_id = {str(o.get("id")): o for o in objs}
    for o in sorted(objs, key=lambda x: (str(x.get("type")), str(x.get("name")))):
        parent = by_id.get(str(o.get("parent_id")), {})
        sub = o.get("subtype")
        subname = sub.get("name") if isinstance(sub, dict) else sub
        print(f"  {str(o.get('code')):<26} {str(o.get('name')):<22} "
              f"type={o.get('type'):<13} sub={subname} "
              f"parent={parent.get('name') or o.get('parent_id')}")


for sysname in ("PYRO", "NYX"):
    dump(sysname)
