"""Regenerate the Pyro + Nyx locations in app/data/locations.json from the UEX
Corp API (public, structured), preserving the curated Stanton entries."""

import json
import re
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://api.uexcorp.space/2.0"
FILE = Path(__file__).resolve().parents[1] / "app" / "data" / "locations.json"


def get(path):
    return requests.get(f"{BASE}/{path}", timeout=30).json().get("data", [])


def slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def build_system(system: str, sid: int) -> list[dict]:
    out = [{"id": system, "name": system.title(), "type": "system",
            "system": system, "parent": None}]
    planet_map: dict[int, str] = {}
    moon_map: dict[int, str] = {}

    for p in get(f"planets?id_star_system={sid}"):
        pid = slug(p["name"])
        planet_map[p["id"]] = pid
        out.append({"id": pid, "name": p["name"], "type": "planet",
                    "system": system, "parent": system})

    for m in get(f"moons?id_star_system={sid}"):
        mid = slug(m["name"])
        moon_map[m["id"]] = mid
        parent = planet_map.get(m.get("id_planet"), system)
        out.append({"id": mid, "name": m["name"], "type": "moon",
                    "system": system, "parent": parent})

    def parent_of(row):
        mo, pl = row.get("id_moon") or 0, row.get("id_planet") or 0
        if mo and mo in moon_map:
            return moon_map[mo]
        if pl and pl in planet_map:
            return planet_map[pl]
        return system

    for kind, typ in (("space_stations", "station"), ("cities", "city"),
                      ("outposts", "outpost")):
        for row in get(f"{kind}?id_star_system={sid}"):
            name = row["name"]
            parent = parent_of(row)
            if name == "Levski":           # famously on Delamar
                parent = "DELAMAR" if "DELAMAR" in planet_map.values() else parent
            out.append({"id": slug(name), "name": name, "type": typ,
                        "system": system, "parent": parent})
    return out


def main():
    systems = {s["name"].upper(): s["id"] for s in get("star_systems")}
    raw = json.loads(FILE.read_text(encoding="utf-8"))
    kept = [l for l in raw["locations"] if l["system"] == "STANTON"]

    new = []
    for name in ("PYRO", "NYX"):
        new += build_system(name, systems[name])

    # de-dup ids defensively
    seen, merged = set(), []
    for l in kept + new:
        if l["id"] not in seen:
            seen.add(l["id"])
            merged.append(l)

    raw["locations"] = merged
    FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    n_pyro = sum(1 for l in new if l["system"] == "PYRO")
    n_nyx = sum(1 for l in new if l["system"] == "NYX")
    print(f"kept {len(kept)} Stanton, added {n_pyro} Pyro + {n_nyx} Nyx "
          f"-> {len(merged)} total")


if __name__ == "__main__":
    main()
