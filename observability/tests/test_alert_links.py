"""Every alert opens its domain board, scoped to the site that raised it."""

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import catalogue

ROOT = Path(__file__).resolve().parents[2]
# A generated alert names the site it's about; any other alert, the site whose
# Prometheus raised it. The Telegram template appends the incident's time range.
URL = re.compile(r"https://grafana\.\$\{INTERNAL_DOMAIN\}/d/([a-z0-9-]+)\?var-cluster="
                 r"\{\{ \$(?:labels\.k8s_cluster|externalLabels\.cluster) \}\}")


def alerts():
    for path in sorted((ROOT / "kubernetes/apps").rglob("*.yaml")):
        text = path.read_text()
        if "kind: PrometheusRule" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "PrometheusRule":
                for group in doc["spec"]["groups"]:
                    for rule in group["rules"]:
                        if "alert" in rule:
                            yield path.relative_to(ROOT), rule


def test_every_alert_links_to_a_generated_board_scoped_to_its_site():
    boards = {board.uid for board in catalogue() if not board.kiosk}
    checked = 0
    for path, rule in alerts():
        if rule.get("labels", {}).get("severity") == "none":
            continue  # heartbeats and inhibitors never reach a person
        url = (rule.get("annotations") or {}).get("dashboard_url", "")
        match = URL.fullmatch(url)
        assert match and match[1] in boards, (str(path), rule["alert"], url)
        checked += 1
    assert checked > 80
