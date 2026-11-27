"""Each host of an internal Ingress of site 1 has a router record.

The router answers each name of the internal ingress with a shared VIP
(infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf,
docs/wsl/multi-site.md). A name with no record there falls to the
internal.tnwks.us wildcard (modules/mikrotik/dns.tf), the old way through
the desktop (docs/wsl/lan-exposure.md), and not to the ingress of the
shared VIP. This test walks the apps that site 1 applies, the way
test_ai_inventory.py does, takes the host of each Ingress of the class
nginx-internal (in a manifest or in the values of a HelmRelease), and
holds each one to a record of that name, except the site-local UIs that
reach site 1 through the wildcard on purpose (WILDCARD_ONLY).
"""

import json
import re
from pathlib import Path

import yaml
from test_ai_inventory import walk

ROOT = Path(__file__).resolve().parents[2]
DNS = ROOT / "infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf"
INVENTORY = ROOT / "observability/generated/inventory.json"
SITES = {"homelab-wsl": "wsl", "homelab-wsl-02": "wsl-02"}
CLASS = "nginx-internal"
# The hosts of site 1 with no record on purpose: the UIs of the site-local
# Prometheus, Alertmanager and Pyrra, which must not follow a shared VIP to
# the other site. Their names resolve through the wildcard to the ingress of
# site 1 only (docs/operations/observability-operations.md, "Access per
# site"). The list is exact: a host on it with a record fails too.
WILDCARD_ONLY = {
    "alertmanager.internal.tnwks.us",
    "prometheus.internal.tnwks.us",
    "pyrra.internal.tnwks.us",
}


def substitute(text: str, site: str = "wsl") -> str:
    """The Flux variables of a site. SECRET_DOMAIN in cluster-settings.yaml
    has no effect: the Secret that comes after it gives the apex, which is
    INTERNAL_DOMAIN without its first label (the comment in the file of
    site 1)."""
    settings = yaml.safe_load((ROOT / "kubernetes/clusters" / site / "cluster-settings.yaml").read_text())["data"]
    values = {**settings, "SECRET_DOMAIN": settings["INTERNAL_DOMAIN"].removeprefix("internal.")}

    def one(match: re.Match) -> str:
        if match[1] in values:
            return str(values[match[1]])
        assert match[3] is not None, f"no value for {match[0]}"
        return match[3]

    return re.sub(r"\$\{([A-Z0-9_]+)(:=([^}]*))?\}", one, text)


def chart_hosts(node):
    """The hosts of each ingress of the class in chart values: app-template
    (className and hosts of dicts) and other charts (ingressClassName and
    hosts of names)."""
    if isinstance(node, dict):
        if CLASS in (node.get("className"), node.get("ingressClassName")) and isinstance(node.get("hosts"), list):
            for host in node["hosts"]:
                yield host["host"] if isinstance(host, dict) else host
        for value in node.values():
            yield from chart_hosts(value)
    elif isinstance(node, list):
        for value in node:
            yield from chart_hosts(value)


def internal_hosts(site: str = "wsl") -> set[str]:
    """The hosts of the class in the apps that a site applies."""
    found = set()
    for _, doc in walk(ROOT / "kubernetes/clusters" / site / "apps"):
        if doc.get("kind") == "Ingress" and doc["spec"].get("ingressClassName") == CLASS:
            found |= {substitute(rule["host"], site) for rule in doc["spec"]["rules"]}
        elif doc.get("kind") == "HelmRelease":
            release = doc["spec"].get("releaseName") or doc["metadata"]["name"]
            found |= {substitute(str(host), site).replace("{{ .Release.Name }}", release)
                      for host in chart_hosts(doc["spec"].get("values") or {})}
    return found


def block(text: str, name: str) -> str:
    """The body of a local of the file, from its line to its closing line
    (two spaces in, as terraform fmt writes them)."""
    match = re.search(rf"^  {name} = (.*?)^  \}}\)?$", text, re.MULTILINE | re.DOTALL)
    assert match, f"no {name} in {DNS.name}"
    return match[1]


def record_names() -> set[str]:
    """The names of local.site_dns_records: the fixed records, and one for
    each app of the generated map (its ${app}.internal.tnwks.us)."""
    text = DNS.read_text()
    fixed = re.findall(r'^\s+name\s+= "([^"]+)"', block(text, "site_fixed_dns_records"), re.MULTILINE)
    generated = block(text, "site_dns_records")
    apps = re.findall(r"^\s+([a-z0-9-]+)\s+= ", block(text, "wsl_app_ingress_vips"), re.MULTILINE)
    head, loop, _ = generated.partition("}) :")
    assert loop, "the generated map has changed its form"
    apps += re.findall(r"^\s+([a-z0-9-]+)\s+= local\.", head, re.MULTILINE)
    assert 'name            = "${app}.internal.tnwks.us"' in generated
    return set(fixed) | {f"{app}.internal.tnwks.us" for app in apps}


def test_the_walk_finds_the_internal_hosts_of_site_1():
    hosts = internal_hosts()
    # Hosts of three forms: a manifest, app-template with a Flux variable,
    # and app-template with the release name.
    assert {"openclaw.internal.tnwks.us", "grafana.internal.tnwks.us", "frigate.internal.tnwks.us"} <= hosts
    assert all(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host) for host in hosts), hosts


def test_the_names_of_the_records_are_those_of_the_file():
    """A fixed record, a generated one of the VIP map and one of the merge.
    The parse gave the list of terraform console for local.site_dns_records
    (the way infrastructure/mikrotik/tools/tests/test_dns_records.py reads
    it) when it was written; each record name is in the file once."""
    names = record_names()
    assert {"grafana.internal.tnwks.us", "oauth2.internal.tnwks.us", "radarr.internal.tnwks.us"} <= names
    text = DNS.read_text()
    fixed = re.findall(r"^    (\w+) = \{$", block(text, "site_fixed_dns_records"), re.MULTILINE)
    assert len(names) == len(fixed) + len(re.findall(r"^    [a-z0-9-]+ +=", block(text, "wsl_app_ingress_vips"),
                                                     re.MULTILINE)) + 3


def test_each_internal_host_of_site_1_has_a_record():
    missing = internal_hosts() - record_names()
    assert missing == WILDCARD_ONLY, (
        f"no record in {DNS.relative_to(ROOT)} for {sorted(missing - WILDCARD_ONLY)}; "
        f"a record now for {sorted(WILDCARD_ONLY - missing)}: take it off WILDCARD_ONLY")


def test_the_control_ui_of_main_has_the_record_of_the_shared_vip_of_site_1():
    ingress = yaml.safe_load((ROOT / "kubernetes/apps/ai/agents/main/agent/app/ingress.yaml").read_text())
    (rule,) = ingress["spec"]["rules"]
    host = substitute(rule["host"])
    match = re.search(r"^\s*vip_openclaw = \{(.*?)^\s*\}", DNS.read_text(), re.MULTILINE | re.DOTALL)
    assert match, "no record vip_openclaw"
    record = dict(re.findall(r'^\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', match[1], re.MULTILINE))
    assert record == {
        "name": host,
        "address": "local.vip_wsl",
        "type": "A",
        "ttl": "5m",
        "match_subdomain": "true",
        "comment": "shared VIP (homelab-wsl first): main agent Control UI",
    }
    assert DNS.read_text().count(f'"{host}"') == 1


def test_each_name_on_a_shared_vip_is_probed_at_the_sites_that_serve_it():
    """The collector of each site probes the names of the shared VIPs as a
    client of that site, which reaches the ingress of its own site
    (docs/wsl/multi-site.md). So a name is probed at the sites whose apps
    have an internal Ingress of that host (INGRESS_CLUSTERS in
    observability/inventory.py), or the other site answers 404."""
    hosts = {cluster: internal_hosts(site) for cluster, site in SITES.items()}
    names = json.loads(INVENTORY.read_text())["ingress"]
    assert "openclaw.internal.tnwks.us" in {name["host"] for name in names}
    for name in names:
        probed = name.get("clusters", list(SITES))
        assert probed == [cluster for cluster in SITES if name["host"] in hosts[cluster]], name
