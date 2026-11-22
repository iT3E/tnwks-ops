"""The Wazuh server (kubernetes/apps/security/wazuh) and the two steps of its
settings (kubernetes/apps/security/wazuh-settings): the files of this repository.

No cluster lists these apps yet, so the render of CI does not build them. These
tests read the files: the tag and the files of the vendor that stay, the images
by digest, the node selector and the memory lines against the tables of
kubernetes/apps/security/README.md, nothing privileged, no route to any address,
no Secret in git, and the configuration of the manager.

test_the_render_of_the_overlay builds the overlay with kustomize against a local
copy of wazuh/wazuh-kubernetes at the tag, as Flux builds it, when
WAZUH_KUBERNETES_DIR names that copy and kustomize is installed; else it skips.
"""

import os
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
TAG = "v5.0.0-rc2"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# The files of wazuh/ at the tag that the GitRepository keeps.
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
# The workloads of the settings, in ../wazuh-settings/app.
SETTINGS_WORKLOADS = ("accounts-job.yaml", "settings-job.yaml", "settings-cronjob.yaml")


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
        (workload,) = docs(SETTINGS / "app" / name)
        spec = workload["spec"]
        found[name] = (spec["jobTemplate"]["spec"] if "jobTemplate" in spec else spec)["template"]["spec"]
    return found


def our_files() -> list[Path]:
    return sorted(p for d in (APP, SETTINGS) for p in d.rglob("*") if p.suffix in (".yaml", ".json"))


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_the_source_is_the_vendor_repository_at_the_tag():
    (repo,) = docs(APP / "app/gitrepository.yaml")
    assert repo["spec"]["url"] == "https://github.com/wazuh/wazuh-kubernetes"
    assert repo["spec"]["ref"] == {"tag": TAG}
    lines = [x.strip() for x in repo["spec"]["ignore"].splitlines() if x.strip() and not x.strip().startswith("#")]
    assert lines[:2] == ["/*", "!/wazuh/"]
    assert set(lines[2:]) == {
        "/wazuh/kustomization.yml",
        "/wazuh/base/",
        "/wazuh/wazuh_managers/wazuh-worker-sts.yaml",
        "/wazuh/wazuh_managers/wazuh-events-svc.yaml",
        "/wazuh/wazuh_managers/network-policies/wazuh-worker-egress.yaml",
        "/wazuh/wazuh_managers/wazuh-registration-svc.yaml",
        "/wazuh/wazuh_managers/network-policies/manager-egress-external.yaml",
        "/wazuh/wazuh_managers/wazuh-agents-svc.yaml",
    }


def test_the_server_builds_the_source_with_no_substitution_of_its_own():
    spec = server()["spec"]
    assert server()["metadata"]["labels"] == {"substitution.flux.home.arpa/disabled": "true"}
    assert spec["sourceRef"] == {"kind": "GitRepository", "name": "wazuh-kubernetes", "namespace": "security"}
    assert (spec["path"], spec["targetNamespace"]) == ("./wazuh", "security")
    assert spec["dependsOn"] == [{"name": "security-wazuh"}]
    assert "postBuild" not in spec
    app = kustomizations()["security-wazuh"]["spec"]
    assert app["path"] == "./kubernetes/apps/security/wazuh/app"
    assert app["dependsOn"] == [{"name": "cert-manager"}]


def test_no_cluster_lists_the_apps_yet():
    for listing in (ROOT / "kubernetes/clusters").glob("*/apps/kustomization.yaml"):
        text = listing.read_text()
        assert "security/wazuh" not in text, listing


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


def test_the_heap_is_half_of_the_indexer_line():
    (indexer,) = patches()[("StatefulSet", "wazuh-indexer")]["spec"]["template"]["spec"]["containers"]
    opts = {e["name"]: e.get("value") for e in indexer["env"]}["OPENSEARCH_JAVA_OPTS"]
    assert "-Xms1536m" in opts.split() and "-Xmx1536m" in opts.split()
    assert 1536 * 2 == mib(indexer["resources"]["limits"]["memory"])
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


def test_no_secret_is_in_git():
    for path in our_files():
        assert not path.name.endswith(".sops.yaml"), path
        if path.suffix == ".yaml":
            assert "Secret" not in {d.get("kind") for d in docs(path)}, path
    for kustomization in (APP / "app/kustomization.yaml", SETTINGS / "app/kustomization.yaml"):
        body = yaml.safe_load(kustomization.read_text())
        assert "secretGenerator" not in body
        assert not [r for r in body["resources"] if "secret" in r]


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


def render(upstream: Path, out: Path) -> tuple[list[str], list[dict]]:
    """Build the server as Flux does: the kept files, the images and the patches,
    with the values of the cluster-settings that the parent substitutes."""
    values = {"SECURITY_NODE": "node", "STORAGE_CLASS_DEFAULT": "local-path"}
    text = re.sub(r"\$\{([A-Z_]+)\}", lambda m: values[m.group(1)], (APP / "ks.yaml").read_text())
    (spec,) = [d["spec"] for d in yaml.safe_load_all(text) if d and d["metadata"]["name"] == "security-wazuh-server"]
    present = sorted(str(p.relative_to(upstream)) for p in (upstream / "wazuh").rglob("*.yaml")
                     if not any(str(p.relative_to(upstream)).startswith(x) for x in ("wazuh/base/",)))
    for rel in KEPT:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(upstream / rel, out / rel)
    body = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "namespace": spec["targetNamespace"], "resources": KEPT, "images": spec["images"],
            "patches": spec["patches"]}
    (out / "kustomization.yaml").write_text(yaml.safe_dump(body))
    result = subprocess.run(["kustomize", "build", str(out)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return present, [d for d in yaml.safe_load_all(result.stdout) if d]


@pytest.mark.skipif(not os.environ.get("WAZUH_KUBERNETES_DIR") or not shutil.which("kustomize"),
                    reason="needs WAZUH_KUBERNETES_DIR (wazuh-kubernetes at the tag) and kustomize")
def test_the_render_of_the_overlay(tmp_path):
    upstream = Path(os.environ["WAZUH_KUBERNETES_DIR"])
    present, rendered = render(upstream, tmp_path)
    ignored = {"wazuh/wazuh_managers/wazuh-worker-sts.yaml", "wazuh/wazuh_managers/wazuh-events-svc.yaml",
               "wazuh/wazuh_managers/network-policies/wazuh-worker-egress.yaml",
               "wazuh/wazuh_managers/wazuh-registration-svc.yaml",
               "wazuh/wazuh_managers/network-policies/manager-egress-external.yaml",
               "wazuh/wazuh_managers/wazuh-agents-svc.yaml"}
    assert sorted(set(present) - ignored) == KEPT
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
