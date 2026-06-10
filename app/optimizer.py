"""Route optimizer.

Models cargo hauling as a single-vehicle Pickup-and-Delivery Problem with a
capacity constraint, then optimizes lexicographically per the user's ranking:

    1. fewest stops          (primary)
    2. least travel time     (secondary)
    3. best capacity use     (tiebreak: prefer lower peak load -> more headroom)

Strategy
--------
* The number of stops for a single trip that visits each needed location once is
  fixed (= number of distinct locations). So we first try to serve *all* legs in
  one trip via exact branch-and-bound; if any precedence/capacity-feasible order
  exists, that is the fewest-stops solution and we pick the cheapest by time,
  breaking ties on the lowest peak load.
* If one trip can't fit the cargo, we bin-pack legs into the *minimum* number of
  capacity-feasible trips (fewest trips => fewest stops) and optimize each trip's
  ordering independently.
* Above a size threshold the exact search is skipped for a greedy
  nearest-feasible heuristic so the UI never hangs. The greedy result is then
  validated; if it can't serve everything in one capacity-feasible pass we fall
  back to the multi-trip bin-packing path rather than emit an invalid route.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cost import CostModel
from .models import Leg, RoutePlan, Ship, Stop, Trip

# Distinct-location count above which we use the heuristic instead of exact B&B.
EXACT_LIMIT = 11


@dataclass
class _Order:
    """A candidate single-trip visiting order and its evaluated metrics."""

    sequence: list[str]
    minutes: float
    peak: int


def optimize(
    legs: list[Leg],
    ship: Ship,
    cost: CostModel,
    start: str | None = None,
) -> RoutePlan:
    """Plan the best route to fulfil ``legs`` with ``ship``.

    ``start`` is the player's current location, if known; it is charged as the
    travel leg into the first stop.
    """

    legs = [leg for leg in legs if leg.scu > 0]
    plan = RoutePlan()
    if not legs:
        plan.notes.append("No cargo legs to plan.")
        return plan

    oversize = [leg for leg in legs if leg.scu > ship.scu_capacity]
    if oversize:
        plan.feasible = False
        for leg in oversize:
            plan.notes.append(
                f"Leg {leg.commodity_summary} at {leg.scu} SCU exceeds capacity "
                f"of {ship.scu_capacity} SCU and can never be carried whole."
            )
        return plan

    # No explicit start -> begin where you load the most cargo (the busiest
    # pickup hub). This avoids opening on an empty pass-through stop and matches
    # how you'd actually run it: fill up at the biggest pickup first.
    if start is None:
        load_by_pickup: dict[str, int] = {}
        legs_by_pickup: dict[str, int] = {}
        for leg in legs:
            load_by_pickup[leg.pickup] = load_by_pickup.get(leg.pickup, 0) + leg.scu
            legs_by_pickup[leg.pickup] = legs_by_pickup.get(leg.pickup, 0) + 1
        start = max(legs_by_pickup,
                    key=lambda p: (legs_by_pickup[p], load_by_pickup[p]))

    # 1) Try to serve everything in a single trip (fewest possible stops).
    single = _best_order(legs, ship.scu_capacity, cost, start)
    if single is not None:
        plan.trips.append(_build_trip(single, legs, ship.scu_capacity, cost, start))
        return plan

    # 2) No order visits every location once (e.g. a hub is both a pickup and a
    #    later dropoff). Bin-pack into the fewest capacity-feasible trips. This
    #    often still resolves to ONE trip that revisits a stop -- which is fine,
    #    because dropping off frees space. Only flag a split when peak load
    #    genuinely forces more than one run.
    for bin_legs in _pack_trips(legs, ship.scu_capacity):
        order = _best_order(bin_legs, ship.scu_capacity, cost, start)
        if order is None:  # safety: a single-bin sum<=cap order always exists
            order = _greedy_order(bin_legs, ship.scu_capacity, cost, start)
        plan.trips.append(_build_trip(order, bin_legs, ship.scu_capacity, cost, start))

    if len(plan.trips) > 1:
        plan.notes.append(
            "Peak load exceeds ship capacity for a single trip; split into "
            "multiple runs (returning to reload)."
        )
    # Last-resort safety net: if any planned run still peaks over capacity
    # (only possible from a greedy fallback on a hard instance), say so rather
    # than presenting an un-haulable route as fine.
    if any(t.peak_scu > ship.scu_capacity for t in plan.trips):
        plan.notes.append(
            "Warning: a planned trip's peak load exceeds capacity; this route "
            "may not be haulable exactly as ordered."
        )
    return plan


# --- single-trip ordering -------------------------------------------------

def _best_order(
    legs: list[Leg], cap: int, cost: CostModel, start: str | None
) -> _Order | None:
    locations = _distinct_locations(legs)
    if len(locations) <= EXACT_LIMIT:
        return _branch_and_bound(locations, cap, cost, start, legs)
    # Above the exact limit we use the greedy heuristic. It does NOT guarantee a
    # single-pass solution exists (e.g. a hub whose total pickup exceeds
    # capacity), so only accept it as a one-trip plan when it genuinely delivers
    # every leg within capacity. Otherwise return None so the caller bin-packs
    # the legs into multiple capacity-feasible trips.
    order = _greedy_order(legs, cap, cost, start)
    return order if _route_feasible(order.sequence, legs, cap) else None


def _route_feasible(sequence: list[str], legs: list[Leg], cap: int) -> bool:
    """True if visiting ``sequence`` delivers every leg in precedence order
    without ever exceeding ``cap`` -- same load/drop semantics as ``_build_trip``."""
    by_pickup, by_dropoff = _index_legs(legs)
    onboard = 0
    loaded: set[int] = set()
    dropped: set[int] = set()
    for loc in sequence:
        drops = [l for l in by_dropoff.get(loc, [])
                 if id(l) in loaded and id(l) not in dropped]
        picks = [l for l in by_pickup.get(loc, []) if id(l) not in loaded]
        loaded |= {id(l) for l in picks}
        dropped |= {id(l) for l in drops}
        onboard -= sum(l.scu for l in drops)
        onboard += sum(l.scu for l in picks)
        if onboard > cap:
            return False
    return len(dropped) == len(legs)


def _branch_and_bound(
    locations: list[str],
    cap: int,
    cost: CostModel,
    start: str | None,
    legs: list[Leg],
) -> _Order | None:
    by_pickup, by_dropoff = _index_legs(legs)

    best: _Order | None = None
    n = len(locations)
    # Search budget. The first complete order is reached in ~n nodes, so this
    # never blocks finding *a* solution; it only caps the extra exploration of
    # equal-time orders done to minimise peak load (the capacity tiebreak),
    # which on uniform-cost inputs could otherwise approach n! and hang the UI.
    budget = 100_000
    nodes = 0

    def recurse(seq, visited, loaded_legs: set[int], onboard, peak, minutes, last):
        nonlocal best, nodes
        nodes += 1
        if nodes > budget:
            return
        # Prune on strictly-greater time so equal-time orders still reach a leaf
        # and can win on the lower-peak (capacity) tiebreak below.
        if best is not None and minutes > best.minutes:
            return
        if len(seq) == n:
            if best is None or (minutes, peak) < (best.minutes, best.peak):
                best = _Order(list(seq), minutes, peak)
            return
        for loc in locations:
            if loc in visited:
                continue
            drops = by_dropoff.get(loc, [])
            if not all(id(l) in loaded_legs for l in drops):
                continue
            new_onboard = onboard - sum(l.scu for l in drops)
            picks = by_pickup.get(loc, [])
            new_onboard += sum(l.scu for l in picks)
            if new_onboard > cap:
                continue
            step = cost.travel_minutes(last, loc) if last is not None else 0.0
            new_loaded = loaded_legs | {id(l) for l in picks}
            seq.append(loc)
            visited.add(loc)
            recurse(seq, visited, new_loaded, new_onboard,
                    max(peak, new_onboard), minutes + step, loc)
            seq.pop()
            visited.discard(loc)

    recurse([], set(), set(), 0, 0, 0.0, start)
    return best


def _greedy_order(
    legs: list[Leg], cap: int, cost: CostModel, start: str | None
) -> _Order:
    locations = _distinct_locations(legs)
    by_pickup, by_dropoff = _index_legs(legs)

    seq: list[str] = []
    visited: set[str] = set()
    loaded_legs: set[int] = set()
    dropped_legs: set[int] = set()
    onboard = peak = 0
    minutes = 0.0
    last = start
    remaining = set(locations)

    def pending_drops(loc: str) -> list[Leg]:
        return [l for l in by_dropoff.get(loc, []) if id(l) not in dropped_legs]

    def deliverable_drops(loc: str) -> list[Leg]:
        return [l for l in pending_drops(loc) if id(l) in loaded_legs]

    def pending_picks(loc: str) -> list[Leg]:
        return [l for l in by_pickup.get(loc, []) if id(l) not in loaded_legs]

    max_visits = len(legs) * 2 + len(locations) + 1
    visits = 0
    while remaining and visits < max_visits:
        visits += 1
        candidates = [
            loc for loc in remaining
            if onboard - sum(l.scu for l in deliverable_drops(loc))
                       + sum(l.scu for l in pending_picks(loc)) <= cap
        ]
        if not candidates:
            candidates = list(remaining)
        # Prefer a stop we can FINISH in this visit: one where every drop still
        # owed here is already onboard, so we deliver it and retire the location
        # for good. Visiting a location that is also the dropoff of a leg we
        # haven't loaded yet (e.g. an outbound-pickup hub that is also a later
        # inbound dropoff) does only a partial pickup and forces a wasteful
        # return trip. Defer those until they're completable; only fall back to
        # an unfinishable visit when nothing is finishable (a genuine revisit).
        finishable = [
            c for c in candidates
            if all(id(l) in loaded_legs for l in pending_drops(c))
        ]
        if finishable:
            candidates = finishable
        # Avoid a no-op re-pick of the stop we just processed -- but NOT on the
        # first step, where ``last`` is the start hub we actually want to open
        # on (travel 0).
        if seq and len(candidates) > 1 and last in candidates:
            candidates = [c for c in candidates if c != last]
        loc = min(
            candidates,
            key=lambda c: (
                cost.travel_minutes(last, c) if last is not None else 0.0,
                c in visited,  # prefer an unvisited stop over revisiting one
            ),
        )
        minutes += cost.travel_minutes(last, loc) if last is not None else 0.0
        drops = deliverable_drops(loc)
        picks = pending_picks(loc)
        onboard -= sum(l.scu for l in drops)
        onboard += sum(l.scu for l in picks)
        loaded_legs |= {id(l) for l in picks}
        dropped_legs |= {id(l) for l in drops}
        peak = max(peak, onboard)
        seq.append(loc)
        visited.add(loc)
        # A location may need a return visit later (drop off cargo picked up
        # here on this very stop), so only retire it once nothing more is owed.
        if not pending_drops(loc):
            remaining.discard(loc)
        last = loc

    return _Order(seq, minutes, peak)


# --- multi-trip packing ---------------------------------------------------

def _pack_trips(legs: list[Leg], cap: int) -> list[list[Leg]]:
    """First-fit-decreasing bin packing that keeps *chained* legs together.

    Only a chain dependency forces two legs into the same trip: when one leg's
    dropoff is another leg's pickup, visiting that hub drops cargo and frees
    room for the pickup, so their peak load can stay below the sum and they may
    fit a single run that revisits the hub. Legs that merely share a pickup (or
    merely share a dropoff) are carried at the same time -- their peak *is* the
    sum -- so they must stay splittable across trips when over capacity. We
    group chained legs first, then bin-pack those groups by total SCU.
    """
    # Build connected components over the chain relation only.
    components: list[set[int]] = []
    for i, leg in enumerate(legs):
        touching = {j for j, comp in enumerate(components)
                    if any(legs[k].dropoff == leg.pickup or
                           legs[k].pickup == leg.dropoff
                           for k in comp)}
        if touching:
            merged: set[int] = {i}
            for j in touching:
                merged |= components[j]
            components = [c for j, c in enumerate(components) if j not in touching]
            components.append(merged)
        else:
            components.append({i})

    groups = [sorted(comp) for comp in components]

    # Now bin-pack groups (not individual legs) by total SCU.
    bins: list[list[Leg]] = []
    sums: list[int] = []
    for group in sorted(groups, key=lambda g: sum(legs[i].scu for i in g), reverse=True):
        group_scu = sum(legs[i].scu for i in group)
        placed = False
        for b, used in enumerate(sums):
            if used + group_scu <= cap:
                bins[b].extend(legs[i] for i in group)
                sums[b] += group_scu
                placed = True
                break
        if not placed:
            bins.append([legs[i] for i in group])
            sums.append(group_scu)
    return bins

# --- shared helpers -------------------------------------------------------

def _distinct_locations(legs: list[Leg]) -> list[str]:
    seen: dict[str, None] = {}
    for leg in legs:
        seen.setdefault(leg.pickup, None)
        seen.setdefault(leg.dropoff, None)
    return list(seen.keys())


def _index_legs(
    legs: list[Leg],
) -> tuple[dict[str, list[Leg]], dict[str, list[Leg]]]:
    """Bucket legs by pickup and by dropoff location."""
    by_pickup: dict[str, list[Leg]] = {}
    by_dropoff: dict[str, list[Leg]] = {}
    for leg in legs:
        by_pickup.setdefault(leg.pickup, []).append(leg)
        by_dropoff.setdefault(leg.dropoff, []).append(leg)
    return by_pickup, by_dropoff


def _build_trip(
    order: _Order, legs: list[Leg], cap: int, cost: CostModel, start: str | None
) -> Trip:
    by_pickup, by_dropoff = _index_legs(legs)

    trip = Trip(capacity=cap)
    onboard = 0
    last = start
    loaded_legs: set[int] = set()
    dropped_legs: set[int] = set()
    for loc in order.sequence:
        drops = [l for l in by_dropoff.get(loc, [])
                 if id(l) in loaded_legs and id(l) not in dropped_legs]
        picks = [l for l in by_pickup.get(loc, []) if id(l) not in loaded_legs]
        loaded_legs |= {id(l) for l in picks}
        dropped_legs |= {id(l) for l in drops}
        onboard -= sum(l.scu for l in drops)   # dropoffs first
        onboard += sum(l.scu for l in picks)
        trip.peak_scu = max(trip.peak_scu, onboard)
        step = cost.travel_minutes(last, loc) if last is not None else 0.0
        trip.total_minutes += step
        trip.stops.append(Stop(
            location=loc,
            dropoffs=list(drops),
            pickups=list(picks),
            onboard_after=onboard,
            travel_from_prev=step,
        ))
        last = loc
    return trip
