"""Self-tests for the optimizer. Run: python -m tests.test_optimizer"""

from __future__ import annotations

from app.cost import CostModel
from app.datastore import load_locations
from app.models import Leg, Ship
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


if __name__ == "__main__":
    test_single_trip_respects_precedence_and_capacity()
    test_disjoint_legs_chain_into_one_trip()
    test_capacity_forces_multiple_trips()
    test_oversize_leg_is_infeasible()
    test_prefers_fewer_stops_in_one_trip()
    print("\nAll optimizer tests passed.")
