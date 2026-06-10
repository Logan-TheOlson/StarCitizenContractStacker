"""Verify scraped verseguide data loads and works with the cost model."""

from __future__ import annotations

from pathlib import Path

from app.cost import CostModel
from app.datastore import DATA_DIR, load_locations


def test_scraped_file_loads_and_costs() -> None:
    path = DATA_DIR / "verseguide_locations.json"
    if not path.exists():
        print("no scraped file present; skipping")
        return
    locations, distances = load_locations(path)
    assert len(locations) > 20
    cost = CostModel(locations, distances)

    # Content-agnostic tier sanity check: two siblings (same parent) must cost
    # less than two locations in different systems (intersystem).
    by_parent: dict[str, list[str]] = {}
    for loc in locations.values():
        if loc.parent:
            by_parent.setdefault(loc.parent, []).append(loc.id)
    siblings = next((ids for ids in by_parent.values() if len(ids) >= 2), None)
    systems = {loc.system for loc in locations.values()}

    if siblings and len(systems) >= 2:
        sib_cost = cost.travel_minutes(siblings[0], siblings[1])
        a = next(iter(locations.values()))
        b = next(l for l in locations.values() if l.system != a.system)
        cross = cost.travel_minutes(a.id, b.id)
        assert sib_cost < cross, (sib_cost, cross)
        print(f"loaded {len(locations)} scraped locations across {len(systems)} "
              f"systems; siblings={sib_cost}m  intersystem={cross}m")
    else:
        print(f"loaded {len(locations)} scraped locations; "
              "not enough structure for tier check")


def test_missing_and_corrupt_files_raise_clear_errors() -> None:
    import tempfile

    missing = Path(tempfile.gettempdir()) / "definitely_not_here_locations.json"
    try:
        load_locations(missing)
    except RuntimeError as e:
        assert "missing" in str(e).lower(), e
    else:
        raise AssertionError("missing file should raise RuntimeError")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        f.write("{ not valid json ]")
        bad = Path(f.name)
    try:
        load_locations(bad)
    except RuntimeError as e:
        assert "corrupt" in str(e).lower(), e
    else:
        raise AssertionError("corrupt file should raise RuntimeError")
    finally:
        bad.unlink(missing_ok=True)
    print("datastore error-handling OK")


if __name__ == "__main__":
    test_scraped_file_loads_and_costs()
    test_missing_and_corrupt_files_raise_clear_errors()
    print("scrape-load test passed.")
