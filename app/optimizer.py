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
* If one trip can't fit the cargo, we run a single continuous SHUTTLE instead of
  separate runs: load what fits, deliver, and on the way back fill the hold with
  whatever is going the other way (e.g. L2->L1 contracts), splitting any leg too
  big to carry in one go across multiple passes. Oversized legs are pre-split
  into capacity-sized chunks so "carry half now, half later" just works.
* Above a size threshold the exact search is skipped for a greedy
  nearest-feasible heuristic so the UI never hangs. The greedy result is then
  validated; if it can't serve everything in one capacity-feasible pass we fall
  back to the shuttle rather than emit an invalid route.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cost import CostModel
from .models import CargoItem, Leg, RoutePlan, Ship, Stop, Trip

# Distinct-location count above which we use the greedy heuristic instead of the
# exact branch-and-bound. B&B always returns the optimal stop count (and never
# an empty pass-through stop); its time is bounded by the node budget inside
# _branch_and_bound, so this can sit comfortably above 11 -- worst-case search
# stays under ~1s and real (varied-distance) inputs resolve far faster.
EXACT_LIMIT = 14


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

    cap = ship.scu_capacity

    # Self-loop legs (pickup == dropoff) are "deliver where you load" -- they're
    # never carried between locations, so they don't constrain routing or
    # capacity. Routing them would only break the single-visit assumption and
    # force pointless return trips, so we set them aside and re-attach them to
    # the stop at their location afterwards.
    selfloops = [leg for leg in legs if leg.pickup == leg.dropoff]
    real = [leg for leg in legs if leg.pickup != leg.dropoff]

    if cap <= 0:
        plan.feasible = False
        plan.notes.append("Ship capacity must be at least 1 SCU.")
        return plan

    # A leg bigger than the hold is no longer hopeless -- split it into
    # capacity-sized chunks and the shuttle carries it across several passes.
    real = _split_legs(real, cap)

    # No explicit start -> begin where you load the most cargo (the busiest
    # pickup hub). This avoids opening on an empty pass-through stop and matches
    # how you'd actually run it: fill up at the biggest pickup first.
    if start is None and real:
        load_by_pickup: dict[str, int] = {}
        legs_by_pickup: dict[str, int] = {}
        for leg in real:
            load_by_pickup[leg.pickup] = load_by_pickup.get(leg.pickup, 0) + leg.scu
            legs_by_pickup[leg.pickup] = legs_by_pickup.get(leg.pickup, 0) + 1
        start = max(legs_by_pickup,
                    key=lambda p: (legs_by_pickup[p], load_by_pickup[p]))

    # 1) Try to serve everything in a single trip (fewest possible stops).
    single = _best_order(real, cap, cost, start) if real else None
    if single is not None:
        plan.trips.append(_build_trip(single, real, cap, cost, start))
        _attach_selfloops(plan, selfloops, cost, cap, start)
        return plan

    # 2) One pass can't carry everything (capacity, or a hub that's both a pickup
    #    and a later dropoff). Run a continuous shuttle: load what fits, deliver,
    #    refill the return leg with whatever's heading back, revisiting as needed.
    trip = _shuttle_trip(real, cap, cost, start)
    plan.trips.append(trip)
    _attach_selfloops(plan, selfloops, cost, cap, start)

    revisited = len({s.location for s in trip.stops}) < len(trip.stops)
    if revisited:
        plan.notes.append(
            "Too much cargo for one load: shuttling back and forth, carrying "
            "return-trip contracts on the way to avoid empty legs."
        )
    delivered = sum(len(s.dropoffs) for s in trip.stops)
    if delivered < len(real):
        plan.feasible = False
        plan.notes.append(
            "Could not route every leg; some cargo is left undelivered.")
    return plan


def _attach_selfloops(
    plan: RoutePlan,
    selfloops: list[Leg],
    cost: CostModel,
    cap: int,
    start: str | None,
) -> None:
    """Fold deliver-where-you-load legs back into the route for display: load
    and deliver them at the stop already visiting their location. If the route
    doesn't otherwise visit that location, add a single stop for it."""
    if not selfloops:
        return
    by_loc: dict[str, list[Leg]] = {}
    for leg in selfloops:
        by_loc.setdefault(leg.pickup, []).append(leg)

    for loc, group in by_loc.items():
        stop = next((s for t in plan.trips for s in t.stops
                     if s.location == loc), None)
        if stop is not None:
            stop.pickups.extend(group)       # load and deliver in the one visit
            stop.dropoffs.extend(group)
            continue
        # Location isn't visited otherwise -> give it its own stop.
        if not plan.trips:
            plan.trips.append(Trip(capacity=cap))
        trip = plan.trips[0]
        last = trip.stops[-1].location if trip.stops else start
        onboard = trip.stops[-1].onboard_after if trip.stops else 0
        step = cost.travel_minutes(last, loc) if last is not None else 0.0
        trip.total_minutes += step
        trip.stops.append(Stop(
            location=loc,
            dropoffs=list(group),
            pickups=list(group),
            onboard_after=onboard,
            travel_from_prev=step,
        ))


# --- split-load shuttle (capacity-forced runs) ----------------------------

def _split_legs(legs: list[Leg], cap: int) -> list[Leg]:
    """Split any leg larger than the hold into capacity-sized chunks so it can
    be carried over multiple passes. Legs that already fit pass through."""
    out: list[Leg] = []
    for leg in legs:
        if leg.scu <= cap:
            out.append(leg)
            continue
        for chunk in _chunk_cargo(leg.cargo, cap):
            out.append(Leg(leg.pickup, leg.dropoff, chunk, leg.contract))
    return out


def _chunk_cargo(cargo: list[CargoItem], cap: int) -> list[list[CargoItem]]:
    """Partition cargo into groups each totalling <= cap SCU, splitting an
    individual item if it alone exceeds cap."""
    chunks: list[list[CargoItem]] = []
    cur: list[CargoItem] = []
    cur_scu = 0
    for item in cargo:
        remaining = item.scu
        while remaining > 0:
            if cur_scu >= cap:
                chunks.append(cur)
                cur, cur_scu = [], 0
            take = min(remaining, cap - cur_scu)
            cur.append(CargoItem(item.commodity, take, item.dropoff))
            cur_scu += take
            remaining -= take
    if cur:
        chunks.append(cur)
    return chunks


def _shuttle_trip(
    legs: list[Leg], cap: int, cost: CostModel, start: str | None
) -> Trip:
    """One continuous run that revisits locations until everything is delivered:
    at each stop drop what's destined there, then load as much waiting cargo as
    fits; head to a dropoff while carrying cargo, otherwise to the next pickup.

    Built directly (not via _build_trip) because the per-visit load split can't
    be reconstructed from a bare location sequence.
    """
    by_pickup, _ = _index_legs(legs)
    trip = Trip(capacity=cap)
    picked: set[int] = set()
    dropped: set[int] = set()
    onboard: list[Leg] = []
    load = 0
    last = start
    location = start
    total = len(legs)
    guard, max_guard = 0, total * 4 + 10

    def near(target: str) -> float:
        return cost.travel_minutes(last, target) if last is not None else 0.0

    while len(dropped) < total and guard < max_guard:
        guard += 1
        # 1) deliver onboard cargo whose destination is here
        drops = [l for l in onboard if l.dropoff == location]
        for l in drops:
            load -= l.scu
            dropped.add(id(l))
        if drops:
            onboard = [l for l in onboard if l.dropoff != location]
        # 2) load waiting cargo that fits (biggest chunks first to pack tight)
        avail = sorted((l for l in by_pickup.get(location, []) if id(l) not in picked),
                       key=lambda l: -l.scu)
        picks: list[Leg] = []
        for l in avail:
            if l.scu <= cap - load:
                picks.append(l)
                picked.add(id(l))
                onboard.append(l)
                load += l.scu
        if drops or picks:
            step = near(location)
            trip.total_minutes += step
            trip.peak_scu = max(trip.peak_scu, load)
            trip.stops.append(Stop(
                location=location, dropoffs=list(drops), pickups=list(picks),
                onboard_after=load, travel_from_prev=step))
            last = location
        # 3) clear the hold before going for more: head to a dropoff if carrying
        #    cargo, otherwise to the nearest remaining pickup.
        if onboard:
            targets = {l.dropoff for l in onboard}
        else:
            targets = {l.pickup for l in legs if id(l) not in picked}
        if not targets:
            break
        location = min(targets, key=near)
    return trip


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
        # Only ever go somewhere there's actual work to do right now -- cargo to
        # load, or onboard cargo we can deliver. Without this the heuristic will
        # happily open on the nearest location (often the start itself) even when
        # nothing can happen there yet, producing pointless empty pass-through
        # stops before any cargo is loaded.
        pool = [loc for loc in remaining
                if deliverable_drops(loc) or pending_picks(loc)]
        if not pool:
            pool = list(remaining)
        candidates = [
            loc for loc in pool
            if onboard - sum(l.scu for l in deliverable_drops(loc))
                       + sum(l.scu for l in pending_picks(loc)) <= cap
        ]
        if not candidates:
            candidates = pool
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

        def _near(c):
            return cost.travel_minutes(last, c) if last is not None else 0.0

        deliver_here = [c for c in candidates if deliverable_drops(c)]
        if deliver_here:
            # We're carrying cargo we can drop -- unload it now (frees the hold
            # and retires the stop). Nearest such stop wins.
            loc = min(deliver_here, key=lambda c: (_near(c), c in visited))
        else:
            # Nothing to deliver yet -- go load where we'll pick up the MOST,
            # i.e. fill the main hub before scattering to small pickups (avoids
            # visiting a small pickup early and having to return to its shared
            # location later). Break ties by distance.
            loc = min(candidates, key=lambda c: (
                -sum(l.scu for l in pending_picks(c)), _near(c), c in visited))
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
        # Never emit a stop where nothing happens -- you'd never fly there to do
        # nothing. Skip it and let the next real stop's travel be measured from
        # the last place we actually stopped. (Defensive: a good ordering won't
        # produce these, but this keeps any stray one out of the route.)
        if not drops and not picks:
            continue
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
