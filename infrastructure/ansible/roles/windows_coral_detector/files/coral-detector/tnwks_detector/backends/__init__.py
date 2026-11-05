"""Detector backends.

A backend owns one accelerator. The server calls, in this order:

    backend = get_backend(name)(lib_dir=..., device=...)
    backend.open()                 # acquire the device; raise if it is missing
    info = backend.load(path)      # load a model file; returns its input spec
    out = backend.detect(tensor)   # (20, 6) float32 rows [class, score, ymin, xmin, ymax, xmax]
    backend.close()                # release the device

To add an accelerator, write a module here with a Backend subclass and add it
to BACKENDS. Its Python packages go into requirements.in.
"""

import importlib

from tnwks_detector.backends.base import Backend, BackendError, ModelInfo

BACKENDS = {
    # name: "module:class"
    "edgetpu": "tnwks_detector.backends.edgetpu:EdgeTpuBackend",
}


def get_backend(name):
    try:
        target = BACKENDS[name]
    except KeyError:
        raise BackendError("unknown backend %r (known: %s)" % (name, ", ".join(sorted(BACKENDS)))) from None
    module, cls = target.split(":")
    return getattr(importlib.import_module(module), cls)


__all__ = ["Backend", "BackendError", "ModelInfo", "BACKENDS", "get_backend"]
