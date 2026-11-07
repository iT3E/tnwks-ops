"""Safety contracts for disk initialization, mounting and VHDX compaction."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


volume = load("volume", "roles/windows_observability_storage/files/observability-volume.py")
UUID = "1e3df741-9195-4b1b-b612-284a74e6e425"


class DiskGuards(unittest.TestCase):
    def disk(self, **changes):
        return {"name": "/dev/fixture", "size": 20 * volume.GIB, "type": "disk",
                "fstype": None, "uuid": None, "mountpoints": [None], **changes}

    def test_unrecognized_disks_never_reach_formatting(self):
        cases = [self.disk(size=1024 * volume.GIB), self.disk(type="part"),
                 self.disk(children=[{"name": "/dev/fixture1"}]),
                 self.disk(fstype="ext4", uuid="another-volume"),
                 self.disk(fstype="swap"), self.disk(mountpoints=["/"])]
        for device in cases:
            with self.subTest(device=device), patch.object(volume, "run") as run:
                with self.assertRaises(AssertionError):
                    volume.initialize(device, 20 * volume.GIB, UUID)
                run.assert_not_called()

    def test_signature_on_apparently_blank_disk_blocks_formatting(self):
        with patch.object(volume, "run", return_value='{"signatures":[{"type":"gpt"}]}') as run:
            with self.assertRaises(AssertionError):
                volume.initialize(self.disk(), 20 * volume.GIB, UUID)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], "wipefs")

    def test_known_completed_initialization_is_never_reformatted(self):
        with patch.object(volume, "run") as run:
            volume.initialize(self.disk(fstype="ext4", uuid=UUID), 20 * volume.GIB, UUID)
            run.assert_not_called()

    def test_blank_disk_formats_only_with_recorded_identity(self):
        with patch.object(volume, "run", return_value='{"signatures":[]}') as run:
            volume.initialize(self.disk(), 20 * volume.GIB, UUID)
            command = run.call_args.args
            self.assertEqual(command[0], "mkfs.ext4")
            self.assertEqual(command[command.index("-U") + 1], UUID)
            self.assertEqual(command[-1], "/dev/fixture")

    def test_normal_startup_rejects_blank_device(self):
        with self.assertRaises(AssertionError):
            volume.validate_disk(self.disk(), 20 * volume.GIB, UUID)


class MountGuards(unittest.TestCase):
    device = {"name": "/dev/fixture", "mountpoints": [None]}

    def test_mountpoint_is_inside_the_workers_var_volume(self):
        mounts = [{"Type": "volume", "Destination": "/etc/cni", "Source": "/docker/other"},
                  {"Type": "volume", "Destination": "/var", "Source": "/docker/var/_data"}]
        labels = {"talos.cluster.name": volume.CLUSTER, "talos.type": "worker"}
        output = json.dumps(labels) + "\n" + json.dumps(mounts) + "\n"
        with patch.object(volume, "run", return_value=output) as run:
            self.assertEqual(volume.mountpoint(), Path("/docker/var/_data") / volume.STORAGE)
        self.assertIn("--format", run.call_args.args, "Plain inspect output holds the Talos config")

    def test_unknown_worker_is_refused(self):
        labels = {"talos.cluster.name": "another", "talos.type": "worker"}
        with patch.object(volume, "run", return_value=json.dumps(labels) + "\n[]\n"):
            with self.assertRaises(AssertionError):
                volume.mountpoint()

    def test_bare_mountpoint_is_immutable_before_the_disk_covers_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            point = Path(tmp) / "observability"
            with patch.object(volume, "mounted", side_effect=[False, True]), \
                    patch.object(volume, "run") as run:
                volume.attach(self.device, point, UUID)
            self.assertEqual([call.args[0] for call in run.call_args_list], ["chattr", "mount"])

    def test_data_on_a_bare_mountpoint_is_never_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            point = Path(tmp)
            (point / "stray").write_text("data")
            with patch.object(volume, "mounted", return_value=False), patch.object(volume, "run") as run:
                with self.assertRaises(AssertionError):
                    volume.attach(self.device, point, UUID)
            run.assert_not_called()


class CompactionGuards(unittest.TestCase):
    def test_compaction_pauses_and_restarts_the_mount_task(self):
        # Its wsl.exe call would restart Ubuntu mid-compaction, as the UDP relays
        # did on 2026-10-31, and worker-1 waits for it afterwards.
        script = (Path(__file__).resolve().parents[1]
                  / "roles/windows_wsl_maintenance/files/Compact-WslVhdx.ps1").read_text()
        paused = next(line for line in script.splitlines() if line.startswith("$Paused"))
        started = next(line for line in script.splitlines() if "Start-ScheduledTask" not in line
                       and line.strip().startswith("foreach ($t in @('tnwks-wsl-keepalive'"))
        self.assertIn("tnwks-observability-mount", paused)
        self.assertIn("tnwks-observability-mount", started)


if __name__ == "__main__":
    unittest.main()
