"""The Talos patches that 01-talos-bootstrap.yml gives a new cluster.

Each YAML file under talos/ parses. Each file that a patch list of
talos_clusters (inventory/group_vars/all/talos.yml) names is there, and each
of its documents is a mapping. Each file in talos/<target>/patches/ of a
cluster with patch lists is in one of its lists, so a rebuild gets it.

This is no validation of a machine config: that needs talosctl and a full
config. talos/README.md says how a patch reaches a running node.

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


def documents(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d is not None]


class TalosPatches(unittest.TestCase):
    def test_each_talos_file_parses(self):
        files = sorted(TALOS.rglob("*.yaml")) + sorted(TALOS.rglob("*.yml"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                documents(path)

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
            listed = {p for name in LISTS for p in cluster.get(name, [])}
            if not listed:
                continue
            for path in sorted((TALOS / target / "patches").glob("*.y*ml")):
                patch = str(path.relative_to(TALOS / target))
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
