"""Each Vector `vector` sink in kubernetes/ delivers an event to its aggregator.

`vector test` (test_hand_written_rules.py) runs the transforms only: it never
opens a sink. Here each `vector` sink of a Vector config next to the
aggregator is started as it is written, with the image of its HelmRelease, and
sends one event to the aggregator's own `vector` source on the same port,
started with the image of the aggregator. The sink keeps its scheme, its
compression and its other settings; only the host becomes the test container.
A sink whose address Vector reads as https while the aggregator listens in
plain HTTP/2 fails here (Vector 0.61 and later: no scheme means https).

Each test needs Docker. Without it a test is skipped, except in CI, where it
fails.
"""

import re
import subprocess
import time
import uuid

import pytest
import yaml
from test_hand_written_rules import FLUX_DEFAULT, FLUX_VARIABLE, ROOT, docker, scratch, vector_image

VECTOR = ROOT / "kubernetes/apps/monitoring/vector"
AGGREGATOR = VECTOR / "aggregator/config/vector.yaml"
ADDRESS = re.compile(r"\A(?P<scheme>[a-z]+://)?(?P<host>[^:/]+):(?P<port>\d+)\Z")
WAIT_SECONDS = 60


def load(config):
    text = FLUX_DEFAULT.sub(r"\1", config.read_text())
    return yaml.safe_load(FLUX_VARIABLE.sub("test", text))


def vector_sinks():
    """(config, sink id) of each `vector` sink next to the aggregator."""
    found = []
    for config in sorted(VECTOR.glob("*/config/vector.yaml")):
        for name, sink in (load(config).get("sinks") or {}).items():
            if sink.get("type") == "vector":
                found.append((config, name))
    return found


SINKS = vector_sinks()


def aggregator_source(port):
    sources = load(AGGREGATOR)["sources"]
    for source in sources.values():
        if source.get("type") == "vector" and source["address"].endswith(f":{port}"):
            return source
    pytest.fail(f"the aggregator has no vector source on port {port}")


def test_there_are_sinks_to_check():
    assert len(SINKS) >= 3, SINKS


@pytest.mark.parametrize(("config", "name"), SINKS, ids=[f"{c.parent.parent.name}-{n}" for c, n in SINKS])
def test_sink_delivers_to_the_aggregator(config, name):
    binary = docker()
    sink = dict(load(config)["sinks"][name])
    address = ADDRESS.match(sink["address"])
    assert address, f"{config}: {sink['address']} is not [scheme://]host:port"
    port = address["port"]
    tag = uuid.uuid4().hex[:12]
    network, receiver, sender = f"sinks-{tag}", f"aggregator-{tag}", f"sender-{tag}"
    marker = f"sink-check-{tag}"
    sink["inputs"] = ["event"]
    sink["address"] = f"{address['scheme'] or ''}{receiver}:{port}"
    work = scratch()
    (work / "receiver.yaml").write_text(yaml.safe_dump({
        "data_dir": "/tmp",
        "sources": {"in": aggregator_source(port)},
        "sinks": {"out": {"type": "console", "inputs": ["in"], "encoding": {"codec": "json"}}},
    }))
    (work / "sender.yaml").write_text(yaml.safe_dump({
        "data_dir": "/tmp",
        "sources": {"event": {"type": "demo_logs", "format": "shuffle", "lines": [marker], "interval": 1}},
        "sinks": {name: sink},
    }))
    subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
    mount = f"type=bind,src={work},dst=/t,readonly"
    started = []
    try:
        subprocess.run([binary, "network", "create", network], check=True, capture_output=True)
        for container, image, file in (
            (receiver, vector_image(AGGREGATOR), "receiver.yaml"),
            (sender, vector_image(config), "sender.yaml"),
        ):
            subprocess.run([
                binary, "run", "-d", "--name", container, "--network", network, "--mount", mount,
                "--entrypoint", "vector", image, "--config", f"/t/{file}",
            ], check=True, capture_output=True)
            started.append(container)
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            logs = subprocess.run([binary, "logs", receiver], capture_output=True, text=True, check=False)
            if marker in logs.stdout:
                return
            time.sleep(2)
        sent = subprocess.run([binary, "logs", sender], capture_output=True, text=True, check=False)
        pytest.fail(f"{config.relative_to(ROOT)} sink {name}: no event in {WAIT_SECONDS}s\n"
                    + (sent.stdout + sent.stderr)[-4000:])
    finally:
        for container in started:
            subprocess.run([binary, "rm", "-f", container], capture_output=True, check=False)
        subprocess.run([binary, "network", "rm", network], capture_output=True, check=False)
        subprocess.run(["rm", "-rf", str(work)], check=False)
