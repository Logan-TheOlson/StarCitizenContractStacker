# Star Citizen Cargo Stack — Route Optimizer

A desktop tool that takes several cargo-hauling contracts and computes the most
efficient route — pickups and drop-offs across multiple locations — while
tracking SCU on board so you never exceed your ship's capacity.

## What it does

- Enter contract **legs** manually: contract name, commodity, pickup, drop-off,
  SCU (and optionally a box breakdown like `32x1,16x2`).
- Pick your ship (built-in SCU presets, or a custom number).
- Click **Optimize** to get a stop-by-stop route with what to **LOAD**/**DROP**
  at each stop and the running on-board load.
- Save/Load your contract set as JSON.

### How "optimal" is defined

The optimizer models cargo hauling as a single-vehicle **Pickup-and-Delivery
Problem with a capacity constraint** and optimizes lexicographically:

1. **Fewest stops** (primary)
2. **Least travel time** (secondary)
3. **Best capacity use** — lower peak load as a tiebreak (more headroom)

It serves all legs in **one trip** when possible (exact branch-and-bound), and
when the cargo can't fit it splits into the **fewest** capacity-feasible trips.
Constraints honored: every pickup precedes its drop-off, and on-board SCU never
exceeds the ship (drop-offs are processed before pickups at each stop).

## Run it

```sh
python main.py
```

The core app needs **only the Python standard library** (tkinter is bundled
with the python.org Windows build). No install step for normal use.

## Run the tests

```sh
python -m tests.test_optimizer
python -m tests.test_gui_smoke
```

## Project layout

```
main.py                      entry point (launches the GUI)
app/
  models.py                  dataclasses: Location, Ship, Leg, Contract, RoutePlan
  cost.py                    travel-cost model (explicit distances + zone tiers)
  optimizer.py               the PDP solver (branch-and-bound + multi-trip)
  report.py                  renders a RoutePlan to text
  datastore.py               loads locations.json + the ship catalogue
  data/locations.json        curated universe data (the app's source of truth)
  data/verseguide_locations.json   scraped hierarchy (generated; optional)
  gui/main_window.py         the tkinter UI
  scraper/
    verseguide.py            Playwright scraper for verseguide.com
    rsi_starmap.py           RSI starmap JSON importer
tests/                       optimizer/GUI tests and recon probes
```

## Location & distance data

The app ships with a **curated `locations.json`** covering the cargo-relevant
places in Stanton (cities, stations, moons, key outposts, Lagrange points) plus
a light Pyro set. Travel cost uses explicit per-pair distances when present and
otherwise falls back to a **zone/tier model** derived from the location tree
(same body < same planet < interplanetary < intersystem).

### Refreshing data from VerseGuide

`verseguide.com` is a Vuetify single-page app backed by Google Firestore — data
streams in over Firestore's Listen channel and renders into the DOM, so there is
no plain REST endpoint to fetch. The scraper drives the page with a headless
browser and reads the rendered links:

```sh
pip install -r requirements.txt
playwright install chromium
python -m app.scraper.verseguide STANTON PYRO
```

This writes `app/data/verseguide_locations.json` with the celestial hierarchy
(planets, moons, Lagrange points, jump points).

**Not yet harvested:** granular surface outposts (rendered as Vuetify cards
behind a moon page's *LIST* tab) and route-calculator distances. The hooks for
both are stubbed in `app/scraper/verseguide.py`.

## Roadmap

- Scrape granular surface outposts + real route distances from VerseGuide.
- Merge scraped data into the app's primary `locations.json` under one id scheme.
- Optional: contract reward/profit-aware route ranking.
- Optional: screenshot/OCR import of in-game mission boards.
```
