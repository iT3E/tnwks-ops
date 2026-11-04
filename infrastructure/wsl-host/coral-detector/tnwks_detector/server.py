"""ZMQ REP server for Frigate's `zmq` detector, with Prometheus metrics.

Protocol (frigate/detectors/plugins/zmq_ipc.py, Frigate 0.18.2), one reply
per request:

    [{"model_request": true, "model_name": n}]        -> {"model_available", "model_loaded"}
    [{"model_data": true, "model_name": n}, bytes]     -> {"model_saved", "model_loaded"}
    [{"shape", "dtype", "model_type"}, tensor bytes]   -> 20x6 float32 (one raw frame)

Every request gets a reply, errors included (an all-zero result), because a
REP socket that skips one reply is wedged for good. Frigate turns its own
timeouts and errors into empty results without logging, so the metrics here
are the health signal for this path.

Exit codes, for the service manager's restart policy: 0 = stopped on request,
3 = an inference hung, 4 = the listen address went away, 5 = too many errors
in a row, 2 = bad arguments.
"""

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time

import numpy as np
import zmq
from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics, start_http_server

from tnwks_detector import __version__
from tnwks_detector.backends import BACKENDS, BackendError, get_backend

log = logging.getLogger("tnwks-detector")

ZERO = np.zeros((20, 6), np.float32).tobytes()

# No *_created series: they double the series count and nothing reads them.
disable_created_metrics()

_BUCKETS = (0.005, 0.0075, 0.01, 0.0125, 0.015, 0.0175, 0.02, 0.025, 0.03, 0.04, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0)


class Metrics:
    """Prometheus metrics, all labelled with this instance's `device` (its
    --name), so several detector services can share dashboards and alerts."""

    def __init__(self, device):
        d = ["device"]
        self.device = device
        self._info = Gauge(
            "tnwks_detector_info",
            "Detector server build and configuration.",
            d + ["version", "backend", "device_spec", "device_path", "bind"],
        )
        self.device_ok = Gauge(
            "tnwks_detector_device_ok", "1 while the accelerator is open and the last inference succeeded.", d
        ).labels(device)
        self._model_loaded = Gauge("tnwks_detector_model_loaded", "1 for the model currently loaded.", d + ["model"])
        self.ready = Gauge("tnwks_detector_ready", "1 while the ZMQ socket is bound and a model is loaded.", d).labels(
            device
        )
        self.start_time = Gauge("tnwks_detector_start_time_seconds", "Unix time the server process started.", d).labels(
            device
        )
        self.last_inference = Gauge(
            "tnwks_detector_last_inference_timestamp_seconds",
            "Unix time of the last successful inference (0 = none yet).",
            d,
        ).labels(device)
        self._requests = Counter("tnwks_detector_requests_total", "Requests received, by type.", d + ["type"])
        self.inferences = Counter("tnwks_detector_inferences_total", "Successful inferences.", d).labels(device)
        self._errors = Counter(
            "tnwks_detector_errors_total", "Requests answered with an error result, by reason.", d + ["reason"]
        )
        self.device_open_failures = Counter(
            "tnwks_detector_device_open_failures_total", "Failed attempts to open the accelerator.", d
        ).labels(device)
        self.inference_seconds = Histogram(
            "tnwks_detector_inference_duration_seconds", "Time spent in the backend per inference.", d, buckets=_BUCKETS
        ).labels(device)
        self.request_seconds = Histogram(
            "tnwks_detector_request_duration_seconds",
            "Time from receiving a detect request to sending its reply.",
            d,
            buckets=_BUCKETS,
        ).labels(device)
        # Pre-create the label sets the alerts read, so they exist at 0.
        for t in ("detect", "model_request", "model_data", "invalid"):
            self._requests.labels(device, t)
        for r in ("inference", "bad_tensor", "no_model"):
            self._errors.labels(device, r)

    def info(self, **labels):
        self._info.clear()
        self._info.labels(device=self.device, **labels).set(1)

    def request(self, kind):
        self._requests.labels(self.device, kind).inc()

    def error(self, reason):
        self._errors.labels(self.device, reason).inc()

    def model_loaded(self, name):
        self._model_loaded.clear()
        if name:
            self._model_loaded.labels(self.device, name).set(1)


EXIT_HUNG = 3
EXIT_ADDRESS_GONE = 4
EXIT_TOO_MANY_ERRORS = 5


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="tnwks_detector", description=__doc__.split("\n")[0])
    ap.add_argument("--backend", default="edgetpu", choices=sorted(BACKENDS))
    ap.add_argument(
        "--device",
        default="usb:0",
        help="backend device selector: usb:<n> or a /sys/bus/usb/devices/<bus>-<port> path for edgetpu",
    )
    ap.add_argument("--name", default="0", help="instance name, the `device` label on every metric")
    ap.add_argument("--lib-dir", required=True, help="directory with the backend's native libraries")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--model", default="edgetpu_model.tflite", help="model file in --model-dir to load at startup")
    ap.add_argument("--listen-address", required=True, help="IP to bind, e.g. the vEthernet (WSL) host IP")
    ap.add_argument("--zmq-port", type=int, default=5555)
    ap.add_argument("--metrics-port", type=int, default=9555)
    ap.add_argument(
        "--allow-model-upload",
        action="store_true",
        help="accept models Frigate uploads (model_data); off by default, models come from --model-dir",
    )
    ap.add_argument("--hang-timeout", type=float, default=10.0, help="exit if one inference takes longer (seconds)")
    ap.add_argument("--max-consecutive-errors", type=int, default=100)
    ap.add_argument("--no-priority-tweaks", action="store_true", help="skip the Windows EcoQoS opt-out")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


def windows_priority_tweaks():
    """Windows 11 runs background processes with EcoQoS (E-cores, low clocks),
    which added 20-50ms of tail latency per inference. Opt out of power
    throttling, raise the priority class and ask for a 1ms timer."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    # ProcessPowerThrottling = 4; EXECUTION_SPEED 0x1 | IGNORE_TIMER_RESOLUTION 0x4; StateMask 0 = never throttle.
    state = PowerThrottlingState(1, 0x1 | 0x4, 0)
    ok = k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state))
    prio = k32.SetPriorityClass(k32.GetCurrentProcess(), 0x80)  # HIGH_PRIORITY_CLASS
    ctypes.windll.winmm.timeBeginPeriod(1)
    log.info("power throttling opt-out=%s, priority HIGH=%s, timer 1ms", bool(ok), bool(prio))


def address_is_local(ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((ip, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Stop:
    def __init__(self):
        self.event = threading.Event()

    def install(self):
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), self._handle)

    def _handle(self, signum, _frame):
        log.info("signal %s: stopping", signum)
        self.event.set()

    def wait(self, seconds):
        return self.event.wait(seconds)

    @property
    def stopping(self):
        return self.event.is_set()


class Watchdog(threading.Thread):
    """Exits the process when one inference runs longer than the timeout, so the
    service manager restarts it and the device gets reopened."""

    def __init__(self, timeout):
        super().__init__(name="watchdog", daemon=True)
        self.timeout = timeout
        self.busy_since = 0.0

    def run(self):
        while True:
            time.sleep(1)
            since = self.busy_since
            if since and time.monotonic() - since > self.timeout:
                log.error("inference hung for more than %.0fs; exiting for a restart", self.timeout)
                logging.shutdown()
                os._exit(EXIT_HUNG)


class Server:
    def __init__(self, args, stop):
        self.args = args
        self.stop = stop
        self.backend = get_backend(args.backend)(lib_dir=args.lib_dir, device=args.device)
        self.m = Metrics(args.name)
        self.m.start_time.set(time.time())
        self.m.device_ok.set(0)
        self.m.ready.set(0)
        self.m.last_inference.set(0)
        self.set_info()
        self.watchdog = Watchdog(args.hang_timeout)
        self.consecutive_errors = 0

    def set_info(self):
        a = self.args
        self.m.info(
            version=__version__,
            backend=a.backend,
            device_spec=a.device,
            device_path=self.backend.device_path,
            bind=a.listen_address,
        )

    # -- startup ----------------------------------------------------------
    def wait_for_address(self):
        ip = self.args.listen_address
        waited = 0
        while not address_is_local(ip):
            if waited % 60 == 0:
                log.warning("%s is not a local address yet (WSL not running?); waiting", ip)
            if self.stop.wait(5):
                return False
            waited += 5
        return True

    def open_backend(self):
        delay = 5
        while not self.stop.stopping:
            try:
                self.backend.open()
                self.set_info()
                log.info("%s backend open: %s = %s", self.backend.name, self.args.device, self.backend.device_path)
                return True
            except Exception as e:  # missing device, driver, DLL
                self.m.device_open_failures.inc()
                self.m.device_ok.set(0)
                log.error("%s backend failed to open: %s; retrying in %ds", self.backend.name, e, delay)
                if self.stop.wait(delay):
                    return False
                delay = min(delay * 2, 60)
        return False

    def load_model(self, name):
        name = os.path.basename(name or "")
        if not name:
            return False
        if self.backend.model and self.backend.model.name == name:
            return True
        path = os.path.join(self.args.model_dir, name)
        if not os.path.isfile(path):
            log.warning("model %s not found in %s", name, self.args.model_dir)
            return False
        t = time.monotonic()
        try:
            info = self.backend.load(path)
        except Exception as e:
            log.error("loading %s failed: %s", name, e)
            self.m.error("model_load")
            return False
        self.m.model_loaded(info.name)
        self.m.device_ok.set(1)
        log.info("loaded %s in %.1fs, input %s %s", info.name, time.monotonic() - t, info.input_shape, info.input_dtype)
        return True

    # -- requests ---------------------------------------------------------
    def handle(self, frames):
        try:
            header = json.loads(bytes(frames[0].buffer))
            if not isinstance(header, dict):
                raise ValueError("header is not an object")
        except Exception as e:
            self.m.request("invalid")
            self.m.error("bad_header")
            log.warning("bad request header: %s", e)
            return [ZERO]

        if header.get("model_request"):
            self.m.request("model_request")
            ok = self.load_model(header.get("model_name"))
            return [json.dumps({"model_available": ok, "model_loaded": ok}).encode()]

        if header.get("model_data"):
            self.m.request("model_data")
            return [self.save_model(header, frames)]

        self.m.request("detect")
        return [self.detect(header, frames)]

    def save_model(self, header, frames):
        name = os.path.basename(header.get("model_name") or "")
        if not self.args.allow_model_upload:
            log.warning("refused upload of model %r (start with --allow-model-upload to accept)", name)
            self.m.error("upload_refused")
            return json.dumps({"model_saved": False, "model_loaded": False}).encode()
        if not name.endswith(".tflite") or len(frames) < 2:
            self.m.error("bad_upload")
            return json.dumps({"model_saved": False, "model_loaded": False}).encode()
        path = os.path.join(self.args.model_dir, name)
        tmp = path + ".upload"
        with open(tmp, "wb") as f:
            f.write(bytes(frames[1].buffer))
        os.replace(tmp, path)
        log.info("saved uploaded model %s", name)
        ok = self.load_model(name)
        return json.dumps({"model_saved": True, "model_loaded": ok}).encode()

    def detect(self, header, frames):
        start = time.perf_counter()
        model = self.backend.model
        if model is None:
            self.m.error("no_model")
            return ZERO
        try:
            if len(frames) < 2:
                raise ValueError("no tensor frame")
            if header.get("model_type", "ssd") != "ssd":
                raise ValueError("model_type %r is not supported" % header.get("model_type"))
            dtype = np.dtype(header.get("dtype", "uint8"))
            if dtype.name != model.input_dtype:
                raise ValueError("dtype %s, model wants %s" % (dtype.name, model.input_dtype))
            tensor = np.frombuffer(frames[1].buffer, dtype=dtype)
            if tensor.size != int(np.prod(model.input_shape)):
                raise ValueError("tensor has %d values, model wants %s" % (tensor.size, model.input_shape))
        except Exception as e:
            self.m.error("bad_tensor")
            log.warning("bad detect request: %s", e)
            return ZERO

        t = time.perf_counter()
        self.watchdog.busy_since = time.monotonic()
        try:
            result = self.backend.detect(tensor)
        except Exception as e:
            self.watchdog.busy_since = 0.0
            self.consecutive_errors += 1
            self.m.error("inference")
            self.m.device_ok.set(0)
            log.error("inference failed (%d in a row): %s", self.consecutive_errors, e)
            return ZERO
        self.watchdog.busy_since = 0.0
        done = time.perf_counter()
        self.consecutive_errors = 0
        self.m.inference_seconds.observe(done - t)
        self.m.inferences.inc()
        self.m.last_inference.set(time.time())
        self.m.device_ok.set(1)
        reply = result.astype(np.float32, copy=False).tobytes()
        self.m.request_seconds.observe(time.perf_counter() - start)
        return reply

    # -- main loop --------------------------------------------------------
    def run(self):
        a = self.args
        if not self.wait_for_address():
            return 0
        start_http_server(a.metrics_port, addr=a.listen_address)
        log.info("metrics on http://%s:%d/metrics", a.listen_address, a.metrics_port)
        if not self.open_backend():
            return 0
        self.load_model(a.model)
        self.watchdog.start()

        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.setsockopt(zmq.LINGER, 0)
        endpoint = "tcp://%s:%d" % (a.listen_address, a.zmq_port)
        sock.bind(endpoint)
        self.m.ready.set(1 if self.backend.model else 0)
        log.info("listening on %s", endpoint)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)

        rc = 0
        last_address_check = time.monotonic()
        try:
            while not self.stop.stopping:
                if poller.poll(500):
                    frames = sock.recv_multipart(copy=False)
                    try:
                        reply = self.handle(frames)
                    except Exception as e:  # never leave the REP socket without a reply
                        log.exception("request failed: %s", e)
                        self.m.error("internal")
                        reply = [ZERO]
                    sock.send_multipart(reply, copy=False)
                    self.m.ready.set(1 if self.backend.model else 0)
                    if self.consecutive_errors >= a.max_consecutive_errors:
                        log.error("%d inference errors in a row; exiting for a restart", self.consecutive_errors)
                        rc = EXIT_TOO_MANY_ERRORS
                        break
                now = time.monotonic()
                if now - last_address_check >= 5:
                    last_address_check = now
                    if not address_is_local(a.listen_address):
                        log.error("%s is no longer a local address; exiting for a restart", a.listen_address)
                        rc = EXIT_ADDRESS_GONE
                        break
        finally:
            self.m.ready.set(0)
            sock.close(0)
            ctx.term()
            self.backend.close()
            log.info("stopped (exit %d)", rc)
        return rc


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log.info(
        "tnwks_detector %s, instance %s, backend %s device %s, python %s",
        __version__,
        args.name,
        args.backend,
        args.device,
        sys.version.split()[0],
    )
    if not args.no_priority_tweaks:
        try:
            windows_priority_tweaks()
        except Exception as e:
            log.warning("priority tweaks failed: %s", e)
    stop = Stop()
    stop.install()
    try:
        return Server(args, stop).run()
    except BackendError as e:
        log.error("%s", e)
        return 2
