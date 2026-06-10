"""Self-tests for the optimizer. Run: python -m tests.test_optimizer"""

from __future__ import annotations

from app.cost import CostModel
from app.datastore import load_locations
from app.models import Leg, Location, Ship
from app.optimizer import optimize


def _cost() -> CostModel:
    locations, distances = load_locations()
    return CostModel(locations, distances)


def test_single_trip_respects_precedence_and_capacity() -> None:
    cost = _cost()
    legs = [
        Leg.single("LORVILLE", "NEW_BABBAGE", "Medical Supplies", 30, contract="A"),
        Leg.single("AREA18", "ORISON", "Agricium", 20, contract="B"),
    ]
    plan = optimize(legs, Ship("Freelancer MAX", 120), cost)
    assert plan.feasible
    assert len(plan.trips) == 1
    seq = [s.location for s in plan.trips[0].stops]
    # Every pickup must come before its dropoff.
    for leg in legs:
        assert seq.index(leg.pickup) < seq.index(leg.dropoff)
    # Never exceed capacity.
    assert plan.trips[0].peak_scu <= 120
    print("single-trip OK:", seq, f"{plan.total_minutes:.1f} min")


def test_disjoint_legs_chain_into_one_trip() -> None:
    cost = _cost()
    # Disjoint pickups/dropoffs: deliver each before loading the next, so peak
    # load stays low and all three fit in a single trip.
    legs = [
        Leg.single("LORVILLE", "NEW_BABBAGE", "Titanium", 40, contract="A"),
        Leg.single("AREA18", "ORISON", "Tungsten", 40, contract="B"),
        Leg.single("EVERUS_HARBOR", "PORT_TRESSLER", "Gold", 40, contract="C"),
    ]
    plan = optimize(legs, Ship("Cutlass", 46), cost)
    assert plan.feasible and len(plan.trips) == 1
    assert plan.trips[0].peak_scu <= 46
    print("chained single-trip OK: peak", plan.trips[0].peak_scu, "SCU")


def test_capacity_forces_multiple_trips() -> None:
    cost = _cost()
    # Both legs load at Lorville, so one visit must carry 80 SCU > capacity.
    legs = [
        Leg.single("LORVILLE", "AREA18", "Titanium", 40, contract="A"),
        Leg.single("LORVILLE", "ORISON", "Tungsten", 40, contract="B"),
    ]
    plan = optimize(legs, Ship("Cutlass", 46), cost)
    assert plan.feasible
    assert len(plan.trips) == 2, f"expected 2 trips, got {len(plan.trips)}"
    for trip in plan.trips:
        assert trip.peak_scu <= 46
    print("multi-trip OK:", len(plan.trips), "trips")


def test_oversize_leg_is_infeasible() -> None:
    cost = _cost()
    legs = [Leg.single("LORVILLE", "AREA18", "Ore", 100, contract="A")]
    plan = optimize(legs, Ship("Cutlass", 46), cost)
    assert not plan.feasible
    print("oversize OK:", plan.notes[0])


def test_prefers_fewer_stops_in_one_trip() -> None:
    cost = _cost()
    # Shared hub: two legs both start at Lorville -> one trip, one Lorville stop.
    legs = [
        Leg.single("LORVILLE", "AREA18", "Goods", 10, contract="A"),
        Leg.single("LORVILLE", "ORISON", "Goods", 10, contract="B"),
    ]
    plan = optimize(legs, Ship("Freelancer", 66), cost)
    assert plan.feasible and len(plan.trips) == 1
    locs = [s.location for s in plan.trips[0].stops]
    assert locs.count("LORVILLE") == 1  # visited once, not twice
    print("shared-hub OK:", locs)


def test_greedy_does_not_double_visit_a_completable_hub() -> None:
    # >EXACT_LIMIT distinct locations forces the greedy heuristic. A hub that is
    # both an outbound pickup (L4 -> M) and a later inbound dropoff (A -> L4)
    # must still be visited exactly once: load at A first, then a single L4 stop
    # delivers A's cargo and loads L4's. Regression for greedy revisiting it.
    names = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L4", "M", "N"]
    locs = {"STANTON": Location("STANTON", "Stanton", "system", "STANTON", None)}
    for n in names:
        locs["STANTON." + n] = Location(
            "STANTON." + n, n, "lagrange", "STANTON", "STANTON"
        )
    cost = CostModel(locs)

    def L(p, d, scu):
        return Leg.single("STANTON." + p, "STANTON." + d, "cargo", scu)

    legs = [
        L("A", "L4", 10), L("L4", "M", 10),
        L("B", "C", 5), L("D", "E", 5), L("F", "G", 5),
        L("H", "I", 5), L("J", "K", 5), L("N", "B", 5),
    ]
    plan = optimize(legs, Ship("hauler", 100), cost, start="STANTON.L4")
    assert plan.feasible
    visits = [s.location for t in plan.trips for s in t.stops]
    # No location is visited twice: distinct-location count == stop count.
    assert len(visits) == len(set(visits)), f"revisits in {visits}"
    # And precedence/delivery still hold for every leg.
    loaded: set[int] = set()
    delivered = 0
    for t in plan.trips:
        for s in t.stops:
            for leg in s.pickups:
                loaded.add(id(leg))
            for leg in s.dropoffs:
                assert id(leg) in loaded, "drop before load"
                delivered += 1
    assert delivered == len(legs)
    print("no-double-visit OK:", [v.split(".")[-1] for v in visits])


def test_large_instance_splits_when_capacity_forces_it() -> None:
    # >EXACT_LIMIT distinct locations (greedy path) where one hub's total pickup
    # far exceeds capacity: must split into capacity-feasible trips and deliver
    # every leg. Regression for the greedy path returning a single nonsense trip
    # that delivered nothing yet reported feasible.
    locs = {"S": Location("S", "S", "system", "S", None),
            "P": Location("P", "P", "lagrange", "S", "S")}
    legs = []
    for i in range(12):
        d = f"D{i}"
        locs[d] = Location(d, d, "lagrange", "S", "S")
        legs.append(Leg.single("P", d, "x", 40))
    plan = optimize(legs, Ship("h", 100), CostModel(locs))
    assert plan.feasible
    assert len(plan.trips) > 1, "should split into multiple trips"
    delivered = sum(len(s.dropoffs) for t in plan.trips for s in t.stops)
    assert delivered == len(legs), f"only {delivered}/{len(legs)} delivered"
    for t in plan.trips:
        assert t.peak_scu <= 100, f"trip over capacity: {t.peak_scu}"
    print("large-split OK:", len(plan.trips), "trips, all", len(legs), "delivered")


def test_capacity_tiebreak_prefers_lower_peak() -> None:
    # Two equal-time orderings; the optimizer should pick the one that keeps
    # peak load lower (deliver leg 1 before loading leg 2 -> peak 60, not 120).
    locs = {"S": Location("S", "S", "system", "S", None)}
    for n in ["A", "B", "C", "D"]:
        locs[n] = Location(n, n, "planet", "S", "S")
    cost = CostModel(locs, {"A": {"B": 1, "C": 1, "D": 1},
                            "B": {"C": 1, "D": 1}, "C": {"D": 1}})
    legs = [Leg.single("A", "B", "x", 60), Leg.single("C", "D", "y", 60)]
    plan = optimize(legs, Ship("h", 100), cost)
    assert plan.feasible and len(plan.trips) == 1
    assert plan.trips[0].peak_scu == 60, plan.trips[0].peak_scu
    print("peak-tiebreak OK: peak", plan.trips[0].peak_scu)


if __name__ == "__main__":
    test_single_trip_respects_precedence_and_capacity()
    test_disjoint_legs_chain_into_one_trip()
    test_capacity_forces_multiple_trips()
    test_oversize_leg_is_infeasible()
    test_prefers_fewer_stops_in_one_trip()
    test_greedy_does_not_double_visit_a_completable_hub()
    test_large_instance_splits_when_capacity_forces_it()
    test_capacity_tiebreak_prefers_lower_peak()
    print("\nAll optimizer tests passed.")
