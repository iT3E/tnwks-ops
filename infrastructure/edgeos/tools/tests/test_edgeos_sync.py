"""No router connections: exercise public output with synthetic credentials."""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("edgeos_sync", Path(__file__).parents[1] / "edgeos-sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class PublicOutput(unittest.TestCase):
    def test_plan_counts_changes_without_disclosing_any_values(self):
        # A retired value isn't in SOPS; SSH values and short secrets used to
        # bypass masking too. No live configuration belongs in a public log.
        secrets = {key: "synthetic-" + key for key in ("password", "ssh_user")}
        secrets["short"] = "xy"
        live = ["set system login user account authentication plaintext-password retired-canary"]
        desired = [f"set service value '{value}'" for value in secrets.values()]
        output = io.StringIO()
        with patch.object(sys, "argv", ["edgeos-sync.py", "plan", "router"]), \
                patch.object(sync, "load_device", return_value=({"host": "192.0.2.1"}, secrets, desired)), \
                patch.object(sync, "live_lines", return_value=live), \
                patch.object(sync, "run_session") as session, contextlib.redirect_stdout(output):
            self.assertEqual(sync.main(), 2)
        self.assertEqual(output.getvalue(), "router (192.0.2.1): 1 to delete, 3 to set\n"
                         "Validated on the device (staged and discarded).\n")
        # The private command stream still has the exact changes and discards
        # the candidate; suppressing output must not change router behavior.
        script = session.call_args.args[2]
        for value in ["retired-canary", *secrets.values()]:
            self.assertIn(value, script)
        self.assertIn(f"{sync.WRAPPER} discard", script)
        self.assertNotIn(f"{sync.WRAPPER} commit", script)

    def test_ssh_errors_do_not_echo_remote_output_or_login(self):
        device = {"host": "192.0.2.1", "host_key": "ssh-ed25519 synthetic"}
        secrets = {"ssh_user": "login-canary"}
        failures = [
            subprocess.CompletedProcess([], 1, stdout="private-canary", stderr="private-canary"),
            subprocess.TimeoutExpired("private-canary", 300, stderr="private-canary"),
            OSError("private-canary"),
        ]
        for failure in failures:
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            output = io.StringIO()
            with self.subTest(failure=type(failure).__name__), \
                    patch.dict(os.environ, {"EDGEOS_SSH_KEY_FILE": "/synthetic/key"}), \
                    patch.object(sync.subprocess, "run", **kwargs), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as error:
                    sync.ssh(device, secrets, "remote")
                self.assertEqual(error.exception.code, 1)
            self.assertIn("withheld", output.getvalue())
            self.assertNotIn("canary", output.getvalue())


if __name__ == "__main__":
    unittest.main()
