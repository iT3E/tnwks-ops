"""Object detector server for Frigate's `zmq` detector (docs/wsl/coral-detector.md).

Runs on the Windows host as the tnwks-coral-detector service and answers
Frigate's ZMQ IPC detector (frigate/detectors/plugins/zmq_ipc.py, Frigate 0.18)
over TCP. The accelerator sits behind a small backend interface
(tnwks_detector.backends), so another one can be added without changing the
service or its Ansible role.
"""

__version__ = "1.0.0"
