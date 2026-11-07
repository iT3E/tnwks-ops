"""Exercise actual v2 panels/tabs in a disposable Grafana with fixture Prometheus."""

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import catalogue


def main():
    base = os.environ.get("GRAFANA_TEST_URL", "http://127.0.0.1:13101")
    out = Path(os.environ.get("GRAFANA_TEST_ARTIFACTS", "observability/.cache/browser"))
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    pending = {}
    failed = []
    http_errors = []
    console = []
    results = []
    with sync_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        browser = p.chromium.launch(headless=True, executable_path=executable,
                                    args=["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: pending.update({request.url: time.monotonic()}))
        page.on("requestfinished", lambda request: pending.pop(request.url, None))
        page.on("requestfailed", lambda request: failed.append({"url": request.url, "failure": request.failure}))
        page.on("response", lambda response: http_errors.append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
        page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
        for board in catalogue():
            if board.kiosk:
                continue
            page.goto(f"{base}/d/{board.uid}/view?var-datasource=prometheus-global", wait_until="networkidle")
            try:
                page.get_by_text(board.title, exact=True).filter(visible=True).first.wait_for(timeout=45000)
            except Exception:
                (out / "failed-page.html").write_text(page.content())
                (out / "failed-state.json").write_text(json.dumps(page.evaluate(
                    '({ready:document.readyState,body:!!document.body,root:document.getElementById("reactRoot")?.outerHTML,frames:[...document.querySelectorAll("iframe")].map(f=>f.src)})')))
                (out / "failed-network.json").write_text(json.dumps({"pending": pending, "failed": failed,
                    "errors": errors, "http_errors": http_errors, "console": console}, indent=2))
                raise
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
        reference_path = Path(__file__).resolve().parents[2] / "kubernetes/apps/monitoring/grafana/upstream"
        for reference in json.loads((reference_path / "catalogue.json").read_text()):
            title = json.loads((reference_path / reference["file"]).read_text())["title"]
            page.goto(f"{base}/d/{reference['uid']}/view", wait_until="domcontentloaded")
            page.get_by_text(title, exact=True).filter(visible=True).first.wait_for(timeout=45000)
            page.wait_for_timeout(500)
            body = page.locator("body").inner_text()
            for bad in ["Visualization plugin not found", "Panel plugin not found", "An unexpected error happened", "Data source was not found"]:
                assert bad not in body, (reference["uid"], bad)
        page.goto(f"{base}/d/home/view?var-datasource=prometheus-global", wait_until="domcontentloaded")
        page.get_by_text("Expected sites", exact=True).filter(visible=True).wait_for(timeout=45000)
        evidence = page.get_by_test_id("data-testid Panel header Site evidence")
        evidence.get_by_text("homelab-wsl-02", exact=True).wait_for(timeout=15000)
        evidence.get_by_text("Unknown", exact=True).wait_for(timeout=15000)
        (out / "home-rendered.html").write_text(page.content())
        page.screenshot(path=str(out / "homelab-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 768, "height": 1024})
        page.reload(wait_until="domcontentloaded")
        page.get_by_text("Expected sites", exact=True).filter(visible=True).wait_for(timeout=45000)
        page.get_by_test_id("data-testid Panel header Site evidence").get_by_text(
            "homelab-wsl-02", exact=True).wait_for(timeout=15000)
        page.screenshot(path=str(out / "homelab-tablet.png"), full_page=True, animations="disabled")
        # Real data-link interpolation, including two selected sites and incident
        # time. Looking for template strings alone cannot prove this behavior.
        page.set_viewport_size({"width": 1440, "height": 1000})
        incident_to = int(time.time() * 1000)
        incident_from = incident_to - 3600000
        query = urllib.parse.urlencode([
            ("from", str(incident_from)), ("to", str(incident_to)),
            ("var-datasource", "prometheus-global"),
            ("var-cluster", "homelab-wsl"), ("var-cluster", "homelab-wsl-02"),
        ])
        page.goto(f"{base}/d/home/view?{query}", wait_until="domcontentloaded")
        panel = page.get_by_test_id("data-testid Panel header Site evidence")
        panel.get_by_text("Reporting", exact=True).click(timeout=15000)
        diagnose = page.get_by_role("link", name="Diagnose", exact=True)
        target = urllib.parse.urlparse(diagnose.get_attribute("href"))
        params = urllib.parse.parse_qs(target.query)
        assert target.path == "/d/tnwks-sites/view"
        assert params["from"] == [str(incident_from)] and params["to"] == [str(incident_to)]
        assert params["var-cluster"] == ["homelab-wsl", "homelab-wsl-02"]
        assert params["var-datasource"] == ["prometheus-global"]
        diagnose.click()
        page.get_by_role("tab", name="Sites", exact=True).wait_for(timeout=15000)
        navigation = {"journey": "home to sites", "incident_time_preserved": True,
                      "selected_sites": params["var-cluster"]}
        browser.close()
    (out / "browser-results.json").write_text(json.dumps({"dashboards": results, "navigation": navigation,
        "page_errors": errors}, indent=2) + "\n")
    assert not errors, errors
    print(f"Rendered {len(results)} operator dashboards and six pinned references in Grafana; screenshots and evidence: {out}")


if __name__ == "__main__":
    main()
