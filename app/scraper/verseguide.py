"""Scrape the celestial hierarchy from verseguide.com.

VerseGuide is a Vuetify single-page app backed by Google Firestore: data
arrives over Firestore's streaming "Listen" channel and is rendered into the
DOM. There is no plain REST endpoint to GET, so we drive the page with a
headless browser (Playwright) and read the rendered anchors.

A system page (e.g. /location/STANTON) renders the whole tree as links shaped
like ``/location/STANTON/2B#Daymar`` -- the path segment is VerseGuide's
designation code and the fragment is the human name. We parse those into the
same schema as app/data/locations.json so the result can drive the app.

Requires: ``pip install playwright`` then ``playwright install chromium``.

Granular surface outposts (rendered as Vuetify cards on moon pages, not as
links) and route-calculator distances are not yet harvested -- see
``scrape_surface_locations`` for where that extension hooks in.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

BASE = "https://verseguide.com/location"
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "verseguide_locations.json"

# digit prefix of a moon code (2 in "2B") -> roman numeral of its parent planet
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}


def _classify(code: str) -> str:
    """Infer a location type from a VerseGuide designation code."""

    if code.startswith("JP."):
        return "jumppoint"
    if re.fullmatch(r"[IVX]+", code):           # I, II, III, IV
        return "planet"
    if re.fullmatch(r"[IVX]+\.\d+", code):       # I.1  (Lagrange point)
        return "lagrange"
    if re.fullmatch(r"\d+[A-Z]+", code):         # 2B   (moon)
        return "moon"
    return "outpost"


def _parent(system: str, code: str) -> str:
    """Best-effort parent id for a code within ``system``."""

    if code.startswith("JP."):
        return system
    if re.fullmatch(r"[IVX]+", code):                 # planet -> system
        return system
    m = re.fullmatch(r"([IVX]+)\.\d+", code)          # Lagrange -> its planet
    if m:
        return f"{system}.{m.group(1)}"
    m = re.fullmatch(r"(\d+)[A-Z]+", code)            # moon -> its planet
    if m:
        roman = _ROMAN.get(int(m.group(1)))
        if roman:
            return f"{system}.{roman}"
    return system


def scrape_system(system: str, wait_ms: int = 6000) -> list[dict]:
    """Return location dicts for one system, harvested from its rendered page."""

    from playwright.sync_api import sync_playwright  # local import: optional dep

    locations: dict[str, dict] = {
        system: {"id": system, "name": system.title(), "type": "system",
                 "system": system, "parent": None}
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE}/{system}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)
        anchors = page.eval_on_selector_all(
            "a[href*='/location/']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        browser.close()

    for href in anchors:
        # want hrefs like /location/STANTON/<code>#<name>
        m = re.match(rf"/location/{re.escape(system)}/([^#]+)#(.*)", href)
        if not m:
            continue
        code, name = m.group(1), unquote(m.group(2)).strip()
        if not code or not name:
            continue
        loc_id = f"{system}.{code}"
        locations[loc_id] = {
            "id": loc_id,
            "name": name,
            "type": _classify(code),
            "system": system,
            "parent": _parent(system, code),
        }
    return list(locations.values())


def scrape_surface_locations(system: str, body_code: str) -> list[dict]:
    """Placeholder for granular outposts on a moon/planet surface.

    On a body page these render as Vuetify ``.location-card`` / chip elements
    (not anchors), and the list sits behind the page's LIST tab. Harvesting them
    means: navigate to /location/{system}/{body_code}, click the LIST tab, then
    read ``.location-card`` titles. Left as a TODO so the reliable hierarchy
    scrape ships first.
    """

    raise NotImplementedError(
        "Surface-outpost scraping is not implemented yet; see docstring.")


def scrape_to_file(systems: tuple[str, ...] = ("STANTON", "PYRO", "NYX"),
                   out: Path | None = None) -> Path:
    out = out or DATA_FILE
    all_locs: list[dict] = []
    seen: set[str] = set()
    for system in systems:
        print(f"scraping {system} ...", flush=True)
        for loc in scrape_system(system):
            if loc["id"] not in seen:
                seen.add(loc["id"])
                all_locs.append(loc)
    payload = {
        "_comment": "Scraped from verseguide.com via app/scraper/verseguide.py. "
                    "Hierarchy only (planets/moons/lagrange/jumppoints); surface "
                    "outposts and route distances not yet included.",
        "distances": {},
        "locations": all_locs,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"wrote {len(all_locs)} locations -> {out}")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = tuple(a.upper() for a in sys.argv[1:]) or ("STANTON", "PYRO", "NYX")
    scrape_to_file(args)
