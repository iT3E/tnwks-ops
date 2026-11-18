"""The vault shares on the NFS server of the cluster
(docs/apps/agent-platform.md, "The vault shares").

Each trust domain with a vault has a claim of its own in `storage`, in the
form of the drop box claim, and an export of its own: read and write for the
nodes of this site only, each client squashed to the user of the agent image.
The exports of today keep their ids, so the file handles of their clients
stay valid when the server restarts.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/storage/nfs-server/app"
KS = ROOT / "kubernetes/clusters/wsl/apps/nfs-server-ks.yaml"
SITES = ROOT / "kubernetes/apps/production/sites/app/deployment.yaml"
SHARES = {"nfs-vault-main": "/vault-main", "nfs-vault-finance": "/vault-finance"}


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def claims() -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d
        for d in docs(APP / "pvc.yaml")
        if d["kind"] == "PersistentVolumeClaim"
    }


def pod() -> dict:
    (deployment,) = [d for d in docs(APP / "deployment.yaml") if d["kind"] == "Deployment"]
    return deployment["spec"]["template"]["spec"]


def exports() -> dict[int, str]:
    """Each EXPORT block of the config, by its Export_Id."""
    (config,) = docs(APP / "configmap.yaml")
    text = config["data"]["ganesha.conf"]
    blocks = {}
    for match in re.finditer(r"^EXPORT \{\n(.*?)^\}$", text, re.MULTILINE | re.DOTALL):
        block = match.group(1)
        (export_id,) = re.findall(r"Export_Id = (\d+);", block)
        assert int(export_id) not in blocks, export_id
        blocks[int(export_id)] = block
    return blocks


def setting(block: str, name: str) -> list[str]:
    return re.findall(rf"^\s*{name} = ([^;]+);", block, re.MULTILINE)


def nodes_of_this_site() -> set[str]:
    """The read-write clients of the drop box export on the network of the
    server: the nodes of homelab-wsl."""
    server = next(v["nfs"]["server"] for v in pod_volumes(SITES) if "nfs" in v)
    network = server.rsplit(".", 1)[0] + "."
    block = exports()[2]
    clients = set()
    for client in re.findall(r"CLIENT \{(.*?)\}", block, re.DOTALL):
        if setting(client, "Access_Type") == ["RW"]:
            for (value,) in [setting(client, "Clients")]:
                clients |= {c.strip() for c in value.split(",") if c.strip().startswith(network)}
    return clients


def pod_volumes(path: Path) -> list[dict]:
    (deployment,) = [d for d in docs(path) if d["kind"] == "Deployment"]
    return deployment["spec"]["template"]["spec"]["volumes"]


def test_each_share_has_a_claim_in_the_form_of_the_drop_box():
    found = claims()
    drop_box = found["nfs-gdrive-sync"]
    for name in SHARES:
        claim = found[name]
        assert claim["metadata"]["namespace"] == "storage"
        assert claim["spec"]["accessModes"] == drop_box["spec"]["accessModes"]
        assert claim["spec"]["storageClassName"] == drop_box["spec"]["storageClassName"]


def test_the_server_mounts_each_share_where_it_exports_it():
    spec = pod()
    volumes = {
        v["name"]: v["persistentVolumeClaim"]["claimName"]
        for v in spec["volumes"]
        if "persistentVolumeClaim" in v
    }
    (server,) = spec["containers"]
    (init,) = spec["initContainers"]
    for claim, path in SHARES.items():
        (volume,) = [name for name, c in volumes.items() if c == claim]
        assert {"name": volume, "mountPath": path} in server["volumeMounts"]
        assert {"name": volume, "mountPath": path} in init["volumeMounts"]
        # The root belongs to the squash uid, mode 0700.
        assert path in init["command"][-1].split("&&")[0]
        assert path in init["command"][-1].split("&&")[1]


def test_the_exports_of_today_keep_their_ids():
    found = exports()
    assert setting(found[1], "Path") == ["/export"]
    assert setting(found[2], "Path") == ["/gdrive-sync"]


def test_each_share_is_for_the_nodes_of_this_site_squashed_to_one_uid():
    nodes = nodes_of_this_site()
    assert len(nodes) == 3
    found = exports()
    by_path = {setting(b, "Path")[0]: b for b in found.values()}
    assert set(SHARES.values()) <= set(by_path)
    for path in SHARES.values():
        block = by_path[path]
        assert setting(block, "Pseudo") == [path]
        assert setting(block, "Squash") == ["All_Squash"]
        assert setting(block, "Anonymous_Uid") == ["1000"]
        assert setting(block, "Anonymous_Gid") == ["1000"]
        assert setting(block, "SecType") == ["sys"]
        assert setting(block, "Protocols") == ["4"]
        clients = re.findall(r"CLIENT \{(.*?)\}", block, re.DOTALL)
        assert len(clients) == 1
        assert setting(clients[0], "Access_Type") == ["RW"]
        (value,) = setting(clients[0], "Clients")
        assert {c.strip() for c in value.split(",")} == nodes
        # The export itself grants nothing: only the listed clients.
        outside = re.sub(r"CLIENT \{.*?\}", "", block, flags=re.DOTALL)
        assert setting(outside, "Access_Type") == ["None"]


def test_the_kustomization_still_waits_for_each_object():
    # The claims bind when the server mounts them, so `wait: true` holds.
    (ks,) = docs(KS)
    assert ks["spec"]["wait"] is True
