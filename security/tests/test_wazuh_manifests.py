"""The Wazuh server (kubernetes/apps/security/wazuh) and the two steps of its
settings (kubernetes/apps/security/wazuh-settings): the files of this repository.

homelab-wsl-02 lists the server and its settings, and no other cluster. These
tests read the files: the copy of the vendor's manifests against its sums, the
files of the vendor that stay, the images by digest, the node selector and the memory lines against the tables of
kubernetes/apps/security/README.md, nothing privileged, no route to any address,
no Secret in git, and the configuration of the manager.

test_the_render_of_the_overlay builds server/ with the images and the patches
of ks.yaml, as Flux builds it, when kustomize is installed; else it skips.
"""

import hashlib
import ipaddress
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "kubernetes/apps/security"
APP = SECURITY / "wazuh"
SETTINGS = SECURITY / "wazuh-settings"
SERVER = APP / "server"
# The vendor's manifests in server/upstream (server/UPSTREAM.md): the tag, its
# commit, and the files.
TAG = "v5.0.0-rc2"
COMMIT = "ceacd20b73fbe5252cc0af1a5429916c4e980fa9"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# The files of wazuh/ at the tag that server/upstream keeps.
KEPT = [
    "wazuh/indexer_stack/wazuh-dashboard/dashboard-deploy.yaml",
    "wazuh/indexer_stack/wazuh-dashboard/dashboard-egress.yaml",
    "wazuh/indexer_stack/wazuh-dashboard/dashboard-pvc.yaml",
    "wazuh/indexer_stack/wazuh-dashboard/dashboard-svc.yaml",
    "wazuh/indexer_stack/wazuh-indexer/cluster/indexer-sts.yaml",
    "wazuh/indexer_stack/wazuh-indexer/indexer-egress.yaml",
    "wazuh/indexer_stack/wazuh-indexer/indexer-ingress.yaml",
    "wazuh/indexer_stack/wazuh-indexer/indexer-svc.yaml",
    "wazuh/wazuh_managers/network-policies/manager-egress.yaml",
    "wazuh/wazuh_managers/network-policies/wazuh-master-ingress.yaml",
    "wazuh/wazuh_managers/wazuh-api-svc.yaml",
    "wazuh/wazuh_managers/wazuh-cluster-svc.yaml",
    "wazuh/wazuh_managers/wazuh-master-sts.yaml",
]
# The row of the memory table of ../README.md for each container.
MEMORY_ROWS = {
    "wazuh-indexer": "Wazuh indexer (step 2)",
    "wazuh-manager": "Wazuh manager (step 2)",
    "wazuh-dashboard": "Wazuh dashboard (step 2)",
    "wazuh-accounts": "Wazuh account job (step 2)",
    "wazuh-settings": "Wazuh settings job (step 2)",
}
# The workloads of the settings: step 1 in ../wazuh-settings/accounts, step 2
# in ../wazuh-settings/app.
SETTINGS_WORKLOADS = ("accounts/accounts-job.yaml", "app/settings-job.yaml", "app/settings-cronjob.yaml")


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kustomizations() -> dict:
    return {d["metadata"]["name"]: d for d in docs(APP / "ks.yaml")}


def server() -> dict:
    return kustomizations()["security-wazuh-server"]


def patches() -> dict:
    """The strategic merge patches of the server, by the kind and name of their target."""
    found = {}
    for p in server()["spec"]["patches"]:
        body = yaml.safe_load(p["patch"])
        found[(p["target"]["kind"], p["target"]["name"])] = body
    return found


def mib(value: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?) ?(Gi|GiB|Mi|MiB)", value.strip())
    assert m, value
    return int(float(m.group(1)) * (1024 if m.group(2).startswith("G") else 1))


def memory_table() -> dict:
    rows = {}
    for line in (SECURITY / "README.md").read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] in MEMORY_ROWS.values():
            rows[cells[0]] = mib(re.match(r"\d+(?:\.\d+)? (?:GiB|MiB)", cells[2]).group(0))
    return rows


def settings_pods() -> dict:
    """The pod spec of each workload of the settings, by file name."""
    found = {}
    for name in SETTINGS_WORKLOADS:
        (workload,) = docs(SETTINGS / name)
        spec = workload["spec"]
        found[name.split("/")[1]] = (spec["jobTemplate"]["spec"] if "jobTemplate" in spec else spec)["template"]["spec"]
    return found


def our_files() -> list[Path]:
    """The files of this repository, not the vendor's copy (the render test reads
    that, as Flux builds it)."""
    return sorted(p for d in (APP, SETTINGS) for p in d.rglob("*")
                  if p.suffix in (".yaml", ".json") and SERVER / "upstream" not in p.parents)


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_the_copy_of_the_vendor_is_the_one_of_its_sums():
    """Each file of server/upstream has the sha256 of upstream.sha256, which
    server/UPSTREAM.md checks against a clone of the vendor at COMMIT."""
    sums = {}
    for line in (SERVER / "upstream.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    files = sorted(str(p.relative_to(SERVER / "upstream")) for p in (SERVER / "upstream").rglob("*") if p.is_file())
    assert files == sorted(sums) == sorted(["LICENSE", *KEPT])
    for name, digest in sums.items():
        assert hashlib.sha256((SERVER / "upstream" / name).read_bytes()).hexdigest() == digest, name
    note = (SERVER / "UPSTREAM.md").read_text()
    assert f"`{TAG}`" in note and f"`{COMMIT}`" in note
    assert [line[3:-1] for line in note.splitlines() if line.startswith("- `")] == ["LICENSE", *KEPT]


def test_the_server_builds_the_copy_of_the_vendor():
    body = yaml.safe_load((SERVER / "kustomization.yaml").read_text())
    assert body["resources"] == [f"./upstream/{rel}" for rel in KEPT]
    assert set(body) == {"apiVersion", "kind", "resources"}
    # No other source: Flux builds each Kustomization of the app from this
    # repository.
    assert not [p for p in APP.rglob("*.yaml") if any(d.get("kind") == "GitRepository" for d in docs(p))
                and SERVER / "upstream" not in p.parents]


def test_the_server_builds_the_source_with_no_substitution_of_its_own():
    spec = server()["spec"]
    assert server()["metadata"]["labels"] == {"substitution.flux.home.arpa/disabled": "true"}
    assert spec["sourceRef"] == {"kind": "GitRepository", "name": "flux-system"}
    assert (spec["path"], spec["targetNamespace"]) == ("./kubernetes/apps/security/wazuh/server", "security")
    assert spec["dependsOn"] == [{"name": "security-wazuh"}]
    assert "postBuild" not in spec
    app = kustomizations()["security-wazuh"]["spec"]
    assert app["path"] == "./kubernetes/apps/security/wazuh/app"
    assert app["dependsOn"] == [{"name": "cert-manager"}, {"name": "cilium-bgp"}]


def test_only_homelab_wsl_02_lists_the_server_and_its_settings():
    for listing in (ROOT / "kubernetes/clusters").glob("*/apps/kustomization.yaml"):
        listed = [r for r in yaml.safe_load(listing.read_text())["resources"] if "security/wazuh" in r]
        site = listing.parent.parent.name
        both = ["../../../apps/security/wazuh/ks.yaml", "../../../apps/security/wazuh-settings/ks.yaml"]
        assert listed == (both if site == "wsl-02" else []), listing


def test_the_site_record_of_the_agents_is_that_of_the_cluster_settings():
    """The router answers the name of the agents with their VIP
    (dns-sites.tf, as the other site records): match_subdomain, so it sits
    above the internal.tnwks.us wildcard (modules/mikrotik/dns.tf)."""
    settings = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/cluster-settings.yaml").read_text())["data"]
    text = (ROOT / "infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf").read_text()
    block = re.search(r"^\s*vip_wazuh_agents = \{(.*?)^\s*\}", text, re.MULTILINE | re.DOTALL)
    assert block, "no record vip_wazuh_agents"
    record = dict(re.findall(r'^\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', block[1], re.MULTILINE))
    assert record == {
        "name": settings["WAZUH_AGENTS_HOST"],
        "address": settings["WAZUH_AGENTS_LB_ADDR"],
        "type": "A",
        "ttl": "5m",
        "match_subdomain": "true",
        "comment": "shared VIP (homelab-wsl-02 only): wazuh agents, 1517",
    }
    assert text.count(settings["WAZUH_AGENTS_HOST"]) == 1


def test_the_agents_reach_a_shared_vip_of_site_2():
    """The Service of the agents is in the shared VIP pool and the BGP
    advertisement (docs/wsl/multi-site.md, "Shared VIPs"), on a free address
    of the half that the router prefers on site 2."""
    settings = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/cluster-settings.yaml").read_text())["data"]
    assert settings["WAZUH_AGENTS_LB_ADDR"] == "10.10.95.201"
    assert settings["WAZUH_AGENTS_HOST"] == "wazuh-agents.internal.tnwks.us"
    assert ipaddress.ip_address(settings["WAZUH_AGENTS_LB_ADDR"]) in ipaddress.ip_network("10.10.95.128/25")
    assert ipaddress.ip_address(settings["WAZUH_AGENTS_LB_ADDR"]) in ipaddress.ip_network(settings["SHARED_VIP_POOL"])
    (service,) = docs(APP / "app/service-agents.yaml")
    label = {"tnwks.us/shared-vip": "true"}
    assert label.items() <= service["metadata"]["labels"].items()
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["spec"]["loadBalancerIP"] == "${WAZUH_AGENTS_LB_ADDR}"
    assert service["spec"]["externalTrafficPolicy"] == "Cluster"
    bgp = ROOT / "kubernetes/apps/kube-system/cilium/bgp"
    (pool,) = [d for d in docs(bgp / "shared-vip-pool.yaml") if d["kind"] == "CiliumLoadBalancerIPPool"]
    assert pool["spec"]["serviceSelector"]["matchLabels"] == label
    adverts = [a for d in docs(bgp / "bgp.yaml") if d["kind"] == "CiliumBGPAdvertisement"
               for a in d["spec"]["advertisements"] if a.get("advertisementType") == "Service"]
    assert any("LoadBalancerIP" in a["service"]["addresses"] and a["selector"]["matchLabels"] == label
               for a in adverts)
    # No other Service of git asks for the address.
    others = [p for p in (ROOT / "kubernetes").rglob("*.yaml") if ".sops." not in p.name
              and settings["WAZUH_AGENTS_LB_ADDR"] in p.read_text()]
    assert others == [ROOT / "kubernetes/clusters/wsl-02/cluster-settings.yaml"]


def test_each_image_is_pinned_by_digest():
    images = {i["name"]: i for i in server()["spec"]["images"]}
    assert set(images) == {"wazuh/wazuh-manager", "wazuh/wazuh-indexer", "wazuh/wazuh-dashboard"}
    for name, image in images.items():
        assert image["newName"] == f"docker.io/{name}"
        assert DIGEST.fullmatch(image["digest"]), name
        assert "newTag" not in image
    # The busybox of the vendor's init container takes the indexer image.
    (hack,) = [c for c in patches()[("StatefulSet", "wazuh-indexer")]["spec"]["template"]["spec"]["initContainers"]
               if c["name"] == "volume-mount-hack"]
    assert hack["image"] == "wazuh/wazuh-indexer:5.0.0-rc2"
    for name, spec in settings_pods().items():
        (job,) = spec["containers"]
        assert DIGEST.search(job["image"]), name


def test_each_workload_has_the_node_selector():
    for (kind, name), body in patches().items():
        if kind in ("StatefulSet", "Deployment"):
            assert body["spec"]["template"]["spec"]["nodeSelector"] == {
                "kubernetes.io/hostname": "${SECURITY_NODE}"}, name
    for name, spec in settings_pods().items():
        assert spec["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}, name


def test_the_memory_lines_are_those_of_the_readme():
    table = memory_table()
    assert set(table) == set(MEMORY_ROWS.values())
    found = {}
    for (kind, _), body in patches().items():
        if kind in ("StatefulSet", "Deployment"):
            for c in body["spec"]["template"]["spec"]["containers"]:
                found[c["name"]] = c["resources"]
    for spec in settings_pods().values():
        for c in spec["containers"]:
            found[c["name"]] = c["resources"]
    assert set(found) == set(MEMORY_ROWS)
    for container, row in MEMORY_ROWS.items():
        limits = found[container]["limits"]
        assert mib(limits["memory"]) == table[row], container
        assert limits.get("cpu") is None, container
        assert found[container]["requests"]["memory"], container
    for container in ("wazuh-indexer", "wazuh-manager", "wazuh-dashboard"):
        assert found[container]["requests"]["memory"] == found[container]["limits"]["memory"]


def test_the_memory_lines_of_this_readme_are_those_of_the_manifests():
    """The table of wazuh/README.md, "Memory": the request and the limit of
    each container, as the patches and the settings set them."""
    rows = {}
    for line in (APP / "README.md").read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.match(r"`([a-z-]+)`", cells[0]) if len(cells) == 3 else None
        if name and name.group(1) in MEMORY_ROWS:
            unit = r"\d+ (?:GiB|MiB)"
            rows[name.group(1)] = (mib(re.match(unit, cells[1]).group(0)), mib(re.match(unit, cells[2]).group(0)))
    found = {}
    for (kind, _), body in patches().items():
        if kind in ("StatefulSet", "Deployment"):
            for c in body["spec"]["template"]["spec"]["containers"]:
                found[c["name"]] = c["resources"]
    for spec in settings_pods().values():
        for c in spec["containers"]:
            found[c["name"]] = c["resources"]
    assert set(rows) == set(MEMORY_ROWS)
    for name, (request, limit) in rows.items():
        assert (mib(found[name]["requests"]["memory"]), mib(found[name]["limits"]["memory"])) == (request, limit), name


def test_the_heap_is_under_half_of_the_indexer_line():
    """OpenSearch recommends a heap of half of the memory of the system; the
    engine and the page cache use the rest (wazuh/README.md, "Memory")."""
    (indexer,) = patches()[("StatefulSet", "wazuh-indexer")]["spec"]["template"]["spec"]["containers"]
    opts = {e["name"]: e.get("value") for e in indexer["env"]}["OPENSEARCH_JAVA_OPTS"]
    assert "-Xms1536m" in opts.split() and "-Xmx1536m" in opts.split()
    assert mib(indexer["resources"]["limits"]["memory"]) == 4096
    assert 1536 * 2 <= mib(indexer["resources"]["limits"]["memory"])
    assert "heap of 1.5 GiB" in (SECURITY / "README.md").read_text()


def test_the_indexer_runs_one_node_with_no_privileged_init():
    spec = patches()[("StatefulSet", "wazuh-indexer")]["spec"]
    assert spec["replicas"] == 1
    inits = {c["name"]: c for c in spec["template"]["spec"]["initContainers"]}
    assert inits["increase-the-vm-max-map-count"] == {"name": "increase-the-vm-max-map-count", "$patch": "delete"}
    env = {e["name"]: e for e in spec["template"]["spec"]["containers"][0]["env"]}
    for name in ("node.max_local_storage_nodes", "NODES_DN"):
        assert env[name].get("$patch") == "delete", name
    # The opensearch.yml of the image names node-1 as the initial cluster
    # manager and sets no discovery type: the vendor's variable of the initial
    # cluster manager (wazuh-indexer-0) stays, and the seed is this one pod, by
    # a name of its certificate.
    assert "cluster.initial_cluster_manager_nodes" not in env
    assert "discovery.type" not in env
    assert env["discovery.seed_hosts"]["value"] == "wazuh-indexer-0.wazuh-indexer"
    certs = list(yaml.safe_load_all((APP / "app/certificates.yaml").read_text()))
    (node,) = [d for d in certs if d and d["metadata"]["name"] == "wazuh-indexer-node"]
    assert "wazuh-indexer-0.wazuh-indexer" in node["spec"]["dnsNames"]
    assert env["plugins.content_manager.telemetry.enabled"]["value"] == "false"
    assert env["node.store.allow_mmap"]["value"] == "false"


def test_nothing_is_privileged():
    for path in our_files():
        text = path.read_text()
        for node in walk(list(yaml.safe_load_all(text)) if path.suffix == ".yaml" else []):
            if isinstance(node, dict):
                assert node.get("privileged") is not True, path
                assert not node.get("hostNetwork") and not node.get("hostPID") and not node.get("hostIPC"), path
                assert "hostPath" not in node, path
                assert not (node.get("capabilities") or {}).get("add"), path
    for p in server()["spec"]["patches"]:
        assert "privileged" not in p["patch"]


def test_no_rule_opens_a_route_to_any_address():
    for path in our_files():
        if path.suffix != ".yaml":
            continue
        text = path.read_text()
        assert "0.0.0.0/0" not in text and "::/0" not in text, path
        for d in docs(path):
            if d.get("kind") != "CiliumNetworkPolicy":
                continue
            for rule in d["spec"].get("egress") or []:
                assert not set(rule) & {"toCIDR", "toCIDRSet", "toEntities"}, (path, d["metadata"]["name"])
                for fqdn in rule.get("toFQDNs") or []:
                    assert set(fqdn) == {"matchName"}, (path, fqdn)
            for rule in d["spec"].get("ingress") or []:
                if "fromEntities" in rule:
                    # Only the agents, on their one port, to the manager.
                    assert d["metadata"]["name"] == "wazuh-agents"
                    assert d["spec"]["endpointSelector"]["matchLabels"] == {
                        "app": "wazuh-manager", "node-type": "master"}
                    assert rule["toPorts"] == [{"ports": [{"port": "1517", "protocol": "TCP"}]}]


def test_the_feed_is_the_one_host_of_the_indexer():
    by_name = {d["metadata"]["name"]: d for d in docs(APP / "app/networkpolicy.yaml")}
    (rule,) = by_name["wazuh-indexer-feed"]["spec"]["egress"]
    assert rule["toFQDNs"] == [{"matchName": "api.pre.cloud.wazuh.com"}]
    assert rule["toPorts"] == [{"ports": [{"port": "443", "protocol": "TCP"}]}]
    assert by_name["wazuh-indexer-feed"]["spec"]["endpointSelector"] == {"matchLabels": {"app": "wazuh-indexer"}}


def test_step2_runs_after_step1_completes():
    """Step 1 is a Flux Kustomization of its own with wait: true, and the one of
    step 2 depends on it, so Flux applies step 2 after the Job of step 1
    completes."""
    by_name = {d["metadata"]["name"]: d["spec"] for d in docs(SETTINGS / "ks.yaml")}
    assert sorted(by_name) == ["security-wazuh-accounts", "security-wazuh-settings"]
    accounts, settings = by_name["security-wazuh-accounts"], by_name["security-wazuh-settings"]
    assert accounts["path"] == "./kubernetes/apps/security/wazuh-settings/accounts"
    assert accounts["wait"] is True and accounts["prune"] is True
    assert {d["name"] for d in accounts["dependsOn"]} == {"security-wazuh-server"}
    assert settings["path"] == "./kubernetes/apps/security/wazuh-settings/app"
    assert {d["name"] for d in settings["dependsOn"]} == {
        "security-wazuh-server", "security-converter", "security-wazuh-accounts", "alertmanager"}
    (job,) = docs(SETTINGS / "accounts/accounts-job.yaml")
    # The timeout of the health check is more than the deadline of the Job.
    seconds = int(accounts["timeout"].rstrip("m")) * 60
    assert seconds > job["spec"]["activeDeadlineSeconds"]
    step1 = yaml.safe_load((SETTINGS / "accounts/kustomization.yaml").read_text())
    step2 = yaml.safe_load((SETTINGS / "app/kustomization.yaml").read_text())
    # The Secrets: test_no_secret_is_in_git.
    assert [r for r in step1["resources"] if "sops" not in r] == ["./networkpolicy.yaml", "./accounts-job.yaml"]
    assert [r for r in step2["resources"] if "sops" not in r] == ["./settings-job.yaml", "./settings-cronjob.yaml"]
    assert [g["name"] for g in step1["configMapGenerator"]] == ["wazuh-accounts-files"]
    assert [g["name"] for g in step2["configMapGenerator"]] == ["wazuh-settings-files"]


def sops_header(path: Path) -> dict:
    """The plain fields of a SOPS file of a Secret: the lines before its data,
    which SOPS leaves as they are (encrypted_regex of .sops.yaml)."""
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith(("stringData:", "data:")):
            break
        lines.append(line)
    return yaml.safe_load("\n".join(lines))


def test_no_secret_is_in_git():
    """The Secrets are SOPS files: the server lists its four, step 1 its two
    and step 2 its one. No plain file holds a Secret."""
    for path in our_files():
        if path.suffix == ".yaml" and not path.name.endswith(".sops.yaml"):
            assert "Secret" not in {d.get("kind") for d in docs(path)}, path
    listed = {
        APP / "app/kustomization.yaml": [
            "./secret-indexer-credentials.sops.yaml",
            "./secret-manager-credentials.sops.yaml",
            "./secret-dashboard-credentials.sops.yaml",
            "./secret-cluster-key.sops.yaml",
        ],
        SETTINGS / "app/kustomization.yaml": ["./secret-settings-user.sops.yaml"],
        SETTINGS / "accounts/kustomization.yaml": [
            "./secret-accounts-user.sops.yaml",
            "./secret-accounts-hashes.sops.yaml",
        ],
    }
    for kustomization, names in listed.items():
        body = yaml.safe_load(kustomization.read_text())
        assert "secretGenerator" not in body
        assert [r for r in body["resources"] if "secret" in r] == names, kustomization
        assert not [p for p in kustomization.parent.glob("*.sops.yaml") if f"./{p.name}" not in names]
        for name in names:
            header = sops_header(kustomization.parent / name)
            assert header["kind"] == "Secret" and header["metadata"]["namespace"] == "security", name


def test_the_reader_secret_of_the_converter_waits_for_its_workstream():
    """The password of tnwks-converter, made with its hash in
    wazuh-accounts-hashes, is a SOPS file of the converter that no
    kustomization lists yet: the change of the converter that reads the
    indexer lists it (README.md, "Contract")."""
    converter = SECURITY / "converter/app"
    assert sorted(p.name for p in converter.glob("*.sops.yaml")) == ["secret-wazuh-converter-reader.sops.yaml"]
    header = sops_header(converter / "secret-wazuh-converter-reader.sops.yaml")
    assert (header["kind"], header["metadata"]["name"], header["metadata"]["namespace"]) == (
        "Secret", "wazuh-converter-reader", "security")
    resources = yaml.safe_load((converter / "kustomization.yaml").read_text())["resources"]
    assert not [r for r in resources if "sops" in r]


def test_flux_substitutes_nothing_in_the_hashes():
    """The parent of the apps of homelab-wsl-02 gives the two Kustomizations of
    the settings decryption and substitution (no label turns it off), and
    kustomize-controller substitutes a decrypted Secret too. A bcrypt hash
    holds no "${" and no "$$", the only forms that the envsubst of Flux reads,
    and the hashes Secret turns substitution off in its plain metadata, which
    the controller reads for each object."""
    off = "substitution.flux.home.arpa/disabled"
    settings = {d["metadata"]["name"]: d for d in docs(SETTINGS / "ks.yaml")}
    for name in ("security-wazuh-accounts", "security-wazuh-settings"):
        assert off not in (settings[name]["metadata"].get("labels") or {}), name
    header = sops_header(SETTINGS / "accounts/secret-accounts-hashes.sops.yaml")
    assert header["metadata"]["name"] == "wazuh-accounts-hashes"
    assert header["metadata"]["annotations"] == {"kustomize.toolkit.fluxcd.io/substitute": "disabled"}
    # The form that step 1 accepts (scripts/accounts.py, BCRYPT).
    text = (SETTINGS / "app/scripts/accounts.py").read_text()
    assert 'BCRYPT = re.compile(r"\\$2[aby]\\$\\d{2}\\$[./A-Za-z0-9]{53}")' in text
    sample = "$2y$12$" + ("./AZaz09" * 7)[:53]
    assert re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", sample)
    assert "${" not in sample and "$$" not in sample


def test_the_jobs_mount_the_certificate_of_the_ca_and_not_its_key():
    for name, spec in settings_pods().items():
        volumes = {v["name"]: v for v in spec["volumes"]}
        assert volumes["ca"]["secret"] == {"secretName": "wazuh-ca", "items": [{"key": "ca.crt", "path": "ca.crt"}]}, name


def test_the_manager_configuration():
    root = ET.parse(APP / "app/config/wazuh-manager.conf").getroot()
    assert root.tag == "wazuh_config"
    assert root.find("remote/legacy") is None
    assert root.findtext("remote/https/port") == "1517"
    assert root.findtext("auth/legacy_enrollment") == "no"
    assert root.findtext("auth/remote_enrollment") == "yes"
    assert root.findtext("auth/use_password") == "yes"
    assert root.findtext("vulnerability-detection/enabled") == "yes"
    assert root.findtext("task-manager/upgrade_enabled") == "no"
    # Not a valid key (^[A-Za-z0-9]{32}$): the image must write the one of the Secret.
    assert not re.fullmatch(r"[A-Za-z0-9]{32}", root.findtext("cluster/key"))
    certs = [e.text for e in root.iter() if e.text and e.text.strip().endswith(".pem")]
    assert certs and all(re.fullmatch(r"/etc/wazuh-manager/certs/[a-z-]+\.pem", c) for c in certs)
    # The manager patch mounts the file where the image reads it.
    (manager,) = patches()[("StatefulSet", "wazuh-manager-master")]["spec"]["template"]["spec"]["containers"]
    (mount,) = manager["volumeMounts"]
    assert mount["mountPath"] == "/wazuh-config-mount/etc/wazuh-manager.conf"


def test_each_certificate_comes_from_the_ca_of_the_server():
    certs = [d for d in docs(APP / "app/certificates.yaml") if d["kind"] == "Certificate"]
    secrets = set()
    for cert in certs:
        spec = cert["spec"]
        assert spec["privateKey"]["encoding"] == "PKCS8", cert["metadata"]["name"]
        assert spec["subject"]["organizations"] == ["tnwks"]
        issuer = "wazuh-selfsigned" if spec.get("isCA") else "wazuh-ca"
        assert spec["issuerRef"] == {"name": issuer, "kind": "Issuer"}, cert["metadata"]["name"]
        secrets.add(spec["secretName"])
    projected = set()
    for (kind, _), body in patches().items():
        if kind in ("StatefulSet", "Deployment"):
            for volume in body["spec"]["template"]["spec"]["volumes"]:
                for source in (volume.get("projected") or {}).get("sources", []):
                    projected.add(source["secret"]["name"])
    assert projected == secrets - {"wazuh-ca"}
    (node,) = [c for c in certs if c["metadata"]["name"] == "wazuh-indexer-node"]
    # The published host name of the node, which the transport checks.
    assert "wazuh-indexer-0.wazuh-indexer.security.svc.cluster.local" in node["spec"]["dnsNames"]


def render(out: Path) -> list[dict]:
    """Build the server as Flux does: server/, with the target namespace, the
    images and the patches of ks.yaml, and the values of the cluster-settings
    that the parent substitutes in ks.yaml."""
    values = {"SECURITY_NODE": "node", "STORAGE_CLASS_DEFAULT": "local-path"}
    text = re.sub(r"\$\{([A-Z_]+)\}", lambda m: values[m.group(1)], (APP / "ks.yaml").read_text())
    (spec,) = [d["spec"] for d in yaml.safe_load_all(text) if d and d["metadata"]["name"] == "security-wazuh-server"]
    shutil.copytree(SERVER, out / "server")
    body = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "namespace": spec["targetNamespace"], "resources": ["./server"], "images": spec["images"],
            "patches": spec["patches"]}
    (out / "kustomization.yaml").write_text(yaml.safe_dump(body))
    result = subprocess.run(["kustomize", "build", str(out)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@pytest.mark.skipif(not shutil.which("kustomize"), reason="needs kustomize")
def test_the_render_of_the_overlay(tmp_path):
    rendered = render(tmp_path)
    assert len(rendered) == len(KEPT)
    text = yaml.safe_dump_all(rendered)
    assert "busybox" not in text and "privileged:" not in text and "0.0.0.0/0" not in text
    for d in rendered:
        assert d["metadata"]["namespace"] == "security"
        if d["kind"] in ("StatefulSet", "Deployment"):
            pod = d["spec"]["template"]["spec"]
            assert pod["nodeSelector"] == {"kubernetes.io/hostname": "node"}
            for c in pod["containers"] + pod.get("initContainers", []):
                assert DIGEST.search(c["image"]), c["name"]
                assert c["resources"]["limits"]["memory"], c["name"]
            for v in pod["volumes"]:
                assert "secret" not in v or v["name"] == "wazuh-credentials-secret" or "credentials" in v["name"]
