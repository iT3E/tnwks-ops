"""Exercise actual listener setup without starting background network fetches."""

import contextlib
import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "config" / "flow_collector.py"


def load(address=None):
    spec = importlib.util.spec_from_file_location("collector", SOURCE)
    module = importlib.util.module_from_spec(spec)
    env = {} if address is None else {"BIND_ADDRESS": address}
    with patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class Listener(unittest.TestCase):
    def test_udp_and_metrics_use_the_assigned_address(self):
        for address in (None, "192.0.2.10"):
            with self.subTest(address=address):
                collector = load(address)
                expected = address or "127.0.0.1"
                with patch.object(collector.socket, "socket") as socket, contextlib.redirect_stdout(io.StringIO()):
                    socket.return_value.recvfrom.side_effect = InterruptedError
                    with self.assertRaises(InterruptedError):
                        collector.flow_loop()
                    socket.return_value.bind.assert_called_once_with((expected, collector.LISTEN_PORT))
                with patch.object(collector.threading, "Thread"), \
                        patch.object(collector, "ThreadingHTTPServer") as http, \
                        contextlib.redirect_stdout(io.StringIO()):
                    collector.main()
                    http.assert_called_once_with((expected, collector.METRICS_PORT), collector.Handler)

    def test_wildcard_or_invalid_overrides_fail_closed(self):
        for address in ("0.0.0.0", "::", "", "not-an-address"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                load(address)


if __name__ == "__main__":
    unittest.main()
