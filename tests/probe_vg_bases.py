"""Open VerseGuide's per-body location dropdown and read all base options."""

import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAGES = [
    "https://verseguide.com/location/STANTON/2B",   # Daymar (known bases)
    "https://verseguide.com/location/PYRO/III",      # Bloom
]


def harvest(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    print("\n" + "=" * 70, "\nURL:", url)
    # The body's location selector is a Vuetify select (.mobi-drop). Click it to
    # open the overlay menu, then read the option titles.
    opened = False
    for sel in (".mobi-drop", ".mobi-drop input", ".v-select"):
        try:
            page.click(sel, timeout=2000)
            opened = True
            break
        except Exception:
            continue
    page.wait_for_timeout(1200)
    # Vuetify renders open menus into .v-menu__content; options are list titles.
    options = []
    for sel in (".v-menu__content .v-list-item__title",
                ".v-menu__content .v-list-item__content",
                ".menuable__content__active .v-list-item__title"):
        try:
            options = page.eval_on_selector_all(
                sel, "els => els.map(e => e.innerText.trim()).filter(Boolean)")
        except Exception:
            options = []
        if options:
            break
    print(f"opened={opened}  options={len(options)}")
    seen = []
    for o in options:
        o = " ".join(o.split())
        if o and o not in seen:
            seen.append(o)
    for o in seen[:60]:
        print("   ", o[:70])


def run():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        for url in PAGES:
            harvest(pg, url)
        b.close()


if __name__ == "__main__":
    run()
