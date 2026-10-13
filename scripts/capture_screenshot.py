"""Capture an actual browser screenshot of the seeded support-case page.

This script is used only in the optional GitHub Actions screenshot job; Playwright
is not a runtime dependency of the workbench itself.
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("LAB_SCREENSHOT_BASE_URL", "http://127.0.0.1:8000")
OUTPUT = Path(__file__).resolve().parent.parent / "screenshots" / "06-support-case-browser.png"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        response = page.goto(f"{BASE_URL}/cases/CASE-1001", wait_until="networkidle", timeout=30000)
        if response is None or response.status != 200:
            raise RuntimeError(f"Support-case page failed: HTTP {response.status if response else 'no response'}")
        page.locator('select[name="root_cause_status"]').wait_for(state="visible", timeout=10000)
        page.get_by_text("وضعیت علت ریشه‌ای", exact=True).wait_for(state="visible", timeout=10000)
        page.screenshot(path=str(OUTPUT), full_page=True)
        browser.close()
    print(f"Browser screenshot captured: {OUTPUT}")


if __name__ == "__main__":
    main()
