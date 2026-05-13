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
provisions the node containers itself with a fixed config. Two options to
get USB through:

### Option A (preferred): post-create container update

After `task bootstrap:wsl` brings the cluster up, the node containers exist
under names like `homelab-wsl-controlplane-1`, `homelab-wsl-worker-1`, etc.

Stop the worker that will host the USB workloads, recreate it with the
`--device` flags pointing at the host USB devices, and restart:

```bash
# Find the worker that should claim the USB devices
docker ps --filter name=homelab-wsl-worker --format '{{.Names}}'

# Inspect existing config (so you can replicate it)
docker inspect homelab-wsl-worker-1 > /tmp/worker-1.json

# Stop it
docker stop homelab-wsl-worker-1

# Recreate with device passthrough
# TBD: exact command — fill in once first hardware boot validates the
# minimum set of flags needed. The known starting point is:
#   docker run -d --privileged \
#     --device=/dev/bus/usb \
#     --device=/dev/serial/by-id/usb-0658_0200-if00 \
#     <other flags from inspect>
```

### Option B: cluster-create wrapper script

Write a wrapper that runs `talosctl cluster create`, sleeps until the
node containers exist, then calls `docker update` to add device mappings.
**TBD** — not implemented yet. Document the exact command set here once
proven on hardware.

### Option C: privileged containers + bind mount of `/dev`

Talos's Docker provisioner supports `--with-init-node` and arbitrary
container args. Worth investigating whether `--user-disk` or a custom
machine-config patch can mount the host's `/dev/bus/usb` into the node
filesystem. **TBD.**

> **Honest status:** none of options A/B/C have been validated end-to-end on
> hardware yet. The first time Ivan plugs the dongles in, fill in the exact
> commands here.

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

`kubernetes/apps/home-automation/zwave-js-ui/app/helmrelease.yaml`:

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

> The `usb-0658_0200-if00` symlink is created by udev rules on Linux. On
> Talos the path may differ — verify with
> `talosctl -n <node> ls /dev/serial/by-id` after Step 2.

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
