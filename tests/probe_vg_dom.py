"""Can we read VerseGuide's child-location list from the rendered DOM?
Check a system page (should list planets) and a moon page (should list outposts)."""

import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAGES = [
    "https://verseguide.com/location/STANTON",
    "https://verseguide.com/location/STANTON/DAYMAR",
]


def dump(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    print("\n" + "=" * 70)
    print("URL:", url)

    # Any anchors that point at other location pages = the child list.
    anchors = page.eval_on_selector_all(
        "a[href*='/location/']",
        "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText.trim()}))",
    )
    seen = set()
    print("--- location links ---")
    for a in anchors:
        key = a["href"]
        if key in seen:
            continue
        seen.add(key)
        if a["text"]:
            print(f"  {a['href']:<45} {a['text'][:40]}")

    # Look for any element whose class hints at a list/card of locations.
    classes = page.eval_on_selector_all(
        "[class]",
        "els => Array.from(new Set(els.map(e => e.className))).filter(c => "
        "typeof c==='string' && /(loc|card|list|item|child|outpost|poi)/i.test(c))",
    )
    print("--- candidate container classes ---")
    for c in classes[:25]:
        print("  ", c)


def run():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        for url in PAGES:
            dump(pg, url)
        b.close()


if __name__ == "__main__":
    run()
