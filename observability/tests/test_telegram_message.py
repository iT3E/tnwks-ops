"""Each alert of the repository renders to a Telegram message that the Bot API takes.

Alertmanager renders kubernetes/apps/monitoring/alertmanager/app/config/telegram.tmpl
with parse_mode HTML (html/template, so the values are escaped). This test renders
the message of each alert rule of kubernetes/apps with amtool of the Alertmanager image
of the HelmRelease: a group of 8 alerts firing (the template prints 8), and one alert
resolved. Each message must fit what the Bot API and Alertmanager require:

- the tags that Telegram knows, each closed in order, and `<a>` with a `href` only
  (the parser of the Bot API, TDLib's parse_html, refuses anything else);
- at most 4096 characters of text after the tags and entities, and at most 32768
  bytes in all (over either, Alertmanager v0.35 sends a fixed text in its place);
- each link an http or https URL of at most 2048 characters. The Bot API names no
  limit for a link; this is the bound of the template, and the graph link of a rule
  with a longer expression is left out.

The annotations are rendered as Prometheus would for their simple forms ($labels,
$externalLabels, Flux variables); any other template action becomes "1", so a text
that a query fills in is shorter here than live.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "kubernetes/apps/monitoring/alertmanager/app"
TEMPLATE = CONFIG / "config/telegram.tmpl"
# The URL of a Prometheus as the operator names it, for the graph link of an alert.
PROMETHEUS_URL = "http://prometheus-kube-prometheus-stack-prometheus.monitoring:9090"
LINK_LIMIT = 2048
TAGS = {"a", "b", "strong", "i", "em", "s", "strike", "del", "u", "ins", "span", "tg-spoiler", "tg-emoji",
        "tg-time", "pre", "code", "blockquote"}


def image() -> str:
    release = yaml.safe_load((CONFIG / "helmrelease.yaml").read_text())
    found = release["spec"]["values"]["controllers"]["alertmanager"]["containers"]["alertmanager"]["image"]
    return f'{found["repository"]}:{found["tag"]}'


def rules():
    for path in sorted((ROOT / "kubernetes/apps").rglob("*.yaml")):
        text = path.read_text()
        if "kind: PrometheusRule" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "PrometheusRule":
                for group in doc["spec"]["groups"]:
                    for rule in group["rules"]:
                        if "alert" in rule:
                            yield rule


def generator_url(expr: str) -> str:
    """As Prometheus 3 builds it: the external URL and strutil.TableLinkForExpression."""
    return f"{PROMETHEUS_URL}/graph?g0.expr={urllib.parse.quote_plus(expr)}&g0.tab=1"


def render_annotation(text: str, labels: dict[str, str]) -> str:
    text = re.sub(r"\$\{INTERNAL_DOMAIN\}", "example.org", text)
    text = re.sub(r"\$\{[A-Z0-9_]+(?::=[^}]*)?\}", "x", text)
    text = re.sub(r"\{\{\s*\$labels\.(\w+)\s*\}\}", lambda match: labels.get(match[1], ""), text)
    text = re.sub(r"\{\{\s*\$externalLabels\.cluster\s*\}\}", labels["cluster"], text)
    return re.sub(r"\{\{.*?\}\}", "1", text, flags=re.DOTALL)


def group(rule: dict, count: int, status: str) -> dict:
    """The data of a notification for `count` alerts of one rule, as template.Data."""
    named = set(re.findall(r"\$labels\.(\w+)", json.dumps(rule.get("annotations") or {})))
    alerts = []
    for index in range(count):
        labels = {"alertname": rule["alert"], **{k: str(v) for k, v in (rule.get("labels") or {}).items()},
                  "cluster": "homelab-wsl-02"}
        for name in sorted(named - labels.keys()):
            labels[name] = f"{name}-sample-{index}"
        annotations = {key: render_annotation(str(value), labels)
                       for key, value in (rule.get("annotations") or {}).items()}
        alerts.append({"status": status, "labels": labels, "annotations": annotations,
                       "startsAt": "2026-11-15T23:00:00Z",
                       "endsAt": "2026-11-16T00:00:00Z" if status == "resolved" else "0001-01-01T00:00:00Z",
                       "generatorURL": generator_url(str(rule["expr"])), "fingerprint": f"{index:016x}"})

    def common(key):
        first = alerts[0][key]
        return {k: v for k, v in first.items() if all(alert[key].get(k) == v for alert in alerts)}

    return {"receiver": "telegram", "status": status, "alerts": alerts, "groupLabels": {"alertname": rule["alert"]},
            "commonLabels": common("labels"), "commonAnnotations": common("annotations"),
            "externalURL": "https://alertmanager.example.org"}


def entity_length(text: str, i: int) -> int:
    """The length of an entity that Telegram decodes at text[i], or 0 (as TDLib)."""
    named = re.match(r"&(lt|gt|amp|quot);?", text[i:])
    if named:
        return len(named[0])
    numeric = re.match(r"&#(x[0-9a-fA-F]+|[0-9]+);?", text[i:])
    if numeric:
        value = numeric[1]
        code = int(value[1:], 16) if value.startswith("x") else int(value)
        if 0 < code < 0x10FFFF:
            return len(numeric[0])
    return 0


def parse_telegram_html(message: str) -> tuple[int, list[str]]:
    """Parse as TDLib's parse_html does; give the length of the text and the links,
    or raise ValueError where the Bot API answers "can't parse entities"."""
    text, links, open_tags, i = 0, [], [], 0
    while i < len(message):
        char = message[i]
        if char == "&" and (length := entity_length(message, i)):
            text += 1
            i += length
            continue
        if char != "<":
            text += 1
            i += 1
            continue
        end = message.find(">", i)
        if end < 0:
            raise ValueError(f"Unclosed start tag at byte offset {i}")
        tag = message[i + 1:end]
        if tag.startswith("/"):
            if not open_tags or tag[1:].strip() not in ("", open_tags[-1]):
                raise ValueError(f"Unmatched end tag {tag!r} at {i}")
            open_tags.pop()
        else:
            name, _, attributes = tag.partition(" ")
            if name.lower() not in TAGS:
                raise ValueError(f"Unsupported start tag {name!r} at {i}")
            found = dict(re.findall(r'(\w+)="([^"]*)"', attributes))
            if name == "a":
                if set(found) != {"href"} or not re.fullmatch(r'\s*href="[^"]*"\s*', attributes):
                    raise ValueError(f"<a> with {attributes!r} at {i}")
                links.append(re.sub(r"&(amp|lt|gt|quot);|&#([0-9]+);",
                                    lambda m: {"amp": "&", "lt": "<", "gt": ">", "quot": '"'}.get(m[1]) or chr(int(m[2])),
                                    found["href"]))
            elif attributes.strip():
                raise ValueError(f"<{name}> with attributes at {i}")
            open_tags.append(name.lower())
        i = end + 1
    if open_tags:
        raise ValueError(f"Unclosed tags {open_tags}")
    return text, links


def test_the_parser_of_this_test_refuses_what_telegram_refuses():
    assert parse_telegram_html('<b>x</b> <a href="https://a.example/?a=1&amp;b=2">y</a> &lt; &#43;') == (
        7, ["https://a.example/?a=1&b=2"])
    for bad in ("<b>x", "<b>x</i>", "<div>x</div>", "<a href='x' title='y'>z</a>", "x </b>", "<i class=\"y\">x</i>"):
        with pytest.raises(ValueError):
            parse_telegram_html(bad)


def test_the_template_leaves_out_a_graph_link_over_the_limit():
    template = TEMPLATE.read_text()
    assert f"(le (len $a.GeneratorURL) {LINK_LIMIT})" in template
    assert template.count("$a.GeneratorURL") == 3, "the link only where the condition holds"


@pytest.fixture(scope="module")
def messages():
    """Render each group with amtool, in one container."""
    if not shutil.which("docker"):
        if os.environ.get("CI"):
            pytest.fail("Docker is needed to render the Telegram messages")
        pytest.skip("docker not installed")
    work = Path(tempfile.mkdtemp(prefix="telegram-"))
    (work / "in").mkdir()
    (work / "out").mkdir()
    for path in (work, work / "in", work / "out"):
        path.chmod(0o777)  # amtool runs as nobody in its image
    shutil.copy(TEMPLATE, work / "telegram.tmpl")
    (work / "telegram.tmpl").chmod(0o644)
    cases = {}
    for index, rule in enumerate(rules()):
        for status, count in (("firing", 8), ("resolved", 1)):
            name = f"{index:03d}-{status}"
            cases[name] = (rule["alert"], status)
            (work / "in" / f"{name}.json").write_text(json.dumps(group(rule, count, status)))
            (work / "in" / f"{name}.json").chmod(0o644)
    script = ('for f in /work/in/*.json; do n=$(basename "$f" .json); '
              'amtool template render --template.glob=/work/telegram.tmpl --template.type=html '
              '--template.text=\'{{ template "telegram.message" . }}\' --template.data="$f" > "/work/out/$n.txt" '
              '|| echo "failed $n"; done')
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
                             "--mount", f"type=bind,src={work},dst=/work", image(), "-c", script],
                            capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0 and "failed" not in result.stdout, result.stdout + result.stderr
    out = {name: ((work / "out" / f"{name}.txt").read_text().rstrip("\n"), *case) for name, case in cases.items()}
    shutil.rmtree(work, ignore_errors=True)
    return out


def test_each_alert_renders_to_a_message_that_telegram_takes(messages):
    assert len(messages) > 200, "each alert of the repository, firing and resolved"
    problems = []
    for message, alert, status in messages.values():
        if not message.strip():
            problems.append((alert, status, "empty"))
            continue
        try:
            text, links = parse_telegram_html(message)
        except ValueError as error:
            problems.append((alert, status, str(error)))
            continue
        if text > 4096:
            problems.append((alert, status, f"{text} characters of text"))
        if len(message.encode()) > 1 << 15:
            problems.append((alert, status, f"{len(message.encode())} bytes"))
        for link in links:
            if len(link) > LINK_LIMIT or not re.match(r"https?://[^/]*\.", link):
                problems.append((alert, status, f"a link of {len(link)} characters: {link[:60]}"))
    assert not problems, "\n".join(map(str, problems))


def test_the_late_result_of_the_security_registry_keeps_its_board_link_and_drops_the_graph_link(messages):
    """The expression of SecurityResultLate has a term for each system, so its graph link
    is longer than the bound."""
    late = [message for message, alert, status in messages.values() if alert == "SecurityResultLate"]
    assert late
    for message in late:
        _, links = parse_telegram_html(message)
        assert any("/d/tnwks-security?" in link for link in links)
        assert not any("/graph?" in link for link in links)
    rule = next(rule for rule in rules() if rule["alert"] == "SecurityResultLate")
    assert len(generator_url(rule["expr"])) > LINK_LIMIT
