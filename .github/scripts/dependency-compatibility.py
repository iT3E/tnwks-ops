#!/usr/bin/env python3
"""Watch compatibility evidence without executing upstream content or changing pins."""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".github/dependency-compatibility.json"
UPDATE_NOTICE = "Compatibility evidence changed again. Review the updated issue body before approving an upgrade."


def download(url):
    headers = {"User-Agent": "tnwks-dependency-compatibility", "Accept": "application/vnd.github+json"}
    if urllib.parse.urlparse(url).hostname == "api.github.com" and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(4_000_001).decode("utf-8")


def evidence(source, root=ROOT, fetch=download):
    kind = source["kind"]
    if kind == "unifi-support":
        text = fetch(source["url"])
        section = text.split("### Setting Up Your External Database", 1)[1]
        paragraphs = re.split(r"\n\s*\n", section.strip())
        text = next(p for p in paragraphs if "supported" in p.lower() and "mongo" in p.lower())
        value = " ".join(text.split())
    elif kind == "coral-releases":
        releases = json.loads(fetch(source["url"]))
        value = [
            {"tag": release["tag_name"], "prerelease": release["prerelease"],
             "assets": sorted(
                 [{"name": asset["name"], "updated": asset["updated_at"],
                   "digest": asset.get("digest")}
                  for asset in release["assets"]
                  if "win" in asset["name"].lower() or asset["name"].endswith(".zip")],
                 key=lambda asset: asset["name"])}
            for release in releases if not release["draft"]
        ]
        value.sort(key=lambda release: release["tag"])
        if not value:
            raise ValueError("release evidence is empty")
    elif kind == "unifi-image":
        text = (root / source["path"]).read_text()
        match = re.search(r"repository:\s*ghcr\.io/linuxserver/unifi-network-application\s*\n\s*tag:\s*(\S+)", text)
        value = match[1]
    elif kind == "coral-runtime":
        defaults = (root / source["defaults"]).read_text()
        requirements = (root / source["requirements"]).read_text()
        value = {
            key: re.search(r"^" + key + r":\s*(\S+)", defaults, re.MULTILINE)[1]
            for key in ["coral_detector_python_version", "coral_detector_backend"]
        }
        value["tflite"] = re.search(r"^tflite-runtime\s*@\s*(\S+)", requirements, re.MULTILINE)[1]
        value["host_overrides"] = {}
        for path in sorted((root / source["inventory"]).rglob("main.yml")):
            overrides = re.findall(
                r"^(coral_detector_(?:backend|python_version|devices)):([^\n]*(?:\n[ \t]+[^\n]*)*)",
                path.read_text(), re.MULTILINE)
            if overrides:
                value["host_overrides"][str(path.relative_to(root))] = overrides
    else:
        raise ValueError("unknown evidence kind")
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inspect(config, today=None, observe=evidence):
    today = today or dt.date.today()
    report = []
    for watch in config["watches"]:
        reasons = []
        if today >= dt.date.fromisoformat(watch["review_after"]):
            reasons.append("The periodic compatibility review is due.")
        for source in watch["sources"]:
            try:
                current = observe(source)
            except (OSError, ValueError, IndexError, KeyError, TypeError, StopIteration):
                reasons.append(source["name"] + ": evidence could not be checked; investigate the watch.")
            else:
                if current != source["sha256"]:
                    reasons.append(source["name"] + ": compatibility evidence changed "
                                   "(" + source["sha256"][:12] + " -> " + current[:12] + ").")
        marker = "<!-- dependency-compatibility:" + watch["id"] + " -->"
        body = "\n\n".join([
            marker,
            "Compatibility review is required. These signals do not prove the upgrade is supported.",
            "\n".join("- " + reason for reason in reasons),
            watch["action"],
            "Routine incompatible updates remain listed under Compatibility review in the Renovate Dashboard (#43). "
            "Security-fix PRs bypass dashboard approval and still require CI and impact review.",
            "Update the evidence baseline in a PR only after reviewing support and testing the proposed migration. "
            "Closing this issue without updating the baseline will cause the next run to reopen it.",
            "Runbook: docs/operations/dependency-compatibility.md",
            "Sources:\n" + "\n".join("- " + s["url"] for s in watch["sources"] if "url" in s),
        ])
        report.append({"id": watch["id"], "title": watch["title"], "marker": marker,
                       "needs_review": bool(reasons), "body": body})
    return report


def gh(repo, *args):
    return subprocess.check_output(["gh", *args, "--repo", repo], text=True)


def publish(report, repo):
    # Every generated title/body is checked before any public write. Upstream
    # prose and release bodies are never included in issues or logs.
    for item in report:
        subprocess.run([str(ROOT / "bin/privacy-check"), "--text"],
                       input=item["title"] + "\n" + item["body"] + "\n" + UPDATE_NOTICE,
                       text=True, check=True, cwd=ROOT)
    issues = json.loads(gh(repo, "issue", "list", "--state", "all", "--label", "question",
                           "--limit", "1000", "--json", "number,title,body,state,author"))
    trusted_authors = {repo.split("/")[0], "github-actions", "github-actions[bot]", "app/github-actions"}
    issues = [issue for issue in issues if issue.get("author", {}).get("login") in trusted_authors]
    for item in report:
        existing = next((issue for issue in issues if item["marker"] in (issue["body"] or "")), None)
        if not item["needs_review"]:
            if existing and existing["state"] == "OPEN":
                gh(repo, "issue", "close", str(existing["number"]))
            continue
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as body_file:
            body_file.write(item["body"])
            body_file.flush()
            if existing:
                if existing["body"] != item["body"] or existing["title"] != item["title"]:
                    gh(repo, "issue", "edit", str(existing["number"]), "--title", item["title"],
                       "--body-file", body_file.name)
                    if existing["state"] == "OPEN":
                        body_file.seek(0)
                        body_file.truncate()
                        body_file.write(UPDATE_NOTICE + "\n")
                        body_file.flush()
                        gh(repo, "issue", "comment", str(existing["number"]), "--body-file", body_file.name)
                if existing["state"] == "CLOSED":
                    gh(repo, "issue", "reopen", str(existing["number"]))
            else:
                gh(repo, "issue", "create", "--title", item["title"], "--body-file", body_file.name,
                   "--label", "question", "--assignee", repo.split("/")[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-reviewed-baseline", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--publish-report", type=Path)
    parser.add_argument("--repo", default="iT3E/tnwks-ops")
    args = parser.parse_args()
    if args.publish_report:
        publish(json.loads(args.publish_report.read_text()), args.repo)
        return
    config = json.loads(CONFIG.read_text())
    if args.accept_reviewed_baseline:
        today = dt.date.today()
        for watch in config["watches"]:
            watch["last_reviewed"] = today.isoformat()
            watch["review_after"] = (today + dt.timedelta(days=watch["review_interval_days"])).isoformat()
            for source in watch["sources"]:
                source["sha256"] = evidence(source)
        CONFIG.write_text(json.dumps(config, indent=2) + "\n")
        print("Recorded reviewed evidence; commit and review this baseline through a PR.")
        return
    report = inspect(config)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    for item in report:
        print(item["id"] + ": " + ("review required" if item["needs_review"] else "evidence unchanged"))


if __name__ == "__main__":
    main()
