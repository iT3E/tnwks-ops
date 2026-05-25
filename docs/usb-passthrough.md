# USB Passthrough: Coral TPU and Z-Wave Stick

How USB devices physically attached to the workstation get exposed to pods
running in the WSL Talos cluster.

The chain has four hops:

```
Windows host  ──►  WSL2  ──►  Docker (Talos node container)  ──►  k8s pod
   usbipd-win    /dev/bus/usb        --device / --privileged       hostPath / privileged
```

Each hop has to be set up the first time and re-attached after every Windows
reboot or WSL restart.

On the **MS-01 cluster** none of this is needed — the dongles plug directly
into one of the MS-01s and Talos exposes them as `/dev/bus/usb/...`. Pods
claim them via the same `securityContext: privileged` / `hostPath` patterns
already in the HelmReleases. See [Step 3](#step-3-talos-container--kubernetes-pod) below.

---

## Step 1. Windows host → WSL2 (`usbipd-win`)

Install once on the Windows host:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Reboot if prompted (the kernel driver loads at startup).

### Identify devices

In an **admin** PowerShell:

```powershell
usbipd list
```

Look for:

| Device | Vendor:Product (typical) | Notes |
|---|---|---|
| Google Coral TPU | `1a6e:089a` (uninitialized) / `18d1:9302` (after first run) | Vendor ID flips after the device's firmware loads |
| Aeotec Z-Stick Gen5 / Gen5+ | `0658:0200` | Sigma Designs CDC ACM |

Note the BUSID column — looks like `2-3` or `4-1`. That's what you bind/attach by.

### Bind once, attach every session

```powershell
# One-time per device:
usbipd bind --busid 2-3
usbipd bind --busid 4-1

# Every time WSL is restarted:
usbipd attach --wsl --busid 2-3
usbipd attach --wsl --busid 4-1
```

### Verify in WSL

```bash
lsusb | grep -E "Coral|Aeotec|Sigma|Google"
ls -l /dev/bus/usb/*/*
ls -l /dev/serial/by-id/   # Z-Stick should appear here
```

If `lsusb` doesn't show the device, restart `wsl --shutdown` from PowerShell
and re-attach.

---

## Step 2. WSL2 → Docker (Talos node container)

`talosctl cluster create` does not accept `--device` flags directly — Talos
provisions the node containers itself with a fixed config. The validated
workflow is to **stop, remove, and re-`docker run`** the chosen worker
with `--device` flags pointing at the host USB devices.

PVC data is safe: Talos uses six **named volumes** on the node container
for `/var`, `/usr/libexec/kubernetes`, `/opt`, `/system/state`, `/etc/cni`
and `/etc/kubernetes`. Named volumes survive `docker rm` — re-mount the
same volume names on the replacement container and local-path PVCs,
kubelet state, and the machine config all carry over.

> Drain the node first (`kubectl drain --delete-emptydir-data
> --ignore-daemonsets`) so pods reschedule cleanly. Anything backed by
> local-path on this worker stays put — drain just relocates pods that
> can move.

```bash
NODE=homelab-wsl-worker-2

# Drain the node
kubectl cordon "$NODE"
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --force --timeout=120s

# Capture current container config (volumes, env, network IP, labels, etc.)
docker inspect "$NODE" > /tmp/$NODE.json

# Pull volume names + env (USERDATA contains the full base64 machine config)
python3 -c "
import json
d = json.load(open('/tmp/$NODE.json'))[0]
print('Volumes:')
for m in d['Mounts']:
    if m['Type'] == 'volume':
        print(f'  -v {m[\"Name\"]}:{m[\"Destination\"]}')
print()
print('Network IP:', d['NetworkSettings']['Networks']['homelab-wsl']['IPAMConfig']['IPv4Address'])
print('Image:', d['Config']['Image'])
"
```

Then stop, remove, and recreate. Substitute the six volume mount lines
from the inspect dump above:

```bash
docker stop "$NODE" && docker rm "$NODE"

docker run -d \
  --name "$NODE" --hostname "$NODE" \
  --privileged --read-only --restart no \
  --shm-size 67108864 --memory 8589934592 --cpus 4 \
  --security-opt seccomp=unconfined --security-opt label=disable \
  --sysctl net.ipv6.conf.all.disable_ipv6=0 \
  --network homelab-wsl --ip 10.5.0.4 \
  --label org.opencontainers.image.source=https://github.com/siderolabs/talos \
  --label talos.cluster.name=homelab-wsl \
  --label talos.owned=true --label talos.type=worker \
  --tmpfs /run --tmpfs /system --tmpfs /tmp \
  -v <var-volume>:/var \
  -v <kubelet-volume>:/usr/libexec/kubernetes \
  -v <opt-volume>:/opt \
  -v <state-volume>:/system/state \
  -v <cni-volume>:/etc/cni \
  -v <k8s-volume>:/etc/kubernetes \
  --device=/dev/bus/usb \
  --device=/dev/serial/by-id/usb-0658_0200-if00 \
  -e PLATFORM=container -e TALOSSKU=4CPU-8192RAM \
  -e PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  -e USERDATA="<base64 from original inspect>" \
  ghcr.io/siderolabs/talos:v1.13.2

# Wait for node to come back Ready
kubectl get node "$NODE" -w
kubectl uncordon "$NODE"
```

### Verify the device made it through

```bash
talosctl -n 10.5.0.4 ls /dev/bus/usb       # should list 001, 002 ...
talosctl -n 10.5.0.4 ls /dev | grep ttyACM # Z-Stick lands here
```

> **Why `/dev/ttyACM0` and not `/dev/serial/by-id/...`?** Talos doesn't
> populate udev's `/dev/serial/by-id/` symlinks. Docker resolves the
> host symlink at container start, so the device node arrives as
> `/dev/ttyACM0` inside the Talos container. The WSL overlay
> (`kubernetes/clusters/wsl/apps/home-automation/zwave-js-ui-ks.yaml`)
> patches the zwave-js-ui hostPath accordingly. The MS-01 cluster keeps
> the upstream by-id path because udev runs there.

---

## Step 3. Talos container → Kubernetes pod

Once `/dev/bus/usb` (and/or `/dev/serial/by-id/...`) is available inside
the Talos node container, the pod-side configuration is already in the
HelmRelease values.

### Frigate (Coral TPU)

`kubernetes/apps/production/frigate/app/helmrelease.yaml`:

```yaml
securityContext:
  privileged: true                # already set
persistence:
  usb:
    enabled: true
    type: hostPath
    hostPath: /dev/bus/usb        # already set
    hostPathType: Directory
    globalMounts:
      - path: /dev/bus/usb
```

### zwave-js-ui (Z-Stick)

`kubernetes/apps/home-automation/zwave-js-ui/app/helmrelease.yaml` (MS-01):

```yaml
securityContext:
  privileged: true                # already set
persistence:
  usb:
    enabled: true
    type: hostPath
    hostPath: /dev/serial/by-id/usb-0658_0200-if00   # Z-Stick CDC device
    hostPathType: CharDevice
```

The WSL overlay patches this to `/dev/ttyACM0` and pins the pod to
`homelab-wsl-worker-2`:

```
kubernetes/clusters/wsl/apps/home-automation/zwave-js-ui-ks.yaml
```

### Per-cluster nodeSelector divergence

The MS-01 HelmReleases use NFD labels (frigate: `coral=true`) and
hostnames (`sce-uk8sw03`) that don't apply on the WSL cluster. Each WSL
overlay carries a `kustomize.toolkit.fluxcd.io/v1` Kustomization wrapper
with `spec.patches` that overrides `defaultPodOptions.nodeSelector` to
pin to `homelab-wsl-worker-2`. See:

- `kubernetes/clusters/wsl/apps/production/frigate-ks.yaml`
- `kubernetes/clusters/wsl/apps/home-automation/zwave-js-ui-ks.yaml`

### DaemonSet alternative

If `securityContext: privileged` is too coarse for prod policy, a device
plugin like [`smarter-device-manager`](https://gitlab.com/arm-research/smarter/smarter-device-manager)
can advertise `/dev/bus/usb/*` as schedulable resources. Not currently
deployed.

---

## Verification commands

Run these top-down to debug:

```bash
# Windows host
usbipd list                                    # device should be 'Attached'

# WSL2
lsusb | grep -E "Coral|Aeotec|Sigma|Google"
ls /dev/bus/usb/*/

# Talos node container (replace <node> with the node name)
talosctl -n <node> ls /dev/bus/usb
talosctl -n <node> ls /dev/serial/by-id

# Pod
kubectl exec -n production deploy/frigate -- ls /dev/bus/usb
kubectl exec -n home-automation deploy/zwave-js-ui -- \
  ls -l /dev/serial/by-id
```

If any layer doesn't see the device, fix that layer before going up.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `usbipd list` shows device but `lsusb` in WSL is empty | Forgot `attach` after WSL restart | `usbipd attach --wsl --busid X-Y` |
| Device disappears after Windows reboot | `bind` is persistent; `attach` is per-session | Re-run `attach` in startup script |
| Pod crashes with "no such device" | Talos node container doesn't see the device | Re-do Step 2 |
| Coral throws permission errors | Coral firmware load flips vendor:product, breaks bind | Re-bind with new busid after first run |
| Z-Wave controller "stuck" | wrong CDC path in hostPath | `talosctl -n <node> ls /dev/serial/by-id`, update the helmrelease |

## References

- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [WSL2 USB support](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [Talos device passthrough discussions](https://www.talos.dev/v1.7/talos-guides/configuration/disk-management/)
- Frigate Coral docs: https://docs.frigate.video/configuration/object_detectors
