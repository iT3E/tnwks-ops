"""Every alert opens its domain board, scoped to the site that raised it."""

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import catalogue
from dashboards.boards.ai import ALERT_FILES, alert_names

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


def test_each_ai_alert_opens_the_ai_board_scoped_to_its_site():
    """The 19 alerts of the agent platform, the heartbeat and the model gateway
    open the AI board, and the board lists the same alerts as firing."""
    names = []
    for path, rule in alerts():
        if not str(path).startswith("kubernetes/apps/ai/"):
            continue
        match = URL.fullmatch((rule.get("annotations") or {}).get("dashboard_url", ""))
        assert match and match[1] == "tnwks-ai", (str(path), rule["alert"])
        names.append(rule["alert"])
    assert len(names) == 20
    assert sorted(names) == alert_names()
    assert {str(path) for path, _ in alerts() if str(path).startswith("kubernetes/apps/ai/")} == set(ALERT_FILES)


REPO_DOC = re.compile(r"https://github\.com/iT3E/tnwks-ops/blob/main/(docs/[^#]+\.md)#([a-z0-9-]+)")


def github_anchors(text):
    """The anchors GitHub makes for the headings of a Markdown file."""
    anchors, seen, fence = set(), {}, False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        if fence or not line.startswith("#"):
            continue
        title = line.lstrip("#").strip().lower()
        slug = re.sub(r"[^a-z0-9 _-]", "", title).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")
    return anchors


def test_every_runbook_in_this_repo_has_its_section():
    checked = 0
    for path, rule in alerts():
        url = (rule.get("annotations") or {}).get("runbook_url", "")
        match = REPO_DOC.fullmatch(url)
        if not url.startswith("https://github.com/iT3E/tnwks-ops/"):
            continue
        assert match, (str(path), rule["alert"], url)
        doc = ROOT / match[1]
        assert doc.is_file(), (str(path), rule["alert"], url)
        assert match[2] in github_anchors(doc.read_text()), (str(path), rule["alert"], url)
        checked += 1
    assert checked >= 15
