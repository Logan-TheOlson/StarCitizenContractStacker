"""Loading of static location data (the universe tree + optional distances)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import Location

# In a PyInstaller build the data is bundled at <_MEIPASS>/app/data.
if getattr(sys, "_MEIPASS", None):
    DATA_DIR = Path(sys._MEIPASS) / "app" / "data"
else:
    DATA_DIR = Path(__file__).resolve().parent / "data"
LOCATIONS_FILE = DATA_DIR / "locations.json"


def load_locations(
    path: Path | None = None,
) -> tuple[dict[str, Location], dict[str, dict[str, float]]]:
    """Return ``(locations_by_id, distances)`` from the JSON cache."""

    path = path or LOCATIONS_FILE
    raw = json.loads(path.read_text(encoding="utf-8"))
    locations = {
        loc["id"]: Location(
            id=loc["id"],
            name=loc["name"],
            type=loc["type"],
            system=loc["system"],
            parent=loc.get("parent"),
        )
        for loc in raw["locations"]
    }
    distances = {
        a: {b: float(m) for b, m in row.items()}
        for a, row in raw.get("distances", {}).items()
    }
    return locations, distances


def selectable_locations(locations: dict[str, Location]) -> list[Location]:
    """Locations a player can actually pick up/drop at (excludes systems)."""

    return sorted(
        (l for l in locations.values() if l.type != "system"),
        key=lambda l: (l.system, l.name),
    )
