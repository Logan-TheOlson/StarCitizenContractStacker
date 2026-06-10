"""Scrapers that refresh the universe data in app/data/.

verseguide.py  - drives verseguide.com (a Firestore-backed Vuetify SPA) with a
                 headless browser and reads the rendered DOM.
rsi_starmap.py - pulls the celestial skeleton + coordinates from RSI's starmap
                 JSON API (reliable, but lacks cargo locations).
"""
