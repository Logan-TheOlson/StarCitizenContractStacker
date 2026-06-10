"""Travel-cost model.

Two layers:

1. Explicit pairwise distances (minutes) when we have them -- e.g. scraped from
   VerseGuide's route calculator. Stored as ``distances[a][b] = minutes``.
2. A zone/tier fallback computed from the location tree (lowest common
   ancestor) when no explicit distance exists. This keeps the optimizer fully
   functional before any scraping has happened.
"""

from __future__ import annotations

from .models import Location


# Approximate quantum-travel minutes per relationship tier. These are sane
# defaults; explicit scraped distances override them whenever available.
TIER_MINUTES = {
    "same": 0.0,
    "same_body": 2.0,        # two outposts on the same moon/planet surface
    "surface": 2.5,          # a body and a location sitting on/around it
    "same_planet": 5.0,      # within one planet's sphere (its moons, station)
    "interplanetary": 10.0,  # different planets in the same system
    "intersystem": 25.0,     # different star systems (jump point)
    "unknown": 8.0,          # locations we can't place in the tree
}


class CostModel:
    def __init__(
        self,
        locations: dict[str, Location],
        distances: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.locations = locations
        self.distances = distances or {}

    # -- public API --------------------------------------------------------

    def travel_minutes(self, a: str, b: str) -> float:
        if a == b:
            return 0.0
        explicit = self._explicit(a, b)
        if explicit is not None:
            return explicit
        return TIER_MINUTES[self.tier(a, b)]

    def tier(self, a: str, b: str) -> str:
        if a == b:
            return "same"
        la, lb = self.locations.get(a), self.locations.get(b)
        if la is None or lb is None:
            return "unknown"
        if la.system != lb.system:
            return "intersystem"

        chain_a = self._chain(a)
        chain_b = self._chain(b)

        # Ancestor relationship (one sits directly under the other).
        if b in chain_a or a in chain_b:
            ancestor = self.locations[b if b in chain_a else a]
            if ancestor.type in ("planet", "system"):
                return "surface"
            return "same_body"

        lca = self._lca(chain_a, chain_b)
        if lca is None:
            return "intersystem"
        lca_type = self.locations[lca].type
        if lca_type == "moon":
            return "same_body"
        if lca_type == "planet":
            return "same_planet"
        if lca_type == "system":
            return "interplanetary"
        return "same_planet"

    # -- internals ---------------------------------------------------------

    def _explicit(self, a: str, b: str) -> float | None:
        d = self.distances.get(a, {}).get(b)
        if d is None:
            d = self.distances.get(b, {}).get(a)
        return d

    def _chain(self, loc_id: str) -> list[str]:
        """``[self, parent, ..., system]``."""
        chain: list[str] = []
        cur: str | None = loc_id
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            loc = self.locations.get(cur)
            cur = loc.parent if loc else None
        return chain

    @staticmethod
    def _lca(chain_a: list[str], chain_b: list[str]) -> str | None:
        set_b = set(chain_b)
        for node in chain_a:
            if node in set_b:
                return node
        return None
