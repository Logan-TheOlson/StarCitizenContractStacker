"""Modern CustomTkinter UI for the cargo route optimizer.

Designed to sit next to Star Citizen on a second monitor or behind an alt-tab,
so the layout favours **fast contract entry** and a **glanceable route**:

    left pane   ->  enter contracts (sticky pickup, keyboard-driven) + ledger
    right pane  ->  the optimized route, rendered into a fast native tk.Text

Workflow (still Contract -> Cargo, two-stage):

1. Set the contract pickup (it stays put between contracts at the same hub) and
   optional reward.
2. Add cargo lines: commodity + SCU + dropoff (+ optional box sizes). Enter on
   the SCU/boxes field drops the line in; each line shows with a one-click ✕.
3. "Add contract" (or Ctrl+Enter) locks the letter (A, B, C…) into the ledger.
4. "Optimize route" plans across the whole ledger; each stop lists its DROP/LOAD
   orders (grouped by contract, with a tick-off checkbox per group) and a
   right-aligned capacity bar. "Copy" yanks a plain text version.

The "Overlay" switch collapses everything to a small, always-on-top route-only
window (no entry pane, stats, bars or checked-off orders) for use beside the
game.

Dark theme, rounded cards, light-blue accent. Locations use a search-first
picker showing the full System › Planet › Moon › Site path.
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk

from ..cost import CostModel
from ..datastore import load_locations, selectable_locations
from ..models import CargoItem, Contract, Leg, Ship
from ..optimizer import optimize
from ..report import format_plan

# --- palette: dark surfaces + light-blue accent --------------------------
ACCENT = "#4FA6E8"
ACCENT_HOVER = "#3E8FCC"
ACCENT_TEXT = "#0c1a24"
WINDOW_BG = "#1b1e23"
CARD_BG = "#23272e"
FIELD_BG = "#2b3038"
CHIP_BG = "#2f3742"
TEXT = "#E5E9F0"
MUTED = "#9aa4b2"
LOAD_GREEN = "#5BD6A6"
DROP_BLUE = "#7CC0F2"
DANGER = "#E8746B"
TRACK_BG = "#323a45"

# Common Stanton hauling commodities — drives the commodity autocomplete. Free
# text is still allowed for anything not listed.
COMMODITIES = sorted([
    "Agricium", "Agricultural Supplies", "Aluminum", "Aphorite", "Astatine",
    "Beryl", "Bexalite", "Carbon", "Chlorine", "Construction Materials",
    "Copper", "Corundum", "Diamond", "Distilled Spirits", "Dolivine",
    "Fluorine", "Gold", "Hephaestanite", "Hydrogen", "Iodine", "Iron",
    "Laranite", "Medical Supplies", "Pressurized Ice", "Processed Food",
    "Quantanium", "Quartz", "Recycled Material Composite", "Scrap", "Silicon",
    "Stims", "Titanium", "Tin", "Tungsten", "Waste",
])

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _asset(name: str) -> Path:
    """Locate a bundled asset, both in dev and inside a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    if base:                                  # frozen: assets/ sits at _MEIPASS
        return Path(base) / "assets" / name
    return Path(__file__).resolve().parents[2] / "assets" / name


# SCU container sizes a contract leg can be split into, largest first.
BOX_SIZES = (8, 4, 2, 1)


def breakdown_scu(total: int, sizes: tuple[int, ...] = BOX_SIZES) -> dict[int, int]:
    """Split ``total`` SCU into the fewest containers from ``sizes``.

    With 1, 2, 4, 8 SCU boxes a greedy largest-first split is optimal, e.g.
    3 -> {2: 1, 1: 1}, 11 -> {8: 1, 2: 1, 1: 1}, 16 -> {8: 2}. This mirrors how
    the game usually hands cargo out; the user can still override it.
    """
    boxes: dict[int, int] = {}
    remaining = max(0, int(total))
    for size in sorted(sizes, reverse=True):
        n, remaining = divmod(remaining, size)
        if n:
            boxes[size] = n
    return boxes


def format_boxes(boxes: dict[int, int]) -> str:
    """Render a box dict as ``'8x1, 2x1, 1x1'`` (largest size first)."""
    return ", ".join(f"{s}x{n}" for s, n in sorted(boxes.items(), reverse=True))


def parse_boxes(text: str) -> dict[int, int]:
    """Parse '8x1, 2x1' -> {8: 1, 2: 1}. Accepts multiple comma/semicolon-
    separated sizes. Empty -> {}."""

    boxes: dict[int, int] = {}
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        if "x" not in chunk:
            raise ValueError(f"Box entry '{chunk}' must look like '8x1'.")
        size_s, count_s = chunk.split("x", 1)
        try:
            size, count = int(size_s), int(count_s)
        except ValueError:
            raise ValueError(f"Box entry '{chunk}' must look like '8x1'.")
        if size <= 0 or count <= 0:
            raise ValueError(f"Box entry '{chunk}' must use positive numbers.")
        boxes[size] = boxes.get(size, 0) + count
    return boxes


def _index_to_letters(n: int) -> str:
    """0->A, 25->Z, 26->AA (spreadsheet-style, so we never run out)."""

    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class LocationPicker(ctk.CTkFrame):
    """Search-first field with a hierarchy-path dropdown.

    Generic over ``(label, value)`` options. With ``allow_free=True`` the typed
    text is returned verbatim when it matches no option (used for commodities).
    """

    MAX_RESULTS = 14

    def __init__(self, master, options, placeholder="Type to search…",
                 width=320, on_choose=None, allow_free=False, variable=None,
                 sort_key=None):
        super().__init__(master, fg_color="transparent")
        self.options = options
        # Pre-lowercase labels once so filtering doesn't re-lowercase every
        # option on every keystroke.
        self._opts_lower = [(label, label.lower(), lid) for label, lid in options]
        self.allow_free = allow_free
        # Optional ``sort_key(value) -> float | None``: when set, matches are
        # ordered ascending by it (closest first), with None sorted last.
        self._sort_key = sort_key
        self._id = None
        self._matches = []
        self._on_choose = on_choose
        self._close_after = None
        self.var = variable or tk.StringVar()
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=width,
                                  fg_color=FIELD_BG, border_color=FIELD_BG,
                                  placeholder_text=placeholder)
        self.entry.pack(fill="x")
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Button-1>", lambda _e: self._show())
        # Arrow keys move the highlighted match while focus stays in the entry;
        # Enter accepts it (see _on_return).
        self.entry.bind("<Down>", lambda _e: self._move(1))
        self.entry.bind("<Up>", lambda _e: self._move(-1))
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<FocusOut>", lambda _e: self._schedule_close())
        self.popup = None
        self.listbox = None

    def get_id(self):
        if self._id is None:
            text = self.var.get().strip()
            for label, lid in self.options:
                if label.lower() == text.lower():
                    self._id = lid
                    break
            if self._id is None and self.allow_free and text:
                return text
        return self._id

    def set_by_id(self, loc_id):
        self._id = loc_id or None
        self.var.set("")
        if loc_id:
            for label, lid in self.options:
                if lid == loc_id:
                    self.var.set(label)
                    return
            if self.allow_free:
                self.var.set(loc_id)

    def clear(self):
        self._id = None
        self.var.set("")

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            if event.keysym == "Escape":
                self._close()
            return
        self._id = None
        self._show()

    def _show(self):
        tokens = [t for t in self.var.get().lower().split() if t]
        if tokens:
            matches = [(label, lid) for label, low, lid in self._opts_lower
                       if all(t in low for t in tokens)]
        else:
            matches = list(self.options)
        # Sort closest-first (if a distance key is set) BEFORE truncating, so the
        # MAX_RESULTS shown are the nearest matches, not the alphabetically-first.
        if self._sort_key is not None:
            def _key(opt):
                d = self._sort_key(opt[1])
                return (d is None, d if d is not None else 0.0)
            matches.sort(key=_key)
        matches = matches[:self.MAX_RESULTS]
        if not matches:
            self._close()
            return
        self._matches = matches
        self._open()
        self.listbox.delete(0, "end")
        for label, _ in matches:
            self.listbox.insert("end", label)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(0)

    def _open(self):
        if self.popup is not None:
            self._position()
            return
        self.popup = tk.Toplevel(self)
        self.popup.wm_overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self._lb_font = tkfont.Font(font=("Segoe UI", 10))
        self.listbox = tk.Listbox(
            self.popup, activestyle="none", bg=FIELD_BG, fg=TEXT,
            selectbackground=ACCENT, selectforeground=ACCENT_TEXT,
            highlightthickness=1, highlightbackground=ACCENT, borderwidth=0,
            # Keep the highlighted row selected even while the entry keeps focus,
            # so <Return> has a current selection to accept.
            exportselection=False,
            font=self._lb_font, height=self.MAX_RESULTS)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Return>", lambda _e: self._accept())
        self.listbox.bind("<Double-Button-1>", lambda _e: self._accept())
        self.listbox.bind("<ButtonRelease-1>", self._click)
        self.listbox.bind("<Escape>", lambda _e: self._close())
        self._position()

    def _position(self):
        self.update_idletasks()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height() + 2
        rows = max(1, min(len(self._matches), self.MAX_RESULTS))
        self.listbox.configure(height=rows)
        # Widen to fit the longest match (full path labels are often wider than
        # the entry) so nothing is clipped; never shrink below the entry width.
        longest = max((self._lb_font.measure(label) for label, _ in self._matches),
                      default=0)
        w = max(self.entry.winfo_width(), longest + 24)
        self.popup.wm_geometry(f"{w}x{rows * 22 + 6}+{x}+{y}")

    def _on_return(self, _event):
        # Enter accepts the highlighted autofill match when the list is open...
        if self.popup is not None and self.listbox is not None:
            self._accept()            # _accept() fires _on_choose at the end
            return "break"
        # ...otherwise it just confirms and moves on (keyboard entry flow).
        if self._on_choose:
            self._on_choose()
            return "break"

    def _move(self, delta):
        """Move the highlighted match by ``delta`` rows, keeping focus in the
        entry. Opens the list on the first Down if it isn't showing yet."""
        if self.popup is None:
            self._show()
            return "break"
        if self.listbox is None or self.listbox.size() == 0:
            return "break"
        cur = self.listbox.curselection()
        i = cur[0] + delta if cur else (0 if delta > 0 else self.listbox.size() - 1)
        i = max(0, min(self.listbox.size() - 1, i))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.activate(i)
        self.listbox.see(i)
        return "break"

    def _click(self, event):
        if self.listbox is not None:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.listbox.nearest(event.y))
            self._accept()

    def _accept(self):
        if not self.listbox or not self.listbox.curselection():
            return
        label, loc_id = self._matches[self.listbox.curselection()[0]]
        self._id = loc_id
        self.var.set(label)
        self._close()
        self.entry.focus_set()
        self.entry.icursor("end")
        if self._on_choose:
            self._on_choose()

    def _schedule_close(self):
        # Delay so a click landing on the listbox is handled before we close.
        self._close_after = self.after(150, self._close)

    def _close(self):
        if self._close_after is not None:
            try:
                self.after_cancel(self._close_after)
            except Exception:
                pass
            self._close_after = None
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass            # already torn down (e.g. app closing)
            self.popup = None
            self.listbox = None

    def destroy(self):
        self._close()
        super().destroy()


# --- small widget helpers -------------------------------------------------

def _label(master, text, **kw):
    return ctk.CTkLabel(master, text=text, text_color=MUTED,
                        font=ctk.CTkFont(size=12), **kw)


def _section(master, text):
    return ctk.CTkLabel(master, text=text, text_color=ACCENT,
                        font=ctk.CTkFont(size=12, weight="bold"))


def _card(master) -> ctk.CTkFrame:
    return ctk.CTkFrame(master, fg_color=CARD_BG, corner_radius=14)


def _entry(master, var, width, placeholder=""):
    return ctk.CTkEntry(master, textvariable=var, width=width, fg_color=FIELD_BG,
                        border_color=FIELD_BG, placeholder_text=placeholder)


def _accent_btn(master, text, cmd, **kw):
    kw.setdefault("font", ctk.CTkFont(size=13, weight="bold"))
    return ctk.CTkButton(master, text=text, command=cmd, fg_color=ACCENT,
                         hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT, **kw)


def _ghost_btn(master, text, cmd, **kw):
    return ctk.CTkButton(master, text=text, command=cmd, fg_color=FIELD_BG,
                         hover_color="#343b45", text_color=TEXT, **kw)


class CargoApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Star Citizen Cargo Stack")
        self.geometry("1240x820")
        self.minsize(1060, 680)
        self.configure(fg_color=WINDOW_BG)
        self._set_window_icon()

        self.locations, self.distances = load_locations()
        self.cost = CostModel(self.locations, self.distances)
        self.loc_options = self._build_options()
        self.commodity_options = [(c, c) for c in COMMODITIES]

        # state -----------------------------------------------------------
        self.contracts: list[Contract] = []      # the locked-in ledger
        self.draft_cargo: list[CargoItem] = []    # cargo of the contract in progress
        self._letter_counter = 0
        self._last_plan = None
        self._last_plan_text = ""
        self._checked: set[int] = set()   # ticked-off order ids (route view)
        self._stop_gids: dict[int, list[int]] = {}  # stop index -> its order ids
        self._editing_index = None        # ledger row being edited, if any
        self._editing_letter = None       # its letter (preserved across the edit)
        self._ADD_LABEL = "✓  Add contract   (Ctrl+Enter)"
        self._SAVE_LABEL = "✓  Save changes"

        # vars the tests + entry rows bind to
        self.cap_var = tk.StringVar(value="120")
        self.reward_var = tk.StringVar()
        self.commodity_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.boxes_var = tk.StringVar()
        # Last box breakdown we auto-filled, so we know when the user has typed
        # their own (and we should stop overwriting it).
        self._boxes_auto = ""
        self.overlay_var = tk.BooleanVar(value=False)
        self._overlay = False
        self._afters: set[str] = set()    # pending after() ids, cancelled on close

        self._build()
        self.bind("<Control-Return>", lambda _e: self._add_contract())
        # Auto-suggest the box breakdown as the SCU amount is typed.
        self.amount_var.trace_add("write", self._on_amount_change)

    def _after(self, ms: int, cb) -> str:
        """``after`` that auto-deregisters and is cancelled on teardown, so a
        delayed callback never fires against a destroyed window."""
        def wrapped():
            self._afters.discard(aid)
            cb()
        aid = self.after(ms, wrapped)
        self._afters.add(aid)
        return aid

    def destroy(self) -> None:
        for aid in (*self._afters, getattr(self, "_rt_after", None)):
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        self._afters.clear()
        super().destroy()

    # -- data helpers ------------------------------------------------------

    def _name(self, loc_id: str) -> str:
        loc = self.locations.get(loc_id)
        return loc.name if loc else (loc_id or "—")

    def _path_label(self, loc_id: str) -> str:
        parts, cur, seen = [], loc_id, set()
        while cur and cur not in seen:
            seen.add(cur)
            loc = self.locations.get(cur)
            if not loc:
                break
            parts.append(loc.name)
            cur = loc.parent
        return " › ".join(reversed(parts))

    def _build_options(self):
        opts = [(self._path_label(l.id), l.id)
                for l in selectable_locations(self.locations)]
        opts.sort(key=lambda o: o[0].lower())
        return opts

    def _dist_key(self, ref_getter):
        """Build a ``sort_key`` for a LocationPicker that orders options by
        travel time from a reference location (resolved fresh on each open via
        ``ref_getter()``). Returns None when there's no reference yet, so the
        picker falls back to its alphabetical order."""
        def key(loc_id):
            ref = ref_getter()
            if not ref or not loc_id:
                return None
            return self.cost.travel_minutes(ref, loc_id)
        return key

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=5, uniform="cols")
        self.grid_columnconfigure(1, weight=6, uniform="cols")

        self._build_header()

        # Plain frame (not scrollable) so it doesn't nest a scroll region
        # around the ledger's own scroll — nested CTkScrollableFrames are the
        # main cause of sluggish scrolling/redraw.
        self.left = ctk.CTkFrame(self, fg_color="transparent")
        self.left.grid(row=1, column=0, sticky="nsew", padx=(14, 7), pady=(0, 12))
        self._build_entry_card(self.left)
        self._build_ledger_card(self.left)

        self.right = _card(self)
        self.right.grid(row=1, column=1, sticky="nsew", padx=(7, 14), pady=(0, 12))
        self._build_route_pane(self.right)

        self._refresh_contract_label()
        self._refresh_draft()
        self._refresh_ledger()
        self._render_route_placeholder()

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18,
                  pady=(14, 8))
        self.head = head

        self.title_box = ctk.CTkFrame(head, fg_color="transparent")
        self.title_box.pack(side="left")
        ctk.CTkLabel(self.title_box, text="Star Citizen Cargo Stack",
                     text_color=TEXT,
                     font=ctk.CTkFont(size=23, weight="bold")).pack(side="left")
        ctk.CTkLabel(self.title_box, text="  route optimizer", text_color=ACCENT,
                     font=ctk.CTkFont(size=14)).pack(side="left", pady=(6, 0))

        ctk.CTkSwitch(head, text="Overlay", variable=self.overlay_var,
                      command=self._toggle_overlay, progress_color=ACCENT,
                      text_color=MUTED, font=ctk.CTkFont(size=12, weight="bold")
                      ).pack(side="right", padx=(12, 0))

        self.cap_wrap = ctk.CTkFrame(head, fg_color="transparent")
        self.cap_wrap.pack(side="right")
        _label(self.cap_wrap, "Capacity SCU").pack(side="left", padx=(0, 6))
        _entry(self.cap_wrap, self.cap_var, 80).pack(side="left")
        _label(self.cap_wrap, "Start").pack(side="left", padx=(16, 6))
        self.start_picker = LocationPicker(self.cap_wrap, self.loc_options,
                                           width=240,
                                           placeholder="current location…")
        self.start_picker.pack(side="left")

    def _set_window_icon(self) -> None:
        ico = _asset("icon.ico")
        try:
            if ico.exists():
                self.iconbitmap(default=str(ico))   # title bar + taskbar
        except Exception:
            pass

    def _toggle_overlay(self) -> None:
        """Toggle a minimal, always-on-top overlay: just the stop list (no
        stats, no capacity bars, no Copy)."""
        on = bool(self.overlay_var.get())
        self._overlay = on
        self.attributes("-topmost", on)
        if on:
            # remember the full-window geometry so we can restore it
            self._normal_geometry = self.geometry()
            self.left.grid_remove()
            self.title_box.pack_forget()
            self.cap_wrap.pack_forget()
            self.copy_btn.pack_forget()
            self.stats_wrap.pack_forget()
            self.head.grid_configure(pady=(8, 4))
            # route pane takes the whole width
            self.right.grid_configure(column=0, columnspan=2, padx=14)
            self.minsize(280, 340)
            self.geometry("360x680")
        else:
            self.left.grid()
            self.right.grid_configure(column=1, columnspan=1, padx=(7, 14))
            self.cap_wrap.pack(side="right")
            self.title_box.pack(side="left")
            self.copy_btn.pack(side="right")
            self.stats_wrap.pack(fill="x", padx=12, pady=(0, 2),
                                 before=self.route_body)
            self.head.grid_configure(pady=(14, 8))
            self.minsize(1060, 680)
            self.geometry(getattr(self, "_normal_geometry", "1240x820"))
        # re-render so stop rows pick up / drop the capacity bars
        if self._rt_ctx:
            self._render_route(*self._rt_ctx)

    # -- contract entry ----------------------------------------------------

    def _build_entry_card(self, parent) -> None:
        card = _card(parent)
        card.pack(fill="x", pady=(0, 12))
        self.contract_label = _section(card, "NEW CONTRACT — A")
        self.contract_label.pack(anchor="w", padx=16, pady=(12, 6))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12)
        _label(top, "Pickup (stays for the next contract)").grid(
            row=0, column=0, padx=6, sticky="w")
        self.cpickup_picker = LocationPicker(
            top, self.loc_options, width=200,
            sort_key=self._dist_key(lambda: self.start_picker.get_id()))
        self.cpickup_picker.grid(row=1, column=0, padx=6, pady=(0, 8), sticky="ew")
        _label(top, "Reward aUEC").grid(row=0, column=1, padx=6, sticky="w")
        reward = _entry(top, self.reward_var, 120)
        reward.grid(row=1, column=1, padx=6, pady=(0, 8), sticky="w")
        top.grid_columnconfigure(0, weight=1)   # pickup grows with the pane

        ctk.CTkFrame(card, height=1, fg_color=FIELD_BG).pack(
            fill="x", padx=16, pady=(4, 8))

        # --- cargo entry: one row, a label above each field --------------
        _label(card, "Add cargo  ·  Enter to add line").pack(
            anchor="w", padx=16, pady=(0, 2))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 2))
        _label(row, "Commodity").grid(row=0, column=0, padx=4, sticky="w")
        _label(row, "SCU").grid(row=0, column=1, padx=4, sticky="w")
        _label(row, "Dropoff").grid(row=0, column=2, padx=4, sticky="w")
        _label(row, "Boxes").grid(row=0, column=3, padx=4, sticky="w")
        self.commodity_picker = LocationPicker(
            row, self.commodity_options, width=80, allow_free=True,
            variable=self.commodity_var, placeholder="commodity…")
        self.commodity_picker.grid(row=1, column=0, padx=4, pady=(0, 6), sticky="ew")
        amt = _entry(row, self.amount_var, 40, "SCU")
        amt.grid(row=1, column=1, padx=4, pady=(0, 6), sticky="ew")
        self.cdropoff_picker = LocationPicker(
            row, self.loc_options, width=80, placeholder="dropoff…",
            sort_key=self._dist_key(lambda: self.cpickup_picker.get_id()))
        self.cdropoff_picker.grid(row=1, column=2, padx=4, pady=(0, 6), sticky="ew")
        boxes = _entry(row, self.boxes_var, 70, "auto")
        boxes.grid(row=1, column=3, padx=4, pady=(0, 6), sticky="ew")
        _accent_btn(row, "+ Add", self._add_cargo, width=58).grid(
            row=1, column=4, padx=4, pady=(0, 6), sticky="w")
        # Fields share the pane width; the +Add button (col 4) stays put on the
        # right so it never gets pushed off the card.
        for _col, _wgt in ((0, 3), (1, 1), (2, 3), (3, 2)):
            row.grid_columnconfigure(_col, weight=_wgt)

        # --- keyboard-only entry flow ------------------------------------
        # Fields in tab order. After Boxes, Tab loops back to Commodity so you
        # can rattle off cargo lines without leaving the keyboard; Pickup and
        # Reward are per-contract and reached only at the start (or Shift+Tab).
        self._field_order = [
            self.cpickup_picker.entry, reward, self.commodity_picker.entry,
            amt, self.cdropoff_picker.entry, boxes,
        ]
        pickers = {0: self.cpickup_picker, 2: self.commodity_picker,
                   4: self.cdropoff_picker}
        for i, w in enumerate(self._field_order):
            pk = pickers.get(i)
            w.bind("<Tab>", lambda _e, i=i, pk=pk: self._field_nav(i, True, pk))
            for seq in ("<Shift-Tab>", "<ISO_Left_Tab>"):
                w.bind(seq, lambda _e, i=i, pk=pk: self._field_nav(i, False, pk))
            # Ctrl+Enter commits the contract from any field (the top-level bind
            # alone doesn't fire while a CTk entry holds focus).
            for seq in ("<Control-Return>", "<Control-KP_Enter>"):
                w.bind(seq, lambda _e: self._add_contract() or "break")
        # Enter advances like Tab; on the pickers this happens after the match
        # is accepted (see LocationPicker._on_return / _on_choose).
        self.cpickup_picker._on_choose = lambda: self._field_order[1].focus_set()
        self.commodity_picker._on_choose = lambda: self._field_order[3].focus_set()
        self.cdropoff_picker._on_choose = lambda: self._field_order[5].focus_set()
        reward.bind("<Return>", lambda _e: self._field_nav(1, True))
        amt.bind("<Return>", lambda _e: self._field_nav(3, True))
        boxes.bind("<Return>", lambda _e: self._add_cargo() or "break")

        # draft cargo lines (one-click ✕ each)
        self.draft_box = ctk.CTkFrame(card, fg_color="transparent")
        self.draft_box.pack(fill="x", padx=12, pady=(2, 2))

        self.entry_status = ctk.CTkLabel(card, text="", text_color=DANGER,
                                         font=ctk.CTkFont(size=11))
        self.entry_status.pack(anchor="w", padx=16)

        self.add_contract_btn = _accent_btn(
            card, self._ADD_LABEL, self._add_contract, height=36)
        self.add_contract_btn.pack(fill="x", padx=16, pady=(6, 14))

    def _flash(self, msg: str) -> None:
        self.entry_status.configure(text=msg)
        if msg:
            self._after(3500, lambda: self.entry_status.configure(text=""))

    def _refresh_draft(self) -> None:
        for w in self.draft_box.winfo_children():
            w.destroy()
        if not self.draft_cargo:
            _label(self.draft_box, "No cargo added yet.").pack(anchor="w", padx=4)
            return
        for i, item in enumerate(self.draft_cargo):
            line = ctk.CTkFrame(self.draft_box, fg_color=FIELD_BG, corner_radius=8)
            line.pack(fill="x", pady=2)
            ctk.CTkButton(line, text="✕", width=26, height=26, fg_color="transparent",
                          hover_color=DANGER, text_color=MUTED,
                          command=lambda idx=i: self._remove_cargo_at(idx)).pack(
                side="right", padx=4, pady=2)
            boxes = format_boxes(item.boxes)
            extra = f"  ·  {boxes}" if boxes else ""
            ctk.CTkLabel(
                line, text=f"{item.commodity} × {item.scu} SCU  →  "
                           f"{self._name(item.dropoff)}{extra}",
                text_color=TEXT, font=ctk.CTkFont(size=12), anchor="w").pack(
                side="left", padx=10, pady=3)

    def _remove_cargo_at(self, idx: int) -> None:
        if 0 <= idx < len(self.draft_cargo):
            del self.draft_cargo[idx]
            self._refresh_draft()

    def _field_nav(self, i: int, forward: bool, picker=None) -> str:
        """Move focus between cargo-entry fields by keyboard. If leaving a
        location picker with its dropdown open, accept the highlighted match
        first so a partial selection isn't lost."""
        if picker is not None and picker.popup is not None \
                and picker.listbox is not None:
            picker._accept()
        last = len(self._field_order) - 1
        if forward:
            nxt = 2 if i == last else min(i + 1, last)   # Boxes -> Commodity
        else:
            nxt = max(i - 1, 0)
        self._field_order[nxt].focus_set()
        return "break"

    def _on_amount_change(self, *_args) -> None:
        """Keep the Boxes field in sync with the SCU amount, suggesting the
        fewest-container breakdown -- unless the user has typed their own."""
        cur = self.boxes_var.get().strip()
        if cur and cur != self._boxes_auto:
            return                       # user-edited: leave their breakdown be
        text = self.amount_var.get().strip()
        if not text or not text.isdigit() or int(text) <= 0:
            if cur == self._boxes_auto:  # clear our suggestion too
                self.boxes_var.set("")
                self._boxes_auto = ""
            return
        self._boxes_auto = format_boxes(breakdown_scu(int(text)))
        self.boxes_var.set(self._boxes_auto)

    def _add_cargo(self) -> None:
        commodity = self.commodity_var.get().strip() or "Cargo"
        dropoff = self.cdropoff_picker.get_id()
        if not dropoff:
            self._flash("Select a dropoff location for this cargo.")
            return
        try:
            boxes = parse_boxes(self.boxes_var.get())
        except ValueError as e:
            self._flash(str(e))
            return
        bad = [s for s in boxes if s not in BOX_SIZES]
        if bad:
            allowed = ", ".join(str(s) for s in sorted(BOX_SIZES))
            self._flash(f"Box size {bad[0]} isn't valid — use {allowed} SCU.")
            return
        amount_text = self.amount_var.get().strip()
        if amount_text:
            try:
                scu = int(amount_text)
            except ValueError:
                self._flash("SCU amount must be a whole number.")
                return
        elif boxes:
            scu = sum(s * n for s, n in boxes.items())
        else:
            self._flash("Enter an SCU amount or box sizes.")
            return
        if scu <= 0:
            self._flash("SCU amount must be positive.")
            return
        # No explicit breakdown -> default to the fewest-container split.
        if not boxes:
            boxes = breakdown_scu(scu)
        self.draft_cargo.append(CargoItem(commodity, scu, dropoff, boxes))
        self._refresh_draft()
        self.commodity_picker.clear()
        self.amount_var.set("")
        self.boxes_var.set("")
        self._boxes_auto = ""
        self.cdropoff_picker.clear()
        self._flash("")
        self.commodity_picker.entry.focus_set()

    def _add_contract(self) -> None:
        if not self.draft_cargo:
            self._flash("Add at least one cargo line first.")
            return
        if not self.cpickup_picker.get_id():
            self._flash("Set the contract's pickup location.")
            return
        reward_text = self.reward_var.get().strip().replace(",", "")
        try:
            reward = int(reward_text) if reward_text else 0
        except ValueError:
            self._flash("Reward must be a whole number.")
            return
        contract = Contract(
            letter="", pickup=self.cpickup_picker.get_id(),
            cargo=list(self.draft_cargo), reward=reward)
        if self._editing_index is not None:
            # Save in place, preserving the contract's letter and position.
            idx = min(self._editing_index, len(self.contracts) - 1)
            contract.letter = self._editing_letter or self.contracts[idx].letter
            self.contracts[idx] = contract
            self._editing_index = None
            self._editing_letter = None
            self.add_contract_btn.configure(text=self._ADD_LABEL)
        else:
            contract.letter = _index_to_letters(self._letter_counter)
            self._letter_counter += 1
            self.contracts.append(contract)
        # reset the draft; keep the pickup sticky for the next contract
        self.draft_cargo = []
        self.reward_var.set("")
        self.cdropoff_picker.clear()
        self._refresh_draft()
        self._refresh_ledger()
        self._refresh_contract_label()
        self._flash("")
        # pickup stays sticky -> jump straight to the next line's commodity
        self.commodity_picker.entry.focus_set()

    # -- ledger ------------------------------------------------------------

    def _build_ledger_card(self, parent) -> None:
        card = _card(parent)
        card.pack(fill="both", expand=True)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 4))
        _section(head, "LEDGER").pack(side="left")
        self.ledger_count = _label(head, "")
        self.ledger_count.pack(side="left", padx=(8, 0))

        self.ledger_box = ctk.CTkScrollableFrame(card, fg_color="transparent",
                                                 height=180)
        self.ledger_box.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=10)
        _ghost_btn(actions, "Clear all", self._clear_all, width=84).pack(
            side="left", padx=3)
        _accent_btn(actions, "OPTIMIZE ROUTE", self._optimize, width=180,
                    height=38, font=ctk.CTkFont(size=14, weight="bold")).pack(
            side="right", padx=3)

    def _refresh_ledger(self) -> None:
        for w in self.ledger_box.winfo_children():
            w.destroy()
        total = sum(c.reward for c in self.contracts)
        self.ledger_count.configure(
            text=f"{len(self.contracts)} contract(s)"
                 + (f"  ·  {total:,} aUEC" if total else ""))
        if not self.contracts:
            _label(self.ledger_box,
                   "Add a contract to start building your run.").pack(
                anchor="w", padx=6, pady=6)
            return
        for ci, c in enumerate(self.contracts):
            editing = (ci == self._editing_index)
            block = ctk.CTkFrame(self.ledger_box,
                                 fg_color=CHIP_BG if editing else FIELD_BG,
                                 corner_radius=10,
                                 border_width=2 if editing else 0,
                                 border_color=ACCENT)
            block.pack(fill="x", pady=3)
            hdr = ctk.CTkFrame(block, fg_color="transparent")
            hdr.pack(fill="x")
            ctk.CTkButton(hdr, text="✕", width=26, height=26,
                          fg_color="transparent", hover_color=DANGER,
                          text_color=MUTED,
                          command=lambda i=ci: self._remove_contract(i)).pack(
                side="right", padx=4, pady=2)
            ctk.CTkButton(hdr, text="✎", width=26, height=26,
                          fg_color="transparent", hover_color=ACCENT,
                          text_color=ACCENT if editing else MUTED,
                          command=lambda i=ci: self._edit_contract(i)).pack(
                side="right", padx=0, pady=2)
            rew = f"   ·   {c.reward:,} aUEC" if c.reward else ""
            total_scu = sum(it.scu for it in c.cargo)
            ctk.CTkLabel(
                hdr, text=f"📄  Contract {c.letter}    ↑ "
                          f"{self._name(c.pickup)}   ·   {total_scu} SCU{rew}",
                text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w").pack(side="left", padx=10, pady=4)
            for item in c.cargo:
                ctk.CTkLabel(
                    block, text=f"      {item.commodity} × {item.scu} SCU  →  "
                                f"{self._name(item.dropoff)}",
                    text_color=MUTED, font=ctk.CTkFont(size=12),
                    anchor="w").pack(fill="x", padx=10, pady=(0, 1))
            ctk.CTkFrame(block, height=4, fg_color="transparent").pack()

    def _remove_contract(self, idx: int) -> None:
        if not (0 <= idx < len(self.contracts)):
            return
        del self.contracts[idx]
        if self._editing_index is not None:
            if idx == self._editing_index:
                self._cancel_edit()         # editing target gone -> stop editing
                return
            if idx < self._editing_index:
                self._editing_index -= 1
        self._refresh_ledger()

    def _edit_contract(self, idx: int) -> None:
        if not (0 <= idx < len(self.contracts)):
            return
        if self._editing_index == idx:      # clicking ✎ again cancels
            self._cancel_edit()
            return
        c = self.contracts[idx]
        self._editing_index = idx
        self._editing_letter = c.letter
        self.cpickup_picker.set_by_id(c.pickup)
        self.reward_var.set(str(c.reward) if c.reward else "")
        # copy the cargo so editing the draft doesn't mutate the saved contract
        self.draft_cargo = [CargoItem(it.commodity, it.scu, it.dropoff,
                                      dict(it.boxes)) for it in c.cargo]
        self.add_contract_btn.configure(text=self._SAVE_LABEL)
        self._refresh_draft()
        self._refresh_ledger()
        self._refresh_contract_label()
        self._flash(f"Editing Contract {c.letter} — change it, then Save.")

    def _cancel_edit(self) -> None:
        self._editing_index = None
        self._editing_letter = None
        self.draft_cargo = []
        self.reward_var.set("")
        self.cdropoff_picker.clear()
        self.add_contract_btn.configure(text=self._ADD_LABEL)
        self._refresh_draft()
        self._refresh_ledger()
        self._refresh_contract_label()
        self._flash("")

    def _clear_all(self) -> None:
        self.contracts = []
        self._editing_index = None
        self._editing_letter = None
        self.add_contract_btn.configure(text=self._ADD_LABEL)
        self._refresh_ledger()

    # -- contract lettering ------------------------------------------------

    def _refresh_contract_label(self) -> None:
        if self._editing_index is not None and self._editing_letter:
            self.contract_label.configure(
                text=f"EDITING CONTRACT — {self._editing_letter}")
        else:
            self.contract_label.configure(
                text=f"NEW CONTRACT — {_index_to_letters(self._letter_counter)}")

    def _clear_labels(self) -> None:
        for i, c in enumerate(self.contracts):
            c.letter = _index_to_letters(i)
        self._letter_counter = len(self.contracts)
        self._refresh_ledger()
        self._refresh_contract_label()

    # -- route rendering ---------------------------------------------------

    def _build_route_pane(self, card) -> None:
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 4))
        _section(head, "OPTIMIZED ROUTE").pack(side="left")
        self.copy_btn = _ghost_btn(head, "Copy", self._copy_route, width=70)
        self.copy_btn.pack(side="right")

        # Stats chips + notes live above the scroll region (fixed, cheap).
        self.stats_wrap = ctk.CTkFrame(card, fg_color="transparent")
        self.stats_wrap.pack(fill="x", padx=12, pady=(0, 2))

        # The stop list is a native tk.Text — it scrolls at OS speed no matter
        # how long the route is, unlike a CTkScrollableFrame full of widgets.
        self.route_body = ctk.CTkFrame(card, fg_color="transparent")
        body = self.route_body
        body.pack(fill="both", expand=True, padx=12, pady=(2, 12))
        self.route_text = tk.Text(
            body, bg=CARD_BG, fg=TEXT, bd=0, highlightthickness=0, wrap="word",
            padx=10, pady=6, font=("Segoe UI", 11), cursor="arrow",
            spacing1=0, spacing3=0)
        sb = ctk.CTkScrollbar(body, command=self.route_text.yview)
        sb.pack(side="right", fill="y")
        self.route_text.configure(yscrollcommand=sb.set)
        self.route_text.pack(side="left", fill="both", expand=True)
        self._configure_route_tags()
        self.route_text.configure(state="disabled")
        # Re-render on resize so the right-aligned bars track the pane width.
        self._rt_ctx = None
        self._rt_after = None
        self.route_text.bind("<Configure>", self._on_route_configure)
        # click a load/drop line (or its checkbox) to tick it off
        self.route_text.bind("<Button-1>", self._on_route_click)

    def _route_tab_x(self) -> int:
        # reserve room for the bar (150) + SCU label + margin, so the group
        # lands flush against the right edge
        w = self.route_text.winfo_width()
        if w <= 1:            # not realised yet -> sensible default
            w = 600
        return max(120, w - 262)

    def _on_route_configure(self, _event=None) -> None:
        if not self._rt_ctx:
            return
        if self._rt_after is not None:
            self.after_cancel(self._rt_after)
        self._rt_after = self.after(120, self._rerender_route)

    def _rerender_route(self) -> None:
        self._rt_after = None
        if self._rt_ctx:
            self._render_route(*self._rt_ctx)

    def _configure_route_tags(self) -> None:
        t = self.route_text
        t.tag_configure("num", foreground=ACCENT, font=("Segoe UI", 13, "bold"),
                        lmargin1=6, spacing1=14)
        # Tab stop parks the capacity bar in a fixed column to the right of the
        # stop title, so the bars line up regardless of title length.
        # NB: the tab stop that parks the bar is set on the *widget* (tag-level
        # -tabs is ignored by Tk); see _render_route / _route_tab_x.
        t.tag_configure("name", foreground=TEXT, font=("Segoe UI", 13, "bold"),
                        spacing1=14)
        # current ("you are here") stop: accent number + name, with a chip-tinted
        # line so it stands out at a glance while flying.
        t.tag_configure("cur_num", foreground=ACCENT, font=("Segoe UI", 13, "bold"),
                        lmargin1=6, spacing1=14, background=CHIP_BG)
        t.tag_configure("cur_name", foreground=ACCENT, font=("Segoe UI", 13, "bold"),
                        spacing1=14, background=CHIP_BG)
        # completed stop: dimmed number, struck-through name.
        t.tag_configure("dim", foreground=MUTED, font=("Segoe UI", 13, "bold"),
                        lmargin1=6, spacing1=14)
        t.tag_configure("done_name", foreground=MUTED, font=("Segoe UI", 13),
                        overstrike=True, spacing1=14)
        t.tag_configure("drop_lab", foreground=DROP_BLUE,
                        font=("Segoe UI", 10, "bold"), lmargin1=38)
        t.tag_configure("drop_item", foreground=DROP_BLUE, font=("Segoe UI", 11),
                        lmargin1=82, lmargin2=82, spacing3=1)
        t.tag_configure("load_lab", foreground=LOAD_GREEN,
                        font=("Segoe UI", 10, "bold"), lmargin1=38)
        t.tag_configure("load_item", foreground=LOAD_GREEN, font=("Segoe UI", 11),
                        lmargin1=82, lmargin2=82, spacing3=1)
        t.tag_configure("leg_head", foreground=MUTED,
                        font=("Segoe UI", 10, "bold"), lmargin1=56, spacing1=3)
        # ticked-off orders: greyed + struck through (non-overlay only)
        t.tag_configure("done", foreground=MUTED, overstrike=True)
        t.tag_configure("barlabel", foreground=MUTED, font=("Segoe UI", 10))
        t.tag_configure("trip", foreground=ACCENT, font=("Segoe UI", 12, "bold"),
                        lmargin1=6, spacing1=16, spacing3=2)
        t.tag_configure("warn", foreground=DANGER, font=("Segoe UI", 13, "bold"),
                        lmargin1=8, spacing1=8)
        t.tag_configure("placeholder", foreground=MUTED, font=("Segoe UI", 12),
                        lmargin1=10, lmargin2=10, spacing1=10)

    def _route_begin(self) -> None:
        self.route_text.configure(state="normal")
        self.route_text.delete("1.0", "end")
        for w in self.route_text.winfo_children():  # drop old embedded bars
            w.destroy()

    def _route_end(self) -> None:
        self.route_text.configure(state="disabled")
        self.route_text.yview_moveto(0.0)

    def _render_route_placeholder(self) -> None:
        self._rt_ctx = None
        for w in self.stats_wrap.winfo_children():
            w.destroy()
        self._route_begin()
        self.route_text.insert(
            "end",
            "Build your ledger, then hit OPTIMIZE ROUTE.\n\n"
            "The plan minimises stops first, then travel distance, then keeps "
            "the most spare cargo room.", ("placeholder",))
        self._route_end()

    def _stat(self, parent, value, label):
        cell = ctk.CTkFrame(parent, fg_color=FIELD_BG, corner_radius=10)
        ctk.CTkLabel(cell, text=value, text_color=TEXT,
                     font=ctk.CTkFont(size=17, weight="bold")).pack(
            padx=14, pady=(8, 0))
        ctk.CTkLabel(cell, text=label, text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(padx=14, pady=(0, 8))
        return cell

    def _stat_banner(self, text, color) -> None:
        b = ctk.CTkLabel(self.stats_wrap, text=text, text_color=color, anchor="w",
                         fg_color=CHIP_BG, corner_radius=8, justify="left",
                         font=ctk.CTkFont(size=12), wraplength=560)
        cols, rows = self.stats_wrap.grid_size()
        b.grid(row=rows, column=0, columnspan=max(1, cols), sticky="ew",
               padx=3, pady=(2, 4))

    @staticmethod
    def _badge(n: int) -> str:
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        return circled[n - 1] if 1 <= n <= 20 else f"{n}."

    def _assign(self, legs):
        """Group a stop's legs by contract; assign ONE stable render-order id
        per contract group (the tick-off unit). Always advances the counter so
        ids stay stable across renders."""
        groups: dict[str, list] = {}
        order: list[str] = []
        for leg in legs:
            key = leg.contract or "—"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].extend(leg.cargo)
        out = []
        for key in order:
            gid = self._seq
            self._seq += 1
            out.append((gid, key, groups[key]))
        return out

    def _visible(self, oid: int) -> bool:
        # checked orders are hidden entirely in overlay, shown (greyed) otherwise
        return not (self._overlay and oid in self._checked)

    def _render_legs(self, label, groups, lab_tag, item_tag) -> None:
        t = self.route_text
        if not any(self._visible(gid) for gid, _, _ in groups):
            return
        t.insert("end", f"{label}\n", (lab_tag,))
        for gid, key, items in groups:
            if not self._visible(gid):
                continue
            checked = gid in self._checked
            box = "☑  " if checked else "☐  "
            tag = f"ord{gid}"
            done = ("done",) if checked else ()
            t.insert("end", box, ("leg_head", tag))
            t.insert("end", f"Contract {key}\n", ("leg_head", tag) + done)
            for item in items:
                t.insert("end", f"{item.commodity} - {item.scu} SCU\n",
                         (item_tag, tag) + done)

    def _ins_stop(self, n: int, stop, capacity: int, si: int,
                  drop_groups, load_groups, state: str) -> None:
        if self._overlay and state == "done":
            return  # hide finished stops in the overlay

        t = self.route_text
        stop_tag = f"stop{si}"
        if state == "current":
            num_tag, name_tag, marker = "cur_num", "cur_name", "▶ "
        elif state == "done":
            num_tag, name_tag, marker = "dim", "done_name", "✓ "
        else:
            num_tag, name_tag, marker = "num", "name", ""
        t.insert("end", f"{marker}{self._badge(n)}  ", (num_tag, stop_tag))
        t.insert("end", self._name(stop.location), (name_tag, stop_tag))
        if self._overlay:
            # Overlay = bare minimum: just the stop name, no capacity bar.
            t.insert("end", "\n", (name_tag,))
        else:
            # Rounded capacity bar + label, right-aligned via the tab stop.
            frac = (stop.onboard_after / capacity) if capacity else 0
            bar = ctk.CTkProgressBar(self.route_text, width=150, height=11,
                                     corner_radius=5, progress_color=ACCENT,
                                     fg_color=TRACK_BG)
            bar.set(max(0.0, min(1.0, frac)))
            t.insert("end", "\t", (name_tag, stop_tag))
            t.window_create("end", window=bar, pady=2)
            t.insert("end", f"  {stop.onboard_after} / {capacity} SCU\n",
                     (name_tag, "barlabel"))
        self._render_legs("DROP", drop_groups, "drop_lab", "drop_item")
        self._render_legs("LOAD", load_groups, "load_lab", "load_item")

    def _on_route_click(self, event):
        idx = self.route_text.index(f"@{event.x},{event.y}")
        tags = self.route_text.tag_names(idx)
        for tag in tags:                       # an individual order line
            if tag.startswith("ord"):
                self._toggle_check(int(tag[3:]))
                return "break"
        for tag in tags:                       # the stop header -> whole stop
            if tag.startswith("stop"):
                self._toggle_stop(int(tag[4:]))
                return "break"

    def _toggle_check(self, oid: int) -> None:
        self._checked.symmetric_difference_update({oid})
        if self._rt_ctx:
            self._render_route(*self._rt_ctx)

    def _toggle_stop(self, si: int) -> None:
        """Mark a whole stop done (or undo it) -- ticks all its orders, which
        advances the highlighted 'current' stop to the next unfinished one."""
        gids = self._stop_gids.get(si, [])
        if not gids:
            return
        if all(g in self._checked for g in gids):
            self._checked.difference_update(gids)
        else:
            self._checked.update(gids)
        if self._rt_ctx:
            self._render_route(*self._rt_ctx)

    def _render_route(self, plan, capacity, reward, preserve_scroll=True) -> None:
        # Keep the reader's place across re-renders (ticking a box, resizing);
        # only a fresh optimize jumps back to the top.
        prev_scroll = self.route_text.yview()[0]
        for w in self.stats_wrap.winfo_children():
            w.destroy()
        self._route_begin()

        if not plan.feasible:
            self._rt_ctx = None
            self.route_text.insert("end", "⚠  Plan not feasible\n", ("warn",))
            for note in plan.notes:
                self.route_text.insert("end", note + "\n", ("placeholder",))
            self._route_end()
            return

        # right-align the capacity bars to the current pane width, and remember
        # context so a resize can re-lay them out (widget-level -tabs, not tag)
        self.route_text.configure(tabs=(self._route_tab_x(),))
        self._rt_ctx = (plan, capacity, reward)

        # stat chips (hidden in overlay mode)
        if not self._overlay:
            cells = [
                (f"{plan.total_stops}", "stops"),
            ]
            if reward:
                cells.append((f"{reward:,}", "aUEC"))
            for i, (val, lab) in enumerate(cells):
                self._stat(self.stats_wrap, val, lab).grid(
                    row=0, column=i, padx=3, pady=(2, 4), sticky="ew")
                self.stats_wrap.grid_columnconfigure(i, weight=1)
            for note in plan.notes:
                self._stat_banner("ℹ  " + note, ACCENT)

        # Pass 1: assign stable order ids per stop and figure out which stop is
        # "current" (the first one not fully ticked off).
        self._seq = 0                   # per-render order-id counter
        self._stop_gids = {}
        stop_groups: dict[int, tuple] = {}
        si = 0
        for trip in plan.trips:
            for stop in trip.stops:
                dg = self._assign(stop.dropoffs)
                lg = self._assign(stop.pickups)
                stop_groups[si] = (dg, lg)
                self._stop_gids[si] = [g for g, _, _ in dg] + [g for g, _, _ in lg]
                si += 1

        def _done(k: int) -> bool:
            gids = self._stop_gids.get(k, [])
            return bool(gids) and all(g in self._checked for g in gids)

        current = next((k for k in range(si) if not _done(k)), None)

        # Pass 2: render with current/done/upcoming state per stop.
        multi = len(plan.trips) > 1
        si = 0
        for ti, trip in enumerate(plan.trips, 1):
            if multi:
                util = (trip.peak_scu / capacity * 100) if capacity else 0
                self.route_text.insert(
                    "end",
                    f"TRIP {ti}  ·  peak {trip.peak_scu}/{capacity} SCU "
                    f"({util:.0f}%)\n", ("trip",))
            for j, stop in enumerate(trip.stops, 1):
                dg, lg = stop_groups[si]
                state = ("done" if _done(si)
                         else "current" if si == current else "upcoming")
                self._ins_stop(j, stop, capacity, si, dg, lg, state)
                si += 1
        self._route_end()
        if preserve_scroll:
            self.route_text.update_idletasks()
            self.route_text.yview_moveto(prev_scroll)

    def _copy_route(self) -> None:
        if not self._last_plan_text:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_plan_text)
        self.copy_btn.configure(text="Copied ✓")
        self._after(1500, lambda: self.copy_btn.configure(text="Copy"))

    # -- optimize / persistence -------------------------------------------

    def _capacity(self):
        try:
            cap = int(self.cap_var.get().replace(",", "").strip())
        except ValueError:
            self._render_route_error("Capacity must be a number in SCU.")
            return None
        if cap <= 0:
            self._render_route_error("Capacity must be positive.")
            return None
        return cap

    def _render_route_error(self, msg: str) -> None:
        self._rt_ctx = None
        for w in self.stats_wrap.winfo_children():
            w.destroy()
        self._route_begin()
        self.route_text.insert("end", "⚠  " + msg + "\n", ("warn",))
        self._route_end()

    def _all_legs(self) -> list[Leg]:
        """Flatten the ledger into optimizer tasks, grouping each contract's
        cargo by dropoff (one pickup -> dropoff move per group)."""

        legs: list[Leg] = []
        for c in self.contracts:
            groups: dict[str, list[CargoItem]] = {}
            for item in c.cargo:
                groups.setdefault(item.dropoff, []).append(item)
            for dropoff, items in groups.items():
                legs.append(Leg(c.pickup, dropoff, list(items), c.letter))
        return legs

    def _optimize(self) -> None:
        cap = self._capacity()
        if cap is None:
            return
        legs = self._all_legs()
        if not legs:
            self._render_route_error("Add at least one contract first.")
            return
        start = self.start_picker.get_id()
        plan = optimize(legs, Ship("Cargo", cap), self.cost, start=start)
        reward = sum(c.reward for c in self.contracts)
        self._checked = set()           # fresh plan -> nothing ticked off yet
        self._last_plan = plan
        self._last_plan_text = format_plan(plan, self.locations, cap,
                                           total_reward=reward)
        self._render_route(plan, cap, reward, preserve_scroll=False)


def run() -> None:
    # Give Windows an explicit app id so the taskbar uses our icon (and doesn't
    # group us under "python"). Must happen before the window is created.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "StarCitizenCargoStack.App")
    except Exception:
        pass
    # CTk re-runs widget-scaling callbacks on every window Configure event
    # (i.e. constantly while dragging/resizing, worst across monitors with
    # different DPI). We render at a fixed scale, so suppress that work for a
    # smooth move/resize. Purely disables the rescale callbacks.
    try:
        ctk.deactivate_automatic_dpi_awareness()
    except Exception:
        pass
    try:
        app = CargoApp()
    except Exception as e:
        # Don't dump a stack trace on a packaged build -- show what went wrong.
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Star Citizen Cargo Stack — startup error",
                             f"The app could not start:\n\n{e}")
        root.destroy()
        return
    app.mainloop()
