"""Reads the audit log of the throwaway API server (run.sh) from standard
input, after test_nfs_volumes.py.

The binder of the controller manager updates each volume when it binds it
(the uid of the claim). Those updates must be in the log, without an
annotation like the rest.

The bindings of git are Audit and Warn: a volume that a rule refuses gets an
audit annotation. This checks that no change of a volume or a claim of the
vault shares (volumes.json and claims.json, from git) has one, and, so that
the check can fail, that the volume off the list of the test (`sites`) has
one from nfs-persistent-volumes.

  docker logs <apiserver> | python3 audit.py <render directory>
"""

import json
import sys
from pathlib import Path

KEY = "validation.policy.admission.k8s.io/validation_failure"


def main(work: Path) -> int:
    names = {
        d["metadata"]["name"] for d in json.loads((work / "volumes.json").read_text())
    }
    names |= {
        d["metadata"]["name"] for d in json.loads((work / "claims.json").read_text())
    }
    seen, flagged, control, binder = set(), [], 0, set()
    for line in sys.stdin:
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("kind") != "Event":
            continue
        name = event.get("objectRef", {}).get("name")
        failure = event.get("annotations", {}).get(KEY)
        if name in names:
            seen.add(name)
            if failure:
                flagged.append((name, event["verb"], failure))
        user = event.get("user", {}).get("username", "")
        ref = event.get("objectRef", {})
        if (
            name in names
            and user.endswith(":persistent-volume-binder")
            and ref.get("resource") == "persistentvolumes"
            and not ref.get("subresource")
            and event["verb"] in ("update", "patch")
        ):
            binder.add(name)
        if name == "sites" and failure and "nfs-persistent-volumes" in failure:
            control += 1
    print(
        f"audit: {len(seen)} of {len(names)} objects of the shares in the log, {len(flagged)} with an annotation"
    )
    print(f"audit: the binder updated {len(binder)} volumes of the shares")
    print(f"audit: the volume off the list has {control} annotations")
    for item in flagged:
        print("audit: flagged", *item)
    volumes = {
        d["metadata"]["name"] for d in json.loads((work / "volumes.json").read_text())
    }
    return (
        0 if seen == names and binder == volumes and not flagged and control > 0 else 1
    )


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
