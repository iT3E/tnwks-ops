"""The Talos patches that 01-talos-bootstrap.yml gives a new cluster.

Each YAML file under talos/ parses. Each directory under talos/ is a cluster
of talos_clusters (inventory/group_vars/all/talos.yml). Each file that a
patch list names is there, and each of its documents is a mapping. Each YAML
file of a cluster with patch lists is in one of its lists, or in NOT_PATCHES.
A cluster with no patch lists is in NO_LISTS, with the reason.

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


def yaml_files(directory):
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in (".yaml", ".yml"))


def documents(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d is not None]


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
                if patch in NOT_PATCHES:
                    continue
                with self.subTest(target=target, patch=patch):
                    self.assertIn(patch, listed, f"talos_clusters.{target} does not list {patch}")

    def test_each_audit_policy_is_for_the_control_plane(self):
        for target, cluster in CLUSTERS.items():
            if not (TALOS / target / "patches/audit-policy.yaml").is_file():
                continue
            with self.subTest(target=target):
                self.assertIn("patches/audit-policy.yaml", cluster.get("controlplane_patches", []))
                self.assertNotIn("patches/audit-policy.yaml", cluster.get("config_patches", []))


if __name__ == "__main__":
    unittest.main()
