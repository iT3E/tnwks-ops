"""Exercise Grafana provisioning in a disposable instance with synthetic metrics."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from dashboards import catalogue
from rehearse_archive import docker, eventually, get
from tests.fake_prometheus import Handler
from versions import GRAFANA, RENDERER

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--renderer", action="store_true")
    args = parser.parse_args()
    name = "obs-test-grafana-" + secrets.token_hex(4)
    renderer = name + "-renderer"
    url = "http://127.0.0.1:13401"
    fixture = ThreadingHTTPServer(("127.0.0.1", 13402), Handler)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory(prefix="observability-grafana-") as tmp:
        base = Path(tmp)
        base.chmod(0o755)
        provisioning = base / "provisioning"
        for folder in ["dashboards", "datasources"]:
            (provisioning / folder).mkdir(parents=True)
        boards = [b for b in catalogue() if not b.kiosk]
        paths = {}
        for board in boards:
            target = base / "dashboards" / board.folder / f"{board.uid}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "kubernetes/apps/monitoring/grafana/dashboards" / target.name, target)
            paths[board.uid] = target
        provider = {"apiVersion": 1, "providers": [{"name": "observability", "orgId": 1,
            "type": "file", "disableDeletion": False, "allowUiUpdates": False,
            "updateIntervalSeconds": 2, "options": {"path": "/etc/dashboards",
            "foldersFromFilesStructure": True}}]}
        references = json.loads((ROOT / "kubernetes/apps/monitoring/grafana/upstream/catalogue.json").read_text())
        folders = {"unpoller": "UniFi Poller", "nginx": "NGINX", "alertmanager": "Kubernetes"}
        for entry in references:
            target = base / "reference" / entry["provider"] / entry["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "kubernetes/apps/monitoring/grafana/upstream" / entry["file"], target)
        for provider_name, folder in folders.items():
            provider["providers"].append({"name": provider_name, "folder": folder, "orgId": 1,
                "type": "file", "disableDeletion": False,
                "options": {"path": "/etc/reference/" + provider_name}})
        (provisioning / "dashboards/provider.yaml").write_text(yaml.safe_dump(provider))
        sources = {"apiVersion": 1, "datasources": [
            {"name": title, "uid": uid, "type": "prometheus", "access": "proxy", "editable": False,
             "isDefault": title == "Global", "url": "http://127.0.0.1:13402",
             "jsonData": {"httpMethod": "POST", "timeInterval": "1m"}}
            for title, uid in [("Global", "prometheus-global"), ("Local", "prometheus-local"),
                               ("Site 1", "prometheus-wsl"), ("Site 2", "prometheus-wsl-02"),
                               ("Prometheus", "PBFA97CFB590B2093")]]}
        (provisioning / "datasources/prometheus.yaml").write_text(yaml.safe_dump(sources))
        env = {"GF_SERVER_HTTP_ADDR": "127.0.0.1", "GF_SERVER_HTTP_PORT": "13401",
            "GF_SERVER_ROOT_URL": url + "/",
            "GF_AUTH_ANONYMOUS_ENABLED": "true", "GF_AUTH_ANONYMOUS_ORG_ROLE": "Viewer",
            "GF_ANALYTICS_REPORTING_ENABLED": "false", "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
            "GF_PLUGINS_PREINSTALL_DISABLED": "true", "GF_SECURITY_ADMIN_PASSWORD": secrets.token_hex(32)}
        try:
            if args.renderer:
                token = secrets.token_hex(32)
                renderer_env = dict(os.environ, AUTH_TOKEN=token)
                docker("run", "-d", "--name", renderer, "--network", "host", "--read-only",
                    "--user", "1000:1000", "--memory", "768m", "--cpus", "1", "--cap-drop", "ALL",
                    "--tmpfs", "/tmp:uid=1000,gid=1000,mode=1777", "-e", "AUTH_TOKEN",
                    "-e", "SERVER_ADDR=127.0.0.1:13403", "-e", "RATE_LIMIT_MAX_LIMIT=1",
                    "-e", "RATE_LIMIT_MIN_LIMIT=1", "-e", "RATE_LIMIT_MAX_AVAILABLE=536870912",
                    RENDERER, process_env=renderer_env)
                eventually(lambda: get("http://127.0.0.1:13403/healthz"), timeout=60)
                try:
                    get("http://127.0.0.1:13403/render?url=http://127.0.0.1:13401")
                except urllib.error.HTTPError as error:
                    assert error.code in (401, 403)
                else:
                    raise AssertionError("Renderer allowed an unauthenticated render")
                env.update(GF_RENDERING_SERVER_URL="http://127.0.0.1:13403/render",
                           GF_RENDERING_CALLBACK_URL=url + "/", GF_RENDERING_RENDERER_TOKEN=token)
            process_env = dict(os.environ, **env)
            environment = [arg for key in env for arg in ["-e", key]]
            docker("run", "-d", "--name", name, "--network", "host", "--memory", "1g", "--cpus", "1",
                "--read-only", "--tmpfs", "/var/lib/grafana:uid=472,gid=0", "--tmpfs", "/tmp:uid=472,gid=0",
                *environment, "--mount",
                f"type=bind,src={provisioning},dst=/etc/grafana/provisioning,readonly", "--mount",
                f"type=bind,src={base / 'dashboards'},dst=/etc/dashboards,readonly", "--mount",
                f"type=bind,src={base / 'reference'},dst=/etc/reference,readonly", GRAFANA,
                process_env=process_env)
            eventually(lambda: get(url + "/api/health"))

            def verify():
                found = json.loads(get(url + "/api/search?type=dash-db"))
                assert {r["uid"] for r in found} == {b.uid for b in boards} | {r["uid"] for r in references}
                for board in boards:
                    obj = json.loads(get(url + "/apis/dashboard.grafana.app/v2/namespaces/default/dashboards/" + board.uid))
                    assert obj["spec"]["title"] == board.title
                    assert obj["spec"]["layout"]["kind"] == "TabsLayout"
                    assert obj["metadata"]["annotations"]["grafana.app/folder"]
                    assert len(obj["spec"]["layout"]["spec"]["tabs"]) == len(board.tabs)

            eventually(verify)
            assert len(json.loads(get(url + "/api/datasources"))) == 5
            if args.browser:
                subprocess.run([sys.executable, str(ROOT / "observability/tests/browser_check.py")],
                    env=dict(os.environ, GRAFANA_TEST_URL=url), check=True)
            if args.renderer:
                with urllib.request.urlopen(url + "/render/d/home?width=1200&height=900&timeout=60",
                                            timeout=90) as response:
                    assert response.status == 200
                    assert response.read(8) == b"\x89PNG\r\n\x1a\n"
                print("PASS: authenticated Grafana→renderer callback produced a PNG; anonymous render rejected")
            paths["tnwks-alerts"].unlink()

            def verify_prune():
                try:
                    get(url + "/apis/dashboard.grafana.app/v2/namespaces/default/dashboards/tnwks-alerts")
                except urllib.error.HTTPError as error:
                    assert error.code == 404
                else:
                    raise AssertionError("deleted file remains provisioned")

            eventually(verify_prune)
            print(f"PASS: Grafana v2 UIDs, tab layouts, folders, five datasources, {len(references)} pinned reference dashboards and file deletion")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            subprocess.run(["docker", "rm", "-f", renderer], capture_output=True)
            fixture.shutdown()
            fixture.server_close()
            thread.join()


if __name__ == "__main__":
    main()
