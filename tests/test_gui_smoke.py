"""Headless smoke test: build a Contract->Leg->Cargo, optimize, tear down."""

from __future__ import annotations

import app.gui.main_window as m
from app.models import CargoItem, Contract


def test_box_parser() -> None:
    assert m.parse_boxes("32x1, 16x2") == {32: 1, 16: 2}
    assert m.parse_boxes("") == {}
    try:
        m.parse_boxes("abc")
    except ValueError:
        pass
    else:
        raise AssertionError("bad box string should raise")


def test_index_to_letters() -> None:
    assert m._index_to_letters(0) == "A"
    assert m._index_to_letters(25) == "Z"
    assert m._index_to_letters(26) == "AA"


def test_path_and_picker() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        assert app._path_label("HUR_L1") == "Stanton › Hurston › HUR-L1 Green Glade Station"
        assert app._path_label("KUDRE_ORE") == "Stanton › Crusader › Daymar › Kudre Ore"
        # search "kudre" then Enter accepts the highlighted autofill
        app.cpickup_picker.var.set("kudre")
        app.cpickup_picker._show()
        app.cpickup_picker._on_return(None)
        assert app.cpickup_picker.get_id() == "KUDRE_ORE"
    finally:
        app.destroy()


def test_build_contract_and_optimize() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        # Contract A: pickup Lorville, reward 80k, two commodities to one dropoff
        app.cpickup_picker.set_by_id("LORVILLE")
        app.reward_var.set("80000")
        app.commodity_var.set("Medical"); app.amount_var.set("30")
        app.cdropoff_picker.set_by_id("NEW_BABBAGE"); app._add_cargo()
        app.commodity_var.set("Food"); app.amount_var.set("20")
        app.cdropoff_picker.set_by_id("NEW_BABBAGE"); app._add_cargo()
        assert len(app.draft_cargo) == 2

        app._add_contract()
        assert len(app.contracts) == 1
        assert app.contracts[0].letter == "A"
        assert app._letter_counter == 1
        assert not app.draft_cargo                       # draft reset

        app._optimize()
        out = app._last_plan_text
        assert "SUMMARY" in out, out
        assert "PAYOUT" in out and "80,000" in out, out
        # same-dropoff cargo is grouped onto one move
        assert "Medical - 30 SCU, Food - 20 SCU" in out, out
        print("route preview:\n", out.strip()[:420])
    finally:
        app.destroy()


def test_cargo_with_different_dropoffs() -> None:
    app = m.CargoApp()
    try:
        app.cpickup_picker.set_by_id("LORVILLE")
        app.commodity_var.set("Medical"); app.amount_var.set("30")
        app.cdropoff_picker.set_by_id("NEW_BABBAGE"); app._add_cargo()
        app.commodity_var.set("Gold"); app.amount_var.set("20")
        app.cdropoff_picker.set_by_id("AREA18"); app._add_cargo()
        app._add_contract()
        legs = app._all_legs()                           # grouped by dropoff
        dropoffs = sorted(leg.dropoff for leg in legs)
        assert dropoffs == ["AREA18", "NEW_BABBAGE"], dropoffs
    finally:
        app.destroy()


def test_clear_labels_recompacts() -> None:
    app = m.CargoApp()
    try:
        app.contracts = [
            Contract("A", "LORVILLE", [CargoItem("X", 10, "AREA18")]),
            Contract("C", "AREA18", [CargoItem("Y", 10, "ORISON")]),
        ]
        app._letter_counter = 3            # B was used then removed -> gap
        app._clear_labels()
        assert [c.letter for c in app.contracts] == ["A", "B"]
        assert app._letter_counter == 2
    finally:
        app.destroy()


if __name__ == "__main__":
    test_box_parser()
    test_index_to_letters()
    test_path_and_picker()
    test_build_contract_and_optimize()
    test_cargo_with_different_dropoffs()
    test_clear_labels_recompacts()
    print("\nGUI smoke test passed.")
