"""Core data models for the Star Citizen cargo route optimizer.

Everything here is plain dataclasses with no third-party dependencies so the
app and optimizer can run on a stock Python install (tkinter is stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- Static universe data -------------------------------------------------

@dataclass(frozen=True)
class Location:
    """A place you can fly to. Forms a tree via ``parent`` up to the system.

    ``id`` is a stable code (e.g. ``"STANTON.HURSTON.LORVILLE"``). ``type`` is
    one of: system, planet, moon, city, station, lagrange, outpost,
    distribution. ``system`` is the top-level system code (e.g. ``"STANTON"``).
    """

    id: str
    name: str
    type: str
    system: str
    parent: Optional[str] = None


@dataclass(frozen=True)
class Ship:
    name: str
    scu_capacity: int


# --- Mission / contract data ---------------------------------------------

@dataclass
class CargoItem:
    """One commodity to move: a kind, an amount, and where it's dropped.

    All cargo in a contract is picked up at the contract's pickup; each item may
    have its own ``dropoff``. ``boxes`` optionally records the SCU box breakdown
    (e.g. ``{32: 1, 16: 2}``); ``scu`` is derived from boxes if omitted.
    """

    commodity: str
    scu: int = 0
    dropoff: str = ""
    boxes: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.boxes and not self.scu:
            self.scu = sum(size * n for size, n in self.boxes.items())


@dataclass
class Leg:
    """One pickup -> dropoff move that may carry several commodities at once.

    All cargo on a leg travels together (same pickup, same dropoff), so for
    routing a leg behaves as a single task whose size is ``scu`` (the sum of its
    cargo). ``contract`` holds the owning contract's letter once locked in.
    """

    pickup: str                                   # Location id
    dropoff: str                                  # Location id
    cargo: list[CargoItem] = field(default_factory=list)
    contract: str = ""

    @property
    def scu(self) -> int:
        return sum(c.scu for c in self.cargo)

    @property
    def commodity_summary(self) -> str:
        return ", ".join(f"{c.commodity} - {c.scu} SCU" for c in self.cargo) or "Cargo"

    @classmethod
    def single(cls, pickup: str, dropoff: str, commodity: str, scu: int,
               contract: str = "") -> "Leg":
        """Convenience for tests / simple one-commodity legs."""
        return cls(pickup, dropoff, [CargoItem(commodity, scu)], contract)


@dataclass
class Contract:
    """A lettered set of cargo, all picked up at ``pickup``.

    Each cargo item may go to its own dropoff. The optimizer groups items by
    dropoff into internal Leg tasks at plan time.
    """

    letter: str
    pickup: str = ""                              # shared pickup for all cargo
    cargo: list[CargoItem] = field(default_factory=list)
    reward: int = 0                               # payout in aUEC


# --- Optimizer output -----------------------------------------------------

@dataclass
class Stop:
    """One visit to a location within a trip."""

    location: str
    dropoffs: list[Leg] = field(default_factory=list)
    pickups: list[Leg] = field(default_factory=list)
    onboard_after: int = 0          # SCU on board when leaving this stop
    travel_from_prev: float = 0.0   # minutes spent getting here


@dataclass
class Trip:
    """A self-contained run: load up, deliver, (optionally) return."""

    stops: list[Stop] = field(default_factory=list)
    capacity: int = 0
    total_minutes: float = 0.0
    peak_scu: int = 0


@dataclass
class RoutePlan:
    trips: list[Trip] = field(default_factory=list)
    feasible: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def total_stops(self) -> int:
        return sum(len(t.stops) for t in self.trips)

    @property
    def total_minutes(self) -> float:
        return sum(t.total_minutes for t in self.trips)
