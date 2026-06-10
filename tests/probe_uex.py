"""Probe UEX Corp API for Pyro/Nyx star systems and their locations."""

import sys

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://api.uexcorp.space/2.0"


def get(path):
    r = requests.get(f"{BASE}/{path}", timeout=30)
    return r.status_code, r.json() if r.ok else r.text


sc, systems = get("star_systems")
print("star_systems ->", sc)
ids = {}
if isinstance(systems, dict):
    for s in systems.get("data", []):
        print("  system:", s.get("id"), s.get("name"), "available:", s.get("is_available"))
        ids[str(s.get("name")).upper()] = s.get("id")

for name in ("PYRO", "NYX"):
    sid = ids.get(name)
    if not sid:
        print(f"\n{name}: no id"); continue
    print(f"\n===== {name} (id {sid}) =====")
    for ep in ("planets", "moons", "space_stations", "cities", "outposts"):
        sc, data = get(f"{ep}?id_star_system={sid}")
        rows = data.get("data", []) if isinstance(data, dict) else []
        print(f"-- {ep}: {sc}, {len(rows)}")
        for row in rows[:40]:
            print(f"     {row.get('name')}  (id {row.get('id')}, "
                  f"planet {row.get('id_planet')}, moon {row.get('id_moon')})")
