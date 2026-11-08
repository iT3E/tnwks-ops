"""Screenshot the generated boards against live data, read-only, in a throwaway Grafana.

The pinned Grafana runs in Docker with the HelmRelease's feature flags, every
datasource pointing at site 1's Thanos Query through a port-forward, and the
boards from this checkout, so a branch can be looked at before it merges.
Nothing in the cluster changes.

    uv run --project observability python observability/screenshot.py --out ~/shots
    uv run --project observability python observability/screenshot.py --out ~/shots \\
        --boards home,tnwks-sites --toggles grafana.visualDesignRefresh

Live screenshots show real access point, camera and host names: keep them off
GitHub. CI's fixture screenshots (rehearse_grafana.py --browser) are the ones to
share.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import yaml
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from dashboards import ROOT, catalogue
from versions import GRAFANA

THANOS_PORT = 19092
GRAFANA_PORT = 13411
DATASOURCES = [("Global", "prometheus-global"), ("Local", "prometheus-local"), ("Site 1", "prometheus-wsl"),
               ("Site 2", "prometheus-wsl-02"), ("House", "prometheus-house")]


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def eventually(check, timeout=120):
    end = time.time() + timeout
    while True:
        try:
            return check()
        except Exception:
            if time.time() > end:
                raise
            time.sleep(2)


def stage(base: Path) -> list:
    """The boards, provisioning and datasources for the throwaway Grafana."""
    provisioning = base / "provisioning"
    for folder in ("dashboards", "datasources"):
        (provisioning / folder).mkdir(parents=True)
    boards = catalogue()
    for board in boards:
        name = ("house" if board.kiosk else board.uid) + ".json"
        resource = json.loads((ROOT / "kubernetes/apps/monitoring/grafana/dashboards" / name).read_text())
        text = json.dumps(resource)
        if board.kiosk:
            # Its own uid would clash with Homelab's here, and the kiosk facade isn't
            # running: read the recorded state the facade serves.
            resource["metadata"]["name"] = "house"
            text = (json.dumps(resource).replace("homelab_house_state{", "max by (domain) (homelab:house_state{")
                    .replace('\\"}"', '\\"})"'))
        target = base / "dashboards" / board.folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (provisioning / "dashboards/boards.yaml").write_text(yaml.safe_dump({"apiVersion": 1, "providers": [{
        "name": "boards", "orgId": 1, "type": "file",
        "options": {"path": "/etc/dashboards", "foldersFromFilesStructure": True}}]}))
    (provisioning / "datasources/thanos.yaml").write_text(yaml.safe_dump({"apiVersion": 1, "datasources": [
        {"name": name, "uid": uid, "type": "prometheus", "access": "proxy", "url": f"http://127.0.0.1:{THANOS_PORT}",
         "isDefault": name == "Global", "jsonData": {"httpMethod": "POST", "timeInterval": "30s"}}
        for name, uid in DATASOURCES]}))
    return boards


def shoot(page, url: str, board, theme: str, width: int, out: Path) -> list[str]:
    uid = "house" if board.kiosk else board.uid
    page.goto(f"{url}/d/{uid}/view?theme={theme}&var-datasource=prometheus-global" + ("&kiosk" if board.kiosk else ""),
              wait_until="load", timeout=90000)
    title = page.get_by_text(board.title, exact=True).filter(visible=True).first
    try:
        title.wait_for(timeout=45000)
    except PlaywrightTimeoutError:
        # Grafana's first cold load can stall on its spinner; one reload.
        page.reload(wait_until="load")
        title.wait_for(timeout=90000)
    page.wait_for_timeout(6000)
    names = []
    for i, tab in enumerate(board.tabs):
        if i:
            page.get_by_role("tab", name=tab, exact=True).click()
            page.wait_for_timeout(6000)
        # CDP rather than page.screenshot, which waits on web fonts and can time out.
        session = page.context.new_cdp_session(page)
        shot = session.send("Page.captureScreenshot", {"format": "png"})
        session.detach()
        name = f"{theme}-{width}-{uid}-{i}-{tab.replace(' ', '_').replace('/', '_')}.png"
        (out / name).write_bytes(base64.b64decode(shot["data"]))
        names.append(name)
        print(name, flush=True)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for the PNGs (outside the repo)")
    parser.add_argument("--boards", default="", help="comma-separated UIDs (house for the kiosk); default every board")
    parser.add_argument("--toggles", default="", help="extra Grafana feature flags to try, comma-separated")
    parser.add_argument("--kubeconfig", default=os.path.expanduser("~/.kube/config-homelab-wsl"))
    args = parser.parse_args()
    out = args.out.expanduser().resolve()
    assert not out.is_relative_to(ROOT), "live screenshots stay out of the repo"
    out.mkdir(parents=True, exist_ok=True)
    only = set(filter(None, args.boards.split(",")))
    # Dark at desktop width for every board; light for Homelab; phone width for the kiosk.
    views = [("dark", 1600, None), ("light", 1600, {"home"}), ("dark", 412, {"house"})]

    url = f"http://127.0.0.1:{GRAFANA_PORT}"
    container = "tnwks-screenshot-" + secrets.token_hex(3)
    forward = subprocess.Popen(["kubectl", f"--kubeconfig={args.kubeconfig}", "-n", "monitoring", "port-forward",
                                "svc/thanos-query", f"{THANOS_PORT}:10902"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        eventually(lambda: get(f"http://127.0.0.1:{THANOS_PORT}/-/ready"))
        with tempfile.TemporaryDirectory(prefix="tnwks-screenshot-") as tmp:
            base = Path(tmp)
            base.chmod(0o755)
            boards = stage(base)
            release = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/grafana/app/helmrelease.yaml").read_text())
            toggles = sorted(k for k, v in release["spec"]["values"]["grafana.ini"]["feature_toggles"].items() if v is True)
            toggles += [t for t in args.toggles.split(",") if t]
            env = {"GF_SERVER_HTTP_ADDR": "127.0.0.1", "GF_SERVER_HTTP_PORT": str(GRAFANA_PORT),
                   "GF_AUTH_ANONYMOUS_ENABLED": "true", "GF_AUTH_ANONYMOUS_ORG_ROLE": "Viewer",
                   "GF_ANALYTICS_REPORTING_ENABLED": "false", "GF_PLUGINS_PREINSTALL_DISABLED": "true",
                   "GF_SECURITY_ADMIN_PASSWORD": secrets.token_hex(16), "GF_FEATURE_TOGGLES_ENABLE": ",".join(toggles)}
            subprocess.run(["docker", "run", "-d", "--name", container, "--network", "host", "--memory", "2g",
                            "--tmpfs", "/var/lib/grafana:uid=472,gid=0",
                            *[a for k, v in env.items() for a in ("-e", f"{k}={v}")],
                            "--mount", f"type=bind,src={base / 'provisioning'},dst=/etc/grafana/provisioning,readonly",
                            "--mount", f"type=bind,src={base / 'dashboards'},dst=/etc/dashboards,readonly",
                            GRAFANA], check=True, capture_output=True)
            try:
                eventually(lambda: get(url + "/api/health"))
                eventually(lambda: len(json.loads(get(url + "/api/search?type=dash-db"))) >= len(boards) or 1 / 0)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                    for theme, width, selection in views:
                        page = browser.new_page(viewport={"width": width, "height": 2200 if width > 1000 else 2400})
                        for board in boards:
                            uid = "house" if board.kiosk else board.uid
                            if (only and uid not in only) or (selection is not None and uid not in selection):
                                continue
                            shoot(page, url, board, theme, width, out)
                        page.close()
                    browser.close()
            finally:
                subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)
    finally:
        forward.terminate()
    print(f"Screenshots in {out}")


if __name__ == "__main__":
    main()
