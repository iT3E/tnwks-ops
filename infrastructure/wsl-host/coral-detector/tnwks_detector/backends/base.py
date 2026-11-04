import abc
from typing import NamedTuple, Tuple

import numpy as np

# Frigate's detector API returns at most 20 detections, 6 floats each.
MAX_DETECTIONS = 20
MIN_SCORE = 0.4


class BackendError(Exception):
    pass


class ModelInfo(NamedTuple):
    name: str
    input_shape: Tuple[int, ...]
    input_dtype: str


class Backend(abc.ABC):
    """One accelerator. Not thread-safe: the server calls it from one thread."""

    name = "base"

    def __init__(self, lib_dir, device=None):
        self.lib_dir = lib_dir
        self.device = device  # backend-specific device selector, e.g. "usb:0"
        self.device_path = ""  # what the selector resolved to, once open
        self.model = None  # ModelInfo of the loaded model

    @abc.abstractmethod
    def open(self):
        """Acquire the device. Raise BackendError when it is not available."""

    @abc.abstractmethod
    def load(self, model_path):
        """Load a model file and return its ModelInfo."""

    @abc.abstractmethod
    def detect(self, tensor):
        """Run one inference. Returns a (20, 6) float32 array."""

    def close(self):  # noqa: B027 (optional hook)
        """Release the device. Must be safe to call more than once."""
        self.model = None

    @staticmethod
    def empty_result():
        return np.zeros((MAX_DETECTIONS, 6), np.float32)
