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

> **Don't do the attach by hand on every reboot — WSL owns it now.**
> `usbipd bind` is persistent but `usbipd attach --wsl` is per-session, so a
> Windows reboot, `wsl --shutdown`, or a WSL-only crash (dockerd bounce, VM
> OOM) drops both dongles out of WSL and the frigate / zwave-js-ui pods break.
>
> The recovery is a **systemd service inside WSL**, one instance per dongle
> (`tnwks-usb-attach@<vid:pid>`), installed by Ansible. Each runs
> `usbipd.exe attach --wsl --auto-attach --hardware-id <VID:PID>` over interop
> and is `enabled` (`WantedBy=multi-user.target`) so it fires on **every WSL
> boot** — including a WSL-only restart, which a Windows-logon-keyed Scheduled
> Task would miss. `--auto-attach` re-attaches on replug (covers the Coral
> firmware flip), and `Restart=always` covers the interop child exiting.
>
> This inverts the old model: **the Windows side now only needs to start WSL**
> (logon autostart / a one-line task). Everything else lives in WSL. Install
> or re-run via the bootstrap task:
>
> ```bash
> task bootstrap:wsl-usb-attach          # idempotent; also part of `task bootstrap:wsl`
> ```
>
> **One-time Windows prerequisite (elevated):** each dongle must be
> `usbipd bind`-ed once so it's shareable and the binding persists
> (`attach`, which the service does repeatedly, needs no elevation):
>
> ```powershell
> usbipd bind --force --hardware-id 0658:0200   # Z-Stick
> usbipd bind --force --hardware-id 18d1:9302   # Coral (runtime VID — see gotcha)
> ```
>
> The old `Register-TnwksUsbAttachTask.ps1` / `Sync-TnwksUsbAttach.ps1`
> Scheduled-Task approach is superseded by this and can be removed. The Coral
> firmware flip still needs a manual nudge in the worst case (see the gotcha
> below).

> **Coral gotcha — bind after firmware loads, not before.** When the
> Coral first appears on Windows it shows VID/PID `1a6e:089a` (DFU
> bootloader). Windows itself completes the firmware load, after which
> `usbipd list` flips it to `18d1:9302` (Google runtime). **You must
> bind/attach at the runtime VID.** If you bind/attach while it's still
> at `1a6e:089a`, `attach --wsl` causes a USB reset that sends the
> device back into bootloader mode, and nothing inside WSL/Talos has
> the Edge TPU runtime to upload firmware again — `libedgetpu` in the
> Frigate pod will fail with `Failed to load delegate from
> libedgetpu.so.1.0`. If you get stuck in this state:
>
> ```powershell
> usbipd detach --busid 1-19
> usbipd list   # wait for it to show 18d1:9302
> usbipd bind --busid 1-19 --force
> usbipd attach --wsl --busid 1-19
> ```

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
provisions the node containers itself with a fixed config. The bootstrap
playbook handles this declaratively: after `talosctl cluster create`
runs, it inspects worker-2, captures its named volumes + env (which hold
the base64 machine config in `USERDATA`), and recreates the container
via `community.docker.docker_container` with the USB passthrough and
`/mnt/e/k8s-storage` bind-mount layered on top.

Both dongles come through as **`rslave` bind mounts** (not `--device`), so
host re-enumeration — and a dongle attached *after* the container started —
propagates the live node in. The Coral binds `/dev/bus/usb`; the Z-Stick
binds the whole host `/dev` tree at a dedicated `/hostdev` mountpoint (its
CDC tty's only parent dir is `/dev` itself). See the `--device` caveat below.

```bash
cd infrastructure
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/01-talos-bootstrap.yml
```

The augmentation step is idempotent — re-runs are a no-op once
worker-2 already has the devices and bind-mount. It also skips
gracefully on hosts where the USB devices haven't been attached yet
(see Step 1) or where `/mnt/e` isn't mounted, leaving worker-2 at the
stock `talosctl` config. PVC data, kubelet state, and the machine
config carry over because Talos uses **named volumes** on the node
container for `/var`, `/usr/libexec/kubernetes`, `/opt`,
`/system/state`, `/etc/cni`, and `/etc/kubernetes` — these survive
`docker rm` and are re-attached on the replacement container.

The playbook drains worker-2 first so pods reschedule cleanly. Anything
backed by `local-path` on this worker stays put (its PVC data is on the
named volume); drain just relocates pods that can move.

### Verify the device made it through

```bash
talosctl -n 10.5.0.4 ls /dev/bus/usb        # Coral: should list 001, 002 ...
talosctl -n 10.5.0.4 ls /hostdev | grep ttyACM  # Z-Stick lands here
```

> **Why `/hostdev/ttyACM0` and not `/dev/serial/by-id/...`?** Talos
> doesn't populate udev's `/dev/serial/by-id/` symlinks, so the Z-Stick
> arrives as the bare `ttyACM0` node. worker-2 bind-mounts the host's
> `/dev` at `/hostdev`, so the device is at `/hostdev/ttyACM0` inside the
> container. The WSL overlay
> (`kubernetes/clusters/wsl/apps/home-automation/zwave-js-ui-ks.yaml`)
> patches the zwave-js-ui hostPath accordingly. The MS-01 cluster keeps
> the upstream by-id path because udev runs there.

> **`--device` is a snapshot, not a live mount.** Docker's `--device`
> flag captures the host's device-node major/minor at container start
> and cgroup-allows that exact one. If the device isn't present yet at
> container start (attached later), or disconnects/reconnects (USB reset,
> suspend, replug, usbipd `detach`/`attach`), the container is left with
> no node or a stale one. The pod then can't open the device:
> `libedgetpu` fails to load, or the kubelet rejects the zwave hostPath
> with `/dev/ttyACM0 is not a character device`.
>
> **Both dongles avoid this with `rslave` bind mounts** instead of
> `--device`, so nodes created on the host propagate into the container
> live. `privileged: true` (already set on the worker container) grants
> the device-cgroup access the bind mounts need, and the pod-side
> `hostPath` mounts carry the matching `mountPropagation: HostToContainer`
> so the live node reaches the app container too.
>
> - **Coral** binds `/dev/bus/usb` directly — `libedgetpu` speaks raw
>   usbfs nodes and the subtree is self-contained, so new
>   `/dev/bus/usb/<bus>/<dev>` nodes from a firmware-flip re-enumeration
>   appear in the pod and Frigate finds the current `18d1:9302`.
> - **Z-Stick** binds the whole host `/dev` at **`/hostdev`** (a dedicated
>   mountpoint, *not* over the container's own `/dev`). zwave-js-ui opens a
>   CDC tty whose only parent dir is `/dev` itself; overlaying the
>   container's `/dev` would shadow Talos's `/dev/pts`, `/dev/shm` and
>   submounts and break the node, so it gets its own path. The live
>   `/hostdev/ttyACM0` appears even when the Z-Stick is attached after
>   worker-2 started — which is exactly the host-reboot ordering that used
>   to wedge the pod.

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
        mountPropagation: HostToContainer # pick up Coral re-enum live
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

The WSL overlay patches this to `/hostdev/ttyACM0` (the host `/dev` tree is
bind-mounted into worker-2 at `/hostdev`) and pins the pod to
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

# Talos node container (replace <node> with the node name).
# On WSL the Z-Stick is under /hostdev; on MS-01 it's /dev/serial/by-id.
talosctl -n <node> ls /dev/bus/usb
talosctl -n <node> ls /hostdev | grep ttyACM    # WSL
talosctl -n <node> ls /dev/serial/by-id         # MS-01

# Pod
kubectl exec -n production deploy/frigate -- ls /dev/bus/usb
kubectl exec -n home-automation deploy/zwave-js-ui -- \
  ls -l /hostdev/ttyACM0                          # WSL (by-id on MS-01)
```

If any layer doesn't see the device, fix that layer before going up.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `usbipd list` shows device but `lsusb` in WSL is empty | Attach service not running (or `vhci_hcd` not loaded) | `systemctl status 'tnwks-usb-attach@*'`; if absent, `task bootstrap:wsl-usb-attach` |
| Devices drop out after every WSL/Windows restart | `bind` is persistent; `attach` is per-session, and the WSL attach service isn't installed/enabled | `task bootstrap:wsl-usb-attach` (then confirm `systemctl is-enabled 'tnwks-usb-attach@0658:0200'`) |
| Pod crashes with "no such device" / zwave "is not a character device" | worker-2 container doesn't see the live node (stale `--device` snapshot, or attached after container start) | Re-run `task bootstrap:wsl` to recreate worker-2 with the `rslave` binds (Step 2) |
| Coral throws permission errors / `Failed to load delegate` | Coral firmware flip kicked it back to the Windows host at `18d1:9302` | `--auto-attach` should re-grab it; if stuck, `systemctl restart 'tnwks-usb-attach@18d1:9302'` or manually re-bind/attach the runtime VID (see Coral gotcha) |
| Z-Wave controller "stuck" | wrong CDC path in hostPath | `talosctl -n <node> ls /hostdev \| grep ttyACM` (WSL) and confirm the overlay path |

## References

- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [WSL2 USB support](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [Talos device passthrough discussions](https://www.talos.dev/v1.7/talos-guides/configuration/disk-management/)
- Frigate Coral docs: https://docs.frigate.video/configuration/object_detectors
