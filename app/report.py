"""Human-readable rendering of a RoutePlan (shared by GUI and any CLI use)."""

from __future__ import annotations

from .models import Leg, Location, RoutePlan


def _name(locations: dict[str, Location], loc_id: str) -> str:
    loc = locations.get(loc_id)
    return loc.name if loc else loc_id


def _legs_text(legs: list[Leg]) -> str:
    return "; ".join(leg.commodity_summary for leg in legs)


def format_plan(
    plan: RoutePlan, locations: dict[str, Location], capacity: int,
    total_reward: int = 0,
) -> str:
    lines: list[str] = []

    if not plan.feasible:
        lines.append("PLAN NOT FEASIBLE")
        lines.append("")
        lines.extend(f"  - {n}" for n in plan.notes)
        return "\n".join(lines)

    for note in plan.notes:
        lines.append(f"note: {note}")
    if plan.notes:
        lines.append("")

    multi = len(plan.trips) > 1
    for i, trip in enumerate(plan.trips, 1):
        header = f"TRIP {i}" if multi else "ROUTE"
        util = (trip.peak_scu / capacity * 100) if capacity else 0
        lines.append(
            f"{header}  -  {capacity} SCU hold  -  "
            f"peak load {trip.peak_scu} SCU, {util:.0f}% used"
        )
        for j, stop in enumerate(trip.stops, 1):
            lines.append(f"  {j}. {_name(locations, stop.location)}")
            if stop.dropoffs:
                lines.append(f"       DROP : {_legs_text(stop.dropoffs)}")
            if stop.pickups:
                lines.append(f"       LOAD : {_legs_text(stop.pickups)}")
            lines.append(f"       onboard -> {stop.onboard_after} SCU")
        lines.append("")

    lines.append(
        f"SUMMARY: {len(plan.trips)} trip(s), {plan.total_stops} stop(s)"
    )

    if total_reward:
        lines.append(f"PAYOUT : {total_reward:,} aUEC")
    return "\n".join(lines)
