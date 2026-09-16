#!/usr/bin/env python3
"""LOCAL DEVELOPMENT ONLY — checks that no panel page scrolls sideways.

What this checks
----------------
The owner reported (round-4) that in Safari the Nutrition page spilled off the
right edge of the screen. The cause was a WebKit-only race while the charts
start up: on a cold page load WebKit could briefly lay a chart canvas out too
wide, and — because the page's main column had no "you may shrink" rule — that
one wide canvas pinned the whole page wider than the window and never settled
back. This script guards against that ever coming back.

For each page + window width below it opens a FRESH browser (cold cache, the
condition that triggered the bug), waits for the charts, then measures whether
the page is any wider than the window. document.scrollWidth <= innerWidth + 1
means "nothing overflows". Any page over that is reported as a FAIL.

It drives WebKit specifically (Safari's engine) because Chrome never reproduced
the bug — the fix has to hold on the engine that actually broke.

What you need to run it
-----------------------
1. The local dev server running:  .venv/bin/python scripts/devserver.py
2. A Python with Playwright + WebKit installed. This is DEV TOOLING ONLY and is
   deliberately NOT in requirements.txt (the app never needs a browser). Set one
   up once in a throwaway venv:

       python3 -m venv /tmp/pwvenv
       /tmp/pwvenv/bin/pip install playwright
       /tmp/pwvenv/bin/playwright install webkit

   then run this script with that venv's python:

       /tmp/pwvenv/bin/python scripts/check-overflow.py

Exit code is 0 when every page fits, 1 if anything overflowed — so it can gate
a pre-deploy check.
"""
import sys

BASE = "http://localhost:5111"

# (page path, window width, how many cold loads). Nutrition is checked at three
# widths because that is where the owner saw it; the rest confirm the fix is
# page-agnostic (the layout rule it relies on is global).
PLAN = [
    ("/nutrition", 1000, 10),
    ("/nutrition", 1280, 10),
    ("/nutrition", 1440, 10),
    ("/", 1000, 5),  # dashboard
    ("/recovery", 1000, 5),
    ("/training", 1000, 5),
    ("/consistency", 1000, 5),
]

MEASURE = "() => ({sw: document.documentElement.scrollWidth, iw: innerWidth})"


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright not installed — see this file's header for the "
                 "one-time dev setup (it is intentionally not an app dependency).")

    failures = []
    with sync_playwright() as p:
        for path, width, loops in PLAN:
            worst = 0
            for _ in range(loops):
                browser = p.webkit.launch()  # fresh launch = cold cache each load
                page = browser.new_page(viewport={"width": width, "height": 1200})
                page.goto(f"{BASE}/dev-login?next={path}")
                page.wait_for_timeout(3500)  # let charts init + layout settle
                m = page.evaluate(MEASURE)
                over = m["sw"] - m["iw"]
                worst = max(worst, over)
                if over > 1:
                    failures.append((path, width, m["sw"], m["iw"]))
                browser.close()
            status = "OK" if worst <= 1 else f"OVERFLOW +{worst}px"
            print(f"{path:14s} @{width}px x{loops:<2}  worst=+{worst}px  {status}")

    print(f"\n{len(failures)} overflowing load(s).")
    for path, width, sw, iw in failures:
        print(f"  FAIL {path} @{width}: scrollWidth {sw} > innerWidth {iw}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
