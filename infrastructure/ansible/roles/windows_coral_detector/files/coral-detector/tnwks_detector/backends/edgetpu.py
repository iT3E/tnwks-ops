"""Coral Edge TPU over USB through tflite_runtime and libedgetpu.

Windows only: needs tflite_runtime 2.5.0.post1 (cp39), edgetpu.dll from
edgetpu_runtime_20221024 (libedgetpu/direct) and the libusb shim
(libusb-1.0.dll + libusbreal.dll), all in lib_dir. One process holds one
Coral; several Corals need one server each.

Device selection (`device`):
  "usb" or "usb:<n>"            the n-th USB Edge TPU in libedgetpu's enumeration order
  "/sys/bus/usb/devices/<b-p>"  the Edge TPU on that USB bus/port, as edgetpu_list_devices
                                reports it (stable while the Coral stays in the same port)
"""

import ctypes
import logging
import os
import time

import numpy as np

from tnwks_detector.backends.base import MAX_DETECTIONS, MIN_SCORE, Backend, BackendError, ModelInfo

log = logging.getLogger("tnwks-detector.edgetpu")

EDGETPU_APEX_PCI = 0
EDGETPU_APEX_USB = 1


class _EdgeTpuDevice(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("path", ctypes.c_char_p)]


class EdgeTpuBackend(Backend):
    name = "edgetpu"

    def __init__(self, lib_dir, device="usb:0"):
        super().__init__(lib_dir, device)
        self._dll_path = os.path.join(lib_dir, "edgetpu.dll")
        self._delegate = None
        self._interpreter = None
        self._input_index = None
        self._input_shape = None
        self._outputs = None
        self.device_path = ""

    def _lib(self):
        if hasattr(os, "add_dll_directory") and not getattr(self, "_dll_dir", None):
            # edgetpu.dll imports libusb-1.0.dll (the shim) from here.
            self._dll_dir = os.add_dll_directory(self.lib_dir)
        if not os.path.exists(self._dll_path):
            raise BackendError("missing %s" % self._dll_path)
        lib = ctypes.CDLL(self._dll_path)
        lib.edgetpu_list_devices.restype = ctypes.POINTER(_EdgeTpuDevice)
        lib.edgetpu_list_devices.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
        lib.edgetpu_free_devices.argtypes = [ctypes.POINTER(_EdgeTpuDevice)]
        return lib

    def list_devices(self):
        """USB Edge TPU paths in libedgetpu's order (the n of "usb:<n>")."""
        lib = self._lib()
        n = ctypes.c_size_t(0)
        devs = lib.edgetpu_list_devices(ctypes.byref(n))
        try:
            return [devs[i].path.decode() for i in range(n.value) if devs[i].type == EDGETPU_APEX_USB]
        finally:
            if n.value:
                lib.edgetpu_free_devices(devs)

    def _resolve(self):
        spec = self.device or "usb:0"
        paths = self.list_devices()
        if spec.startswith("/"):
            if spec not in paths:
                raise BackendError("no Edge TPU at %s (found: %s)" % (spec, ", ".join(paths) or "none"))
            return "usb:%d" % paths.index(spec), spec, paths
        if spec == "usb":
            spec = "usb:0"
        if not spec.startswith("usb:") or not spec[4:].isdigit():
            raise BackendError("device %r: use usb, usb:<n> or a /sys/bus/usb/devices/<bus>-<port> path" % spec)
        idx = int(spec[4:])
        if idx >= len(paths):
            raise BackendError("no Edge TPU %s (found %d: %s)" % (spec, len(paths), ", ".join(paths) or "none"))
        return spec, paths[idx], paths

    def open(self):
        if self._delegate is not None:
            return
        from tflite_runtime.interpreter import load_delegate

        for _attempt in range(3):
            option, path, before = self._resolve()
            try:
                delegate = load_delegate(self._dll_path, {"device": option})
            except ValueError as e:
                raise BackendError("no Edge TPU at %s (%s): %s" % (option, path, e or "load_delegate failed")) from e
            # Opening a Coral in DFU mode flashes it and it re-enumerates; if the
            # list changed meanwhile (another Coral did the same), the index
            # may have pointed elsewhere, so resolve and open again.
            after = self.list_devices()
            if after == before:
                self._delegate = delegate
                self.device_path = path
                log.info("opened Edge TPU %s at %s (all: %s)", option, path, ", ".join(after))
                return
            log.warning("Edge TPU list changed while opening (%s -> %s); retrying", before, after)
            del delegate
            time.sleep(2)
        raise BackendError("Edge TPU list kept changing while opening %s" % self.device)

    def load(self, model_path):
        from tflite_runtime.interpreter import Interpreter

        if self._delegate is None:
            raise BackendError("device not open")
        it = Interpreter(model_path=model_path, experimental_delegates=[self._delegate])
        it.allocate_tensors()
        inp = it.get_input_details()[0]
        outputs = [o["index"] for o in it.get_output_details()]
        if len(outputs) < 4:
            raise BackendError("%s has %d outputs; expected an SSD model with 4" % (model_path, len(outputs)))
        self._interpreter = it
        self._input_index = inp["index"]
        self._input_shape = tuple(int(x) for x in inp["shape"])
        self._outputs = outputs
        self.model = ModelInfo(os.path.basename(model_path), self._input_shape, np.dtype(inp["dtype"]).name)
        return self.model

    def detect(self, tensor):
        it = self._interpreter
        if it is None:
            raise BackendError("no model loaded")
        it.set_tensor(self._input_index, tensor.reshape(self._input_shape))
        it.invoke()
        # Output order of Frigate's SSD models: boxes, classes, scores, count.
        boxes = it.get_tensor(self._outputs[0])[0]
        classes = it.get_tensor(self._outputs[1])[0]
        scores = it.get_tensor(self._outputs[2])[0]
        count = int(it.get_tensor(self._outputs[3])[0])
        out = np.zeros((MAX_DETECTIONS, 6), np.float32)
        for i in range(min(count, MAX_DETECTIONS)):
            if scores[i] < MIN_SCORE:
                break
            out[i] = [classes[i], scores[i], boxes[i][0], boxes[i][1], boxes[i][2], boxes[i][3]]
        return out

    def close(self):
        # Dropping the interpreter and the delegate releases the USB device.
        self._interpreter = None
        self._delegate = None
        self.model = None
        time.sleep(0.1)
