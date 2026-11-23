"""Verify router record names with Terraform and without providers or state."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter


ROOT = Path(__file__).resolve().parents[3] / "terraform/environments/prod/mikrotik"


class RouterDNS(unittest.TestCase):
    def test_generated_and_site_dns_names_are_unique(self):
        with tempfile.TemporaryDirectory() as fixture:
            for name in ("locals.tf", "dns-sites.tf"):
                shutil.copyfile(ROOT / name, Path(fixture) / name)
            result = subprocess.run(
                ["terraform", "console", "-no-color"],
                cwd=fixture,
                input='jsonencode([for record in concat(values(local.dns.static_records), '
                      'values(local.site_dns_records)) : trimsuffix(lower(record.name), ".")])\n',
                text=True,
                check=True,
                capture_output=True,
            )
        names = Counter(json.loads(json.loads(result.stdout)))
        self.assertEqual(sorted(name for name, count in names.items() if count > 1), [])


if __name__ == "__main__":
    unittest.main()
