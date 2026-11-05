# Coral detector on the Windows host

Frigate (`production/frigate` on `homelab-wsl`) can send object detection to
Coral USB Accelerators plugged into the Windows host DESKTOP-HKE05KI, over
Frigate's built-in `zmq` detector. Each Coral is served by its own Windows
service, `tnwks-coral-detector-<id>`, which Ansible manages from this repo
(role `infrastructure/ansible/roles/windows_coral_detector`, server code in
`infrastructure/ansible/roles/windows_coral_detector/files/coral-detector/`).

Status (2026-11-03): one Coral, service `tnwks-coral-detector-0`. Frigate
detects on it since PR #1682 (detector `coral0`, type `zmq`); see
[Cutover and rollback](#cutover-and-rollback) for going back.

## Why on Windows

Through usbip (the old path, [usb-passthrough.md](usb-passthrough.md)) the
Coral hung Frigate's detector every 3-4 days: the WSL kernel's `vhci_hcd`
stores the URB unlink sequence number without a 32-bit cast, so after about
2^31 URBs (~8k/s from the Coral) every unlink fails. Nothing short of
reloading the module recovers it, and the bug is still unfixed upstream.
Running the Coral natively on Windows removes usbip from the path. The
Z-Stick stays on usbip; its traffic is tiny.

## Architecture

```
frigate pod (worker-2)                 Windows host DESKTOP-HKE05KI
  detectors.coral: type zmq     tcp      tnwks-coral-detector-0 (WinSW service)
  endpoint tcp://172.26.80.1:5555 ───►    python -m tnwks_detector
                                           ├─ zmq REP  172.26.80.1:5555
  Prometheus  ──────────────────────►     ├─ /metrics 172.26.80.1:9555
                                           └─ backend edgetpu: tflite_runtime
                                              + edgetpu.dll + libusb shim ─► Coral (WinUSB)
```

- **Protocol.** Frigate 0.18's `frigate/detectors/plugins/zmq_ipc.py`: a REQ
  socket sends `[header json, tensor bytes]` and expects 20x6 float32 rows
  `[class, score, ymin, xmin, ymax, xmax]`; a `model_request` handshake comes
  first. The server answers every request, errors included (all zeros),
  because a REP socket that misses one reply is wedged.
- **Backends.** `tnwks_detector/backends/` has a small interface
  (`open()`, `load(model) -> ModelInfo`, `detect(tensor) -> (20, 6)`,
  `close()`). Only `edgetpu` exists. Another accelerator (ONNX Runtime on
  the desktop GPU, Hailo, MemryX) is a new module there plus its packages
  in `requirements.in`; the service, ports, metrics and the Ansible role stay
  the same (`coral_detector_backend`).
- **Address.** Everything binds the vEthernet (WSL) host address
  (`WINDOWS_HOST_IP`, 172.26.80.1), never the LAN. The firewall rules
  `tnwks-coral-detector zmq (WSL)` (tcp 5555-5558) and
  `tnwks-coral-detector metrics (WSL)` (tcp 9555-9558) admit only the WSL
  subnet. That adapter exists only while WSL runs, so after a boot the server
  waits for the address, and if the address disappears it exits so the
  service manager restarts it.
- **Service.** WinSW 2.12 (`WinSW.NET461.exe`, one renamed copy per
  instance with its XML next to it) runs the venv's Python. The service runs
  as its virtual account `NT SERVICE\tnwks-coral-detector-<id>`, which can
  open the Coral through WinUSB, so it needs no LocalSystem. It restarts on
  failure (5s, 15s, then 60s), WinSW rolls the logs in
  `C:\ProgramData\tnwks-coral-detector\logs\<id>\` (5 x 10 MB), and a stop
  sends Ctrl+C so the server releases the Coral cleanly. The server also
  exits (and gets restarted) when one inference hangs for 10s or 100 in a
  row fail.
- **Latency.** A background process on Windows 11 gets EcoQoS (E-cores, low
  clocks), which added 20-50ms of tail latency in the spike. The server opts
  out (`SetProcessInformation(ProcessPowerThrottling)`), runs at HIGH
  priority and asks for a 1ms timer.
- **Files.** `C:\ProgramData\tnwks-coral-detector\`: `bin` (WinSW copies,
  uv, `edgetpu.dll`, the libusb shim and the real libusb), `app`
  (`tnwks_detector`), `venv`, `python` (uv-managed 3.9), `models`, `driver`,
  `logs`. Inheritance from ProgramData is off: Users and the service accounts
  can read, only Administrators and SYSTEM can write, and each service can
  write only its own `logs\<id>`.

### Runtime pieces and why

- **Python 3.9.** The only Windows `tflite_runtime` with the Edge TPU
  delegate is `2.5.0.post1` cp39 (google-coral/pycoral v2.0.0). Newer
  tflite_runtime dropped the delegate, and the community forks ship no Windows
  builds. Python 3.9 is past end of life: an accepted risk for a process that
  only listens on the WSL subnet. `numpy<2` because that wheel is built
  against the numpy 1.x ABI. All packages are hash-pinned in
  `requirements.txt` and installed with `uv pip sync --require-hashes`.
- **edgetpu.dll** comes from `edgetpu_runtime_20221024.zip`
  (`libedgetpu/direct`, ~16ms per inference against 19.5ms for `throttled`).
- **Driver.** Only Google's WinUSB INFs, `Coral_USB_Accelerator.inf` and
  `Coral_USB_Accelerator_(DFU).inf`, with their signer (Google LLC, expired
  2021 but timestamped) in `LocalMachine\TrustedPublisher` so `pnputil`
  installs them without a prompt. **Never UsbDk** (the runtime's
  `install.bat` installs it): usbipd-win flags UsbDk as incompatible, and the
  Z-Stick depends on usbipd.
- **The libusb shim** (`coral-detector/shim/`). libedgetpu on Windows calls
  `libusb_set_option(USE_USBDK)` and treats a failure as fatal, so without
  UsbDk it finds no device. The shim is a `libusb-1.0.dll` that returns
  success for that one option and forwards every other export to the real
  libusb 1.0.30 (VS2022/MS64), installed next to it as `libusbreal.dll`
  (1.0.30 also cut the delegate load from 15s to 2s against the bundled
  1.0.26). `gen-def.sh` writes the export list from the real DLL;
  `build.sh` builds it with mingw-w64 on the controller. The build is
  reproducible (no PE timestamp, fixed image base), so the role only copies
  a new shim when its source changes. After a libusb bump, regenerate the
  `.def` and update `coral_detector_libusb_*` in the role defaults.

## Several Corals

One service per Coral. Add an entry to `coral_detector_devices` in
the host's `infrastructure/ansible/inventory/host_vars/<host>/main.yml`:

```yaml
coral_detector_devices:
  - { id: 0, device: "usb:0" }
  - { id: 1, device: "usb:1" } # service tnwks-coral-detector-1, ports 5556 / 9556
```

and add a second zmq detector to Frigate (`endpoint: tcp://172.26.80.1:5556`)
plus a second scrape target. The firewall rules already cover ports up to
`base + 3`. Instances removed from the list get their service, wrapper and
event-log source removed.

- **Device selection.** `usb:<n>` is the n-th Edge TPU in libedgetpu's
  enumeration order, which follows the USB topology, so it changes when a
  Coral moves to another port or one is missing. For a stable mapping, use
  the device path instead: the server lists the Corals with
  `edgetpu_list_devices` and resolves a path like
  `/sys/bus/usb/devices/1-19` (libusb's bus-port, the same name on Windows)
  to its index. Each instance logs `opened Edge TPU usb:N at <path>` and
  exports it as `tnwks_detector_info{device_path=...}`, which is where to
  read the paths once both are plugged in. A path stays valid while the
  Coral stays in that port; if it moves, the service logs `no Edge TPU at
<path> (found: ...)` and `tnwks_detector_device_ok` stays 0. Whether a
  freshly powered Coral (DFU mode, `1a6e:089a`, before its first flash)
  appears under the same path is untested; `usb:<n>` works in that state.
- **USB ports.** Give each Coral its own USB 3 port, ideally on separate root
  ports or controllers, and avoid shared hubs: each one moves ~8k URBs/s at full
  rate, and the runtime falls back to USB 2 speed on a weak link.
- **Capacity.** One Coral on Windows does ~15-16ms per inference, about
  62-66 inferences/s (two threads on one Coral add only ~5%). Six cameras
  already peak at ~58 detections/s, so ~10 cameras need the second Coral.

## Apply, check, verify

Prerequisites on the controller (WSL): `uv`, `sudo apt-get install -y
gcc-mingw-w64-x86-64 p7zip-full unzip`, and the sshd bootstrap from
[windows-ansible.md](windows-ansible.md).

```bash
task ansible:init             # once: the pinned controller and collections
task wsl-host:windows-check   # --check --diff, expect changed=0
task wsl-host:windows-apply
```

Only one process can hold a Coral. Stop anything else using it (a bench, the
old spike server) before the service starts.

Health:

```bash
curl -s http://172.26.80.1:9555/metrics | grep -E '^tnwks_detector_(ready|device_ok|inferences_total)'
```

`ready 1` = Coral open, model loaded, socket bound. From the frigate pod, a
REQ client that sends the model handshake and 600 (1,320,320,3) uint8
tensors measured on 2026-11-03:

| client                                                    | p50    | p90     | p99            |
| --------------------------------------------------------- | ------ | ------- | -------------- |
| WSL (not CPU-limited)                                     | 15.5ms | 17.9ms  | 24.5ms         |
| frigate pod, Frigate on OpenVINO                          | 16.1ms | 18-25ms | 30-63ms        |
| server side (`tnwks_detector_inference_duration_seconds`) | ~15ms  |         | all under 30ms |

The pod's tail is on the pod side: during the runs
homelab-wsl-worker-2 (4-CPU cap) was CFS-throttled in ~70% of periods while
OpenVINO used 1.5-2.3 cores. Re-measure after the cutover takes OpenVINO off
the CPU. The spike (a hidden process before the EcoQoS opt-out) had a p99 of
63ms; with the opt-out, 22.7ms.

Monitoring: Prometheus scrapes each instance as job `coral-detector`;
alerts `CoralDetectorDown`, `CoralDetectorNotReady` and
`CoralDetectorNoInferences` live in
`kubernetes/apps/monitoring/kube-prometheus-stack/addons/alerts/coral-detector.yaml`.
Frigate's zmq detector turns timeouts and errors into empty results without
an error, so a dead server looks like a quiet scene on Frigate's side; these
alerts are the signal for this path. Also: if Frigate starts while the
server is down, its model handshake fails and Frigate's zmq detector stays
"not ready" (all-empty results, no retry) until the frigate pod restarts.

## Cutover and rollback

**To the Coral** (a Frigate PR; validate the config with Frigate itself,
`python3 -m frigate --validate-config` in the running pod, and check that the
top-level `model:` size matches the detector's): in
`kubernetes/apps/production/frigate/app/resources/config.yml` replace
`detectors: {ov: ...}` with

```yaml
detectors:
  coral:
    type: zmq
    endpoint: tcp://172.26.80.1:5555
```

and the top-level `model:` block with the 320x320 SSD model the server
loads (`path: /edgetpu_model.tflite`, `width: 320`, `height: 320`,
`input_tensor: nhwc`, `input_pixel_format: rgb`, `labelmap_path:
/labelmap.txt`). Frigate only sends the model name in the handshake, so the
server's `edgetpu_model.tflite` must be the same file (the role pins it by
SHA-256).

**Back to OpenVINO:** revert that PR (`detectors: {ov: {type: openvino,
device: CPU}}` plus the explicit 300x300 `model:` block from PR #1679). The
detector services can keep running; they only answer requests.

**Back to the Coral in WSL (usbip):** stop and disable the services
(`Set-Service tnwks-coral-detector-0 -StartupType Disabled; Stop-Service
tnwks-coral-detector-0`), then undo the passthrough changes made for the Windows-native Coral:
move `18d1:9302` from `wsl_usb_attach_retired_devices` back to
`wsl_usb_attach_devices` (role `wsl_usb_attach`), set `usb_attach_args` to
`-CoralDfuVidPid 1a6e:089a -CoralRuntimeVidPid 18d1:9302` (role
`windows_usb_attach`), add the Coral back to `Watch-TnwksWsl.ps1`'s device
list, and `usbipd bind --force --hardware-id 18d1:9302`. The
vhci_hcd bug above comes back with it.
