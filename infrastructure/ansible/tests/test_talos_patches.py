"""The Talos patches that 01-talos-bootstrap.yml gives a new cluster.

Each YAML file under talos/ parses. Each directory under talos/ is a cluster
of talos_clusters (inventory/group_vars/all/talos.yml). Each file that a
patch list names is there, and each of its documents is a mapping. Each YAML
file of a cluster with patch lists is in one of its lists, in NOT_PATCHES or
in LIVE_ONLY. A cluster with no patch lists is in NO_LISTS, with the reason.

homelab-wsl has each setting twice: its lists in the multi-document form, for
a new cluster, and the v1alpha1 files that its live node needs (LIVE_ONLY).
TwoForms holds them equal, field by field (SAME_SETTING). The conversion of
the live node to the multi-document form ends the double set.

This is no validation of a machine config: that needs Talos and a full
config. Talos refuses a v1alpha1 field that a document of its version also
sets, and this test does not see that. talos/README.md says how a patch
reaches a running node.

    python3 -m unittest discover -s infrastructure/ansible/tests -p test_talos_patches.py -v
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
TALOS = ROOT / "talos"
INVENTORY = ROOT / "infrastructure/ansible/inventory/group_vars/all/talos.yml"
LISTS = ("config_patches", "controlplane_patches")

CLUSTERS = yaml.safe_load(INVENTORY.read_text())["talos_clusters"]

# Clusters whose files no patch list names, and why.
NO_LISTS = {
    # 01-talos-bootstrap.yml applies controlplane.yaml whole with
    # talosctl apply-config. It gives patches/ to nothing yet.
    "ms-01": "applied whole, no patch lists",
}

# YAML files of a cluster directory that are no patch.
NOT_PATCHES = {
    "versions.yaml": "the Talos and Kubernetes versions",
    "talosconfig.sops.yaml": "the encrypted client config",
}

# The v1alpha1 patches of a live node whose config is older than its lists.
# No list names them: Talos 1.14 refuses them in a new config. Their headers
# have the `talosctl patch mc` command for the live node.
LIVE_ONLY = {
    "wsl": [
        "cluster-config.yaml",
        "patches/kubelet-image-gc.yaml",
        "patches/talos-api-access.yaml",
        "patches/audit-policy.yaml",  # security/tests/test_audit_policy.py holds its rules
    ],
}

# homelab-wsl: each field of a LIVE_ONLY file and where its list file has it. Each entry is (live file, field path, list
# file, document, field path). The document is a kind, or "v1alpha1" for a
# key that Talos 1.14 still takes in the v1alpha1 document. A field path of
# None means the setting is the document deleted ($patch: delete) or present.
SAME_SETTING = [
    ("cluster-config.yaml", "cluster.network.podSubnets", "patches/cluster.yaml", "KubeNetworkConfig", "podSubnets"),
    ("cluster-config.yaml", "cluster.network.serviceSubnets", "patches/cluster.yaml", "KubeNetworkConfig", "serviceSubnets"),
    ("cluster-config.yaml", "cluster.network.cni.name", "patches/cluster.yaml", "KubeFlannelCNIConfig", None),
    ("cluster-config.yaml", "cluster.proxy.disabled", "patches/cluster.yaml", "KubeProxyConfig", None),
    ("cluster-config.yaml", "cluster.proxy.disabled", "patches/cluster.yaml", "v1alpha1", "cluster.proxy.disabled"),
    ("cluster-config.yaml", "cluster.discovery", "patches/cluster.yaml", "DiscoveryServiceConfig", None),
    ("cluster-config.yaml", "cluster.discovery", "patches/cluster.yaml", "v1alpha1", "cluster.discovery"),
    ("cluster-config.yaml", "machine.features.kubePrism.enabled", "patches/cluster.yaml", "KubePrismConfig", None),
    ("cluster-config.yaml", "machine.features.kubePrism.port", "patches/cluster.yaml", "KubePrismConfig", "port"),
    ("cluster-config.yaml", "cluster.controllerManager.extraArgs", "patches/controlplane.yaml",
     "KubeControllerManagerConfig", "extraArgs"),
    ("patches/kubelet-image-gc.yaml", "machine.kubelet.extraConfig", "patches/cluster.yaml", "KubeletConfig", "config"),
    ("patches/talos-api-access.yaml", "machine.features.kubernetesTalosAPIAccess", "patches/controlplane.yaml",
     "v1alpha1", "machine.features.kubernetesTalosAPIAccess"),
    # security/tests/test_audit_policy.py also checks the rules of each policy file.
    ("patches/audit-policy.yaml", "cluster.apiServer.auditPolicy", "patches/audit-policy-multidoc.yaml",
     "KubeAuditPolicyConfig", "configuration"),
]

# Fields that each document has, which hold no setting.
DOCUMENT_KEYS = {"apiVersion", "kind", "name", "$patch"}

# The value of a live field that a deleted or present document stands for.
DOCUMENT_MEANS = {
    "KubeFlannelCNIConfig": ("deleted", "none"),
    "KubeProxyConfig": ("deleted", True),
    "DiscoveryServiceConfig": ("deleted", None),  # the v1alpha1 key carries the value
    "KubePrismConfig": ("present", True),
}


def yaml_files(directory):
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in (".yaml", ".yml"))


def documents(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d is not None]


def get(data, path):
    for key in path.split("."):
        data = data[key]
    return data


def leaves(data, prefix=""):
    if isinstance(data, dict) and data:
        for key, value in data.items():
            yield from leaves(value, f"{prefix}.{key}" if prefix else key)
    elif prefix:
        yield prefix


def merged(path):
    """The v1alpha1 documents of a file as one mapping (their keys do not overlap here)."""
    out = {}
    for doc in documents(path):
        if "kind" not in doc:
            for key, value in doc.items():
                out.setdefault(key, {}).update(value)
    return out


def kinds(path):
    return {doc["kind"]: doc for doc in documents(path) if "kind" in doc}


class TalosPatches(unittest.TestCase):
    def test_each_talos_file_parses(self):
        files = yaml_files(TALOS)
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                documents(path)

    def test_each_talos_directory_is_a_cluster(self):
        for directory in sorted(p for p in TALOS.iterdir() if p.is_dir()):
            with self.subTest(directory=directory.name):
                self.assertIn(directory.name, CLUSTERS)

    def test_each_cluster_has_lists_or_a_reason(self):
        for target, cluster in CLUSTERS.items():
            if not (TALOS / target).is_dir():
                continue
            with self.subTest(target=target):
                has_lists = any(cluster.get(name) for name in LISTS)
                self.assertNotEqual(has_lists, target in NO_LISTS,
                                    f"{target}: patch lists or an entry in NO_LISTS, not both")

    def test_each_listed_patch_is_there_and_is_a_mapping(self):
        for target, cluster in CLUSTERS.items():
            for name in LISTS:
                for patch in cluster.get(name, []):
                    path = TALOS / target / patch
                    with self.subTest(target=target, patch=patch):
                        self.assertTrue(path.is_file(), f"{path.relative_to(ROOT)} is not there")
                        docs = documents(path)
                        self.assertTrue(docs, "no document")
                        for doc in docs:
                            self.assertIsInstance(doc, dict)

    def test_each_patch_file_is_in_a_list(self):
        for target, cluster in CLUSTERS.items():
            if target in NO_LISTS or not (TALOS / target).is_dir():
                continue
            listed = {p for name in LISTS for p in cluster.get(name, [])}
            for path in yaml_files(TALOS / target):
                patch = path.relative_to(TALOS / target).as_posix()
                if patch in NOT_PATCHES or patch in LIVE_ONLY.get(target, []):
                    continue
                with self.subTest(target=target, patch=patch):
                    self.assertIn(patch, listed, f"talos_clusters.{target} does not list {patch}")

    def test_each_audit_policy_is_for_the_control_plane(self):
        for target, cluster in CLUSTERS.items():
            policies = [p.relative_to(TALOS / target).as_posix()
                        for p in sorted((TALOS / target / "patches").glob("audit-policy*.yaml"))]
            listed = [p for p in policies if p not in LIVE_ONLY.get(target, [])]
            if not policies:
                continue
            with self.subTest(target=target):
                self.assertEqual(len(listed), 1, f"{target}: one audit policy for a new cluster")
                self.assertIn(listed[0], cluster.get("controlplane_patches", []))
                self.assertNotIn(listed[0], cluster.get("config_patches", []))

    def test_live_only_files_are_there_and_in_no_list(self):
        for target, files in LIVE_ONLY.items():
            listed = {p for name in LISTS for p in CLUSTERS[target].get(name, [])}
            for patch in files:
                with self.subTest(target=target, patch=patch):
                    self.assertTrue((TALOS / target / patch).is_file())
                    self.assertNotIn(patch, listed)


class TwoForms(unittest.TestCase):
    """homelab-wsl: the lists hold each setting of the live node's v1alpha1 files."""

    TARGET = TALOS / "wsl"

    def test_each_setting_is_the_same_in_both_forms(self):
        for live, live_path, new, document, new_path in SAME_SETTING:
            with self.subTest(live=f"{live}:{live_path}", new=f"{new}:{document}"):
                value = get(merged(self.TARGET / live), live_path)
                if document == "v1alpha1":
                    self.assertEqual(get(merged(self.TARGET / new), new_path), value)
                    continue
                doc = kinds(self.TARGET / new).get(document)
                self.assertIsNotNone(doc, f"no {document} in {new}")
                if new_path is not None:
                    self.assertEqual(get(doc, new_path), value)
                    continue
                state, means = DOCUMENT_MEANS[document]
                self.assertEqual(doc.get("$patch") == "delete", state == "deleted")
                if means is not None:
                    self.assertEqual(value, means)

    def test_each_live_field_has_a_place_in_the_lists(self):
        mapped = {(live, path) for live, path, *_ in SAME_SETTING}
        for live in {live for live, *_ in SAME_SETTING}:
            for leaf in leaves(merged(self.TARGET / live)):
                with self.subTest(live=live, field=leaf):
                    self.assertTrue(any(f == live and (leaf == p or leaf.startswith(p + "."))
                                        for f, p in mapped), f"{live}: {leaf} is in no SAME_SETTING entry")

    def test_each_list_setting_comes_from_a_live_file(self):
        listed = {p for name in LISTS for p in CLUSTERS["wsl"].get(name, [])}
        for new in sorted(listed):
            covered = {(d, p) for _, _, f, d, p in SAME_SETTING if f == new}
            for kind, doc in kinds(self.TARGET / new).items():
                with self.subTest(new=new, kind=kind):
                    self.assertIn(kind, {d for d, _ in covered})
                # Each field of a document that is kept is a mapped field, or lies under one.
                if doc.get("$patch") == "delete":
                    continue
                fields = {p for d, p in covered if d == kind and p is not None}
                for leaf in leaves({k: v for k, v in doc.items() if k not in DOCUMENT_KEYS}):
                    with self.subTest(new=new, kind=kind, field=leaf):
                        self.assertTrue(any(leaf == p or leaf.startswith(p + ".") for p in fields),
                                        f"{new}: {kind}.{leaf} is in no SAME_SETTING entry")
            for leaf in leaves(merged(self.TARGET / new)):
                with self.subTest(new=new, field=leaf):
                    self.assertTrue(any(d == "v1alpha1" and (leaf == p or leaf.startswith(p + "."))
                                        for d, p in covered), f"{new}: {leaf} is in no SAME_SETTING entry")


if __name__ == "__main__":
    unittest.main()
