# Dependency compatibility reviews

Some newer dependency releases cannot run with their consumer yet. These
updates wait under **Compatibility review** in the [Renovate Dashboard](https://github.com/iT3E/tnwks-ops/issues/43),
using `dependencyDashboardApproval`. There is no `allowedVersions` ceiling
or disabled package: newer releases remain discoverable, and checking the
dashboard entry creates a normal reviewed PR. Supported patch updates
continue to open PRs.

- **Windows Coral / NumPy.** The Windows service still uses the Edge TPU
  backend, `tflite_runtime 2.5.0.post1` for CPython 3.9, and the NumPy 1.x
  ABI. NumPy majors wait for approval. A future migration must update
  `requirements.in` and regenerate `requirements.txt` together, and test
  delegate loading and real inference. Replacing the detector backend is
  also possible; the ZMQ protocol does not depend on the runtime.
- **UniFi / MongoDB.** MongoDB minor and major changes wait for approval;
  8.0.x patches continue normally. LinuxServer currently documents support
  through MongoDB 8.0. Recheck support against the deployed UniFi version
  before approving a different database release line. Database majors keep
  the existing `impact/critical` approval and backup requirements.

## How compatibility gets revisited

`.github/workflows/dependency-compatibility.yaml` checks weekly and on
manual dispatch. It watches:

- the vendor's MongoDB support paragraph and the UniFi image pinned here;
- official Coral Python and Edge TPU releases, including assets added or
  replaced on an existing release;
- this repo's Coral backend, Python, TFLite runtime selection, and per-host
  Coral overrides.

A changed signal opens one assigned review issue per compatibility group.
Further evidence changes update that issue and post one notification;
unchanged weekly runs do not post anything. An unreadable source also
requests review. The issue is a request to check support, not a claim that
the new combination works. UniFi image PRs carry a reminder as well.

There is a periodic fallback even if abandoned upstream projects never
change: Coral is reviewed every 30 days and UniFi every 90 days. Review the
support matrix and test a proposed migration before recording a new
baseline:

```bash
python3 .github/scripts/dependency-compatibility.py --accept-reviewed-baseline
```

Commit the resulting `.github/dependency-compatibility.json` in a PR. This
updates the reviewed evidence and next review date; it does not approve a
dependency upgrade. The next scheduled check closes the matching review
issue when its evidence matches. Closing an issue without reviewing and
updating its baseline causes the next run to reopen it.

## Security stays visible

GitHub vulnerability alerts remain enabled. Renovate's
`vulnerabilityAlerts.dependencyDashboardApproval: false` makes security-fix
PRs bypass these routine compatibility gates. They still need CI and impact
review, so an incompatible security fix is investigated rather than
automerged. OSV vulnerability detection and the dashboard's full OSV
summary are enabled, including advisories without an available fix.
These features only cover the dependencies supported by those feeds; they
are not a guarantee that the whole runtime has no vulnerabilities.

The current Coral stack already uses end-of-life Python 3.9 and an old
TFLite binary. Moving its NumPy major update to the dashboard neither
changes those installed versions nor resolves that existing security debt.
Keep the service's restricted bind address, firewall, unprivileged account,
and disabled model upload. A security fix that needs a newer runtime or
another backend takes priority over keeping the old stack.

## The Coral shim's pinned toolchain

Frigate reaches the Windows service over ZMQ. The service then reaches the
USB Coral through `tflite_runtime`, `edgetpu.dll`, and the libusb shim; the
network hop does not remove the shim. Its installed checksum matches
`coral_detector_shim_sha256`. The shim avoids UsbDk, which conflicts with
the Z-Stick's usbipd path; see [Coral detector](../wsl/coral-detector.md).

The controller play builds that shim inside a pinned toolchain image
(`shim/Dockerfile`: digest-pinned Ubuntu 24.04, mingw-w64 from a fixed
Ubuntu snapshot) and asserts its pinned output hash. The runner's own OS no
longer matters: Ubuntu 26.04's MinGW would produce different bytes, but it
isn't used. A toolchain move (new snapshot or Ubuntu release) changes the
DLL, so it is a deliberate PR that re-pins the hash and is verified on the
detector. Deleting the shim or only changing its expected hash would break
that contract.

References: [LinuxServer's UniFi database support](https://docs.linuxserver.io/images/docker-unifi-network-application/#setting-up-your-external-database),
[NumPy's ABI change](https://numpy.org/doc/stable/release/2.0.0-notes.html),
[Python lifecycle](https://devguide.python.org/versions/), and
[Renovate approval and vulnerability options](https://docs.renovatebot.com/configuration-options/).
