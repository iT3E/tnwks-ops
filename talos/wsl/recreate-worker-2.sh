#!/usr/bin/env bash
set -euo pipefail

# Recreate homelab-wsl-worker-2 with USB device passthrough and the
# /mnt/e bind-mount that backs the local-path-bulk StorageClass.
#
# Why this script exists: `talosctl cluster create` provisions Talos node
# containers with a fixed config — no way to add --device or extra -v
# flags. The validated workflow is to inspect the existing container,
# capture its named volumes + env (six volumes hold /var, kubelet state,
# /opt, /system/state, /etc/cni, /etc/kubernetes), then docker rm + run
# with the same volumes plus the extra device/mount flags. Named volumes
# survive `docker rm`, so PVC data, kubelet state, and the machine
# config all carry over.
#
# See docs/usb-passthrough.md for the full chain
# (Windows host → usbipd → WSL → Docker → Talos → pod).
#
# Idempotent: running this against an already-recreated worker just
# reapplies the same flags. Drains the node first so pods relocate
# cleanly; uncordons at the end.

NAME=homelab-wsl-worker-2
IMAGE=ghcr.io/siderolabs/talos:v1.13.2
NETWORK=homelab-wsl
IP=10.5.0.4

# USB devices passed through from the WSL host. Coral TPU lands on the
# whole /dev/bus/usb tree (its busid changes when firmware reloads); the
# Z-Stick is a stable serial-by-id symlink.
USB_DEVICES=(
  --device=/dev/bus/usb
  --device=/dev/serial/by-id/usb-0658_0200-if00
)

# Bulk storage backing for local-path-bulk StorageClass: Windows E:
# drive surfaced into WSL via 9p drvfs at /mnt/e, then bind-mounted
# straight into the Talos container so the local-path-provisioner
# helper pod can mkdir under it.
EXTRA_MOUNTS=(
  -v /mnt/e/k8s-storage:/mnt/e/k8s-storage
)

if ! docker inspect "$NAME" >/dev/null 2>&1; then
  echo "!! container $NAME not found — bring the cluster up first" >&2
  exit 1
fi

echo ">> draining $NAME"
kubectl cordon "$NAME"
kubectl drain "$NAME" --ignore-daemonsets --delete-emptydir-data --force --timeout=120s || true

echo ">> capturing current container config"
SNAPSHOT=$(docker inspect "$NAME")

# Build -v flags from the existing named volumes (preserves PVC data
# and Talos state across the recreate).
VOLUME_ARGS=$(echo "$SNAPSHOT" | python3 -c '
import json, sys
d = json.load(sys.stdin)[0]
for m in d["Mounts"]:
    if m["Type"] == "volume":
        name = m["Name"]
        dest = m["Destination"]
        print("-v " + name + ":" + dest)
')

# USERDATA is the base64-encoded machine config — must be preserved
# verbatim, otherwise the node forgets its identity and re-bootstraps.
read -r PLATFORM TALOSSKU TPATH USERDATA < <(echo "$SNAPSHOT" | python3 -c '
import json, sys
d = json.load(sys.stdin)[0]
env = {x.split("=",1)[0]: x.split("=",1)[1] for x in d["Config"]["Env"]}
print(env["PLATFORM"], env["TALOSSKU"], env["PATH"], env["USERDATA"])
')

echo ">> stopping $NAME"
docker stop "$NAME"
echo ">> removing $NAME"
docker rm "$NAME"

echo ">> recreating $NAME with USB + bulk-storage mounts"
# shellcheck disable=SC2086 # word-splitting on $VOLUME_ARGS is intentional
docker run -d \
  --name "$NAME" \
  --hostname "$NAME" \
  --privileged \
  --read-only \
  --restart no \
  --shm-size 67108864 \
  --memory 8589934592 \
  --cpus 4 \
  --security-opt seccomp=unconfined \
  --security-opt label=disable \
  --sysctl net.ipv6.conf.all.disable_ipv6=0 \
  --network "$NETWORK" \
  --ip "$IP" \
  --label org.opencontainers.image.source=https://github.com/siderolabs/talos \
  --label talos.cluster.name=homelab-wsl \
  --label talos.owned=true \
  --label talos.type=worker \
  --tmpfs /run \
  --tmpfs /system \
  --tmpfs /tmp \
  $VOLUME_ARGS \
  "${USB_DEVICES[@]}" \
  "${EXTRA_MOUNTS[@]}" \
  -e "PLATFORM=$PLATFORM" \
  -e "TALOSSKU=$TALOSSKU" \
  -e "PATH=$TPATH" \
  -e "USERDATA=$USERDATA" \
  "$IMAGE"

echo ">> waiting for $NAME to come Ready"
kubectl wait --for=condition=Ready "node/$NAME" --timeout=180s
kubectl uncordon "$NAME"

echo ">> done"
docker ps --filter name="$NAME" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
