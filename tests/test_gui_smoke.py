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


def test_breakdown_scu() -> None:
    cases = {1: {1: 1}, 3: {2: 1, 1: 1}, 6: {4: 1, 2: 1},
             7: {4: 1, 2: 1, 1: 1}, 8: {8: 1}, 11: {8: 1, 2: 1, 1: 1},
             16: {8: 2}, 24: {8: 3}}
    for total, expected in cases.items():
        got = m.breakdown_scu(total)
        assert got == expected, (total, got)
        assert sum(s * n for s, n in got.items()) == total
    assert m.format_boxes(m.breakdown_scu(11)) == "8x1, 2x1, 1x1"


def test_boxes_autofill_and_override() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        # typing SCU fills the box breakdown
        app.amount_var.set("11")
        assert app.boxes_var.get() == "8x1, 2x1, 1x1"
        # changing SCU re-suggests (not user-edited yet)
        app.amount_var.set("6")
        assert app.boxes_var.get() == "4x1, 2x1"
        # a manual edit is preserved across further SCU changes
        app.boxes_var.set("8x1")
        app.amount_var.set("20")
        assert app.boxes_var.get() == "8x1"

        # disallowed box size is rejected
        app.commodity_var.set("Gold")
        app.cdropoff_picker.set_by_id("AREA18")
        app.amount_var.set("5")
        app.boxes_var.set("3x1")
        app._add_cargo()
        assert not app.draft_cargo

        # empty boxes default to the fewest-container split on add
        app.boxes_var.set("")
        app.amount_var.set("7")
        app.cdropoff_picker.set_by_id("AREA18")
        app._add_cargo()
        assert app.draft_cargo[-1].boxes == {4: 1, 2: 1, 1: 1}

        # multi-size manual breakdown drives SCU when amount is blank
        app.commodity_var.set("Iron")
        app.cdropoff_picker.set_by_id("AREA18")
        app.amount_var.set("")
        app.boxes_var.set("8x2, 4x1")
        app._add_cargo()
        last = app.draft_cargo[-1]
        assert last.boxes == {8: 2, 4: 1} and last.scu == 20
    finally:
        app.destroy()


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


def test_arrow_keys_move_highlight() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        p = app.cpickup_picker
        p.var.set("")
        p._show()
        app.update_idletasks()
        base = p.listbox.curselection()[0]
        p._move(1)
        p._move(1)
        assert p.listbox.curselection()[0] == base + 2
        p._move(-1)
        assert p.listbox.curselection()[0] == base + 1
        # clamps at the top, never goes negative
        for _ in range(20):
            p._move(-1)
        assert p.listbox.curselection()[0] == 0
    finally:
        app.destroy()


def test_dropoff_sorts_closest_first() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        app.cpickup_picker.set_by_id("LORVILLE")
        d = app.cdropoff_picker
        d.var.set("")
        d._show()
        app.update_idletasks()
        dists = [app.cost.travel_minutes("LORVILLE", lid) for _, lid in d._matches]
        assert dists == sorted(dists), dists
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


def test_edit_contract_saves_in_place() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        app.cpickup_picker.set_by_id("LORVILLE"); app.reward_var.set("1000")
        app.commodity_var.set("Gold"); app.amount_var.set("10")
        app.cdropoff_picker.set_by_id("AREA18"); app._add_cargo()
        app._add_contract()
        app.cpickup_picker.set_by_id("AREA18"); app.reward_var.set("2000")
        app.commodity_var.set("Iron"); app.amount_var.set("20")
        app.cdropoff_picker.set_by_id("ORISON"); app._add_cargo()
        app._add_contract()

        # edit A: form is loaded, button switches to Save
        app._edit_contract(0)
        assert app._editing_index == 0 and app._editing_letter == "A"
        assert app.add_contract_btn.cget("text") == app._SAVE_LABEL
        assert app.cpickup_picker.get_id() == "LORVILLE"
        assert len(app.draft_cargo) == 1

        # save with a changed reward -> same letter/position, updated value
        app.reward_var.set("9999")
        app._add_contract()
        assert app._editing_index is None
        assert app.add_contract_btn.cget("text") == app._ADD_LABEL
        assert app.contracts[0].letter == "A" and app.contracts[0].reward == 9999
        assert [c.letter for c in app.contracts] == ["A", "B"]

        # editing then clicking edit again cancels
        app._edit_contract(1)
        app._edit_contract(1)
        assert app._editing_index is None
    finally:
        app.destroy()


def test_current_stop_tracking() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        app.cpickup_picker.set_by_id("LORVILLE")
        app.commodity_var.set("Gold"); app.amount_var.set("10")
        app.cdropoff_picker.set_by_id("AREA18"); app._add_cargo()
        app.commodity_var.set("Iron"); app.amount_var.set("15")
        app.cdropoff_picker.set_by_id("ORISON"); app._add_cargo()
        app._add_contract()
        app.cap_var.set("120"); app._optimize()
        app.update_idletasks()

        assert app._stop_gids, "stop order-ids not tracked"
        # fresh plan: first stop is current (arrow), nothing checked
        txt = app.route_text.get("1.0", "end")
        assert "▶" in txt  # current-stop marker present
        assert not app._checked

        # completing the first stop ticks its orders and advances the marker
        app._toggle_stop(0)
        assert all(g in app._checked for g in app._stop_gids[0])
        app._toggle_stop(0)  # undo
        assert not any(g in app._checked for g in app._stop_gids[0])
    finally:
        app.destroy()


def test_keyboard_field_navigation() -> None:
    app = m.CargoApp()
    try:
        app.update_idletasks()
        rec = []
        for idx, w in enumerate(app._field_order):
            w.focus_set = (lambda idx=idx: rec.append(idx))  # record target
        assert len(app._field_order) == 6
        assert app._field_nav(0, True) == "break" and rec[-1] == 1   # pickup->reward
        app._field_nav(4, True); assert rec[-1] == 5                 # dropoff->boxes
        app._field_nav(5, True); assert rec[-1] == 2                 # boxes->commodity loop
        app._field_nav(2, False); assert rec[-1] == 1                # shift-tab back
        app._field_nav(0, False); assert rec[-1] == 0                # clamp at pickup
        # Enter on a picker advances to the next field
        app.cpickup_picker._on_choose(); assert rec[-1] == 1
        app.commodity_picker._on_choose(); assert rec[-1] == 3
        app.cdropoff_picker._on_choose(); assert rec[-1] == 5
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
    test_breakdown_scu()
    test_boxes_autofill_and_override()
    test_path_and_picker()
    test_arrow_keys_move_highlight()
    test_dropoff_sorts_closest_first()
    test_build_contract_and_optimize()
    test_cargo_with_different_dropoffs()
    test_edit_contract_saves_in_place()
    test_current_stop_tracking()
    test_keyboard_field_navigation()
    test_clear_labels_recompacts()
    print("\nGUI smoke test passed.")
