"""Discover how verseguide.com loads its data: capture all network calls made
while a location page renders, and flag JSON/data responses."""

import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://verseguide.com/location/STANTON"
calls = []


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            calls.append((resp.status, ct.split(";")[0], resp.url))

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        title = page.title()
        # grab a chunk of rendered text to confirm data made it into the DOM
        body = page.inner_text("body")[:500]
        browser.close()

    print("page title:", title)
    print("\n--- DATA-LIKE RESPONSES (json / api / data) ---")
    for status, ct, url in calls:
        u = url.lower()
        if "json" in ct or "/api" in u or "data" in u or u.endswith(".json"):
            print(f"[{status}] {ct:<26} {url}")

    print("\n--- ALL XHR/FETCH-ISH HOSTS ---")
    hosts = {}
    for _, ct, url in calls:
        host = url.split("/")[2] if "//" in url else url
        hosts[host] = hosts.get(host, 0) + 1
    for host, n in sorted(hosts.items(), key=lambda x: -x[1]):
        print(f"  {n:>3}  {host}")

    print("\n--- RENDERED BODY PREVIEW ---")
    print(body)


if __name__ == "__main__":
    run()
