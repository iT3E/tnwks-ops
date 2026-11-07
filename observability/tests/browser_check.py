"""Exercise actual v2 panels/tabs in a disposable Grafana with fixture Prometheus."""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import catalogue


def main():
    base = os.environ.get("GRAFANA_TEST_URL", "http://127.0.0.1:13101")
    out = Path(os.environ.get("GRAFANA_TEST_ARTIFACTS", "/tmp/observability-browser"))
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    results = []
    with sync_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        browser = p.chromium.launch(headless=True, executable_path=executable,
                                    args=["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        for board in catalogue():
            if board.kiosk:
                continue
            page.goto(f"{base}/d/{board.uid}?var-datasource=prometheus-global", wait_until="domcontentloaded")
            page.get_by_text(board.title, exact=True).first.wait_for(timeout=15000)
            body = page.locator("body").inner_text()
            for bad in ["Visualization plugin not found", "Panel plugin not found", "An unexpected error happened"]:
                assert bad not in body, (board.uid, bad)
            assert "Unknown" in body or "No data" in body or "Expected sites" in body
            for tab in board.tabs:
                button = page.get_by_role("tab", name=tab, exact=True)
                assert button.count() == 1, (board.uid, "missing tab", tab)
                button.click()
                page.wait_for_timeout(150)
            results.append({"dashboard": board.uid, "title": board.title, "tabs": list(board.tabs)})
        page.goto(f"{base}/d/home?var-datasource=prometheus-global", wait_until="domcontentloaded")
        page.screenshot(path=str(out / "homelab-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 768, "height": 1024})
        page.reload(wait_until="domcontentloaded")
        page.screenshot(path=str(out / "homelab-tablet.png"), full_page=True)
        browser.close()
    (out / "browser-results.json").write_text(json.dumps({"dashboards": results, "page_errors": errors}, indent=2) + "\n")
    assert not errors, errors
    print(f"Rendered {len(results)} operator dashboards in Grafana; screenshots and evidence: {out}")


if __name__ == "__main__":
    main()
