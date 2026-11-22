# wazuh

The Wazuh server, step 2 of the security platform, on homelab-wsl-02 only:
one manager node, one indexer node and the dashboard, at the tag
`v5.0.0-rc2`. The agents of the hosts report to the manager, and the
converter (`../converter`) reads the state of the hosts from the indexer.

No cluster lists this app yet. It starts nothing until a pull request adds
`ks.yaml` and `../wazuh-settings/ks.yaml` to
`kubernetes/clusters/wsl-02/apps/kustomization.yaml` (see "Before the merge
that lists it").

## How it is built

Flux reads the vendor's manifests from `wazuh/wazuh-kubernetes` at the tag
(`app/gitrepository.yaml`), and this repository patches them. Two
Kustomizations (`ks.yaml`):

| Kustomization | What it applies |
| --- | --- |
| `security-wazuh` | `app/`: the GitRepository of the vendor's manifests, the certificates, the network rules, the agents Service and the configuration of the manager |
| `security-wazuh-server` | The vendor's folder `wazuh/`, without the files that `app/gitrepository.yaml` ignores, with the images by digest and the patches of `ks.yaml` |

The patches of `security-wazuh-server`:

- A node selector on each pod, a request and a memory limit on each
  container (see "Memory"), and no CPU limit, as for the other apps here.
- One indexer node with single-node discovery, which is the default of the
  indexer image. The vendor's privileged init container that sets
  `vm.max_map_count` is gone, so each pod meets the `baseline` level of the
  namespace. `node.store.allow_mmap: false` keeps the indexer off memory
  maps, so it needs no sysctl of the node.
- The indexer heap at 1536 MiB, half of its memory line.
- `plugins.content_manager.telemetry.enabled: false`: the daily ping of the
  update check service of the Content Manager is off
  ([settings](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/content-manager/configuration.md)).
- The init container `volume-mount-hack` of the indexer runs the indexer
  image in place of `busybox` with no tag, and changes the owner of the data
  volume only when it is not `101:101`.
- The certificates of `app/certificates.yaml` under the file names that the
  vendor's init containers read, through projected volumes.
- The manager reads `app/config/wazuh-manager.conf` (see "The manager").
- Volumes on `${STORAGE_CLASS_DEFAULT}`: 15 GiB for the manager and 30 GiB
  for the indexer (Design Doc 0004, section 10.1). The class enforces no
  size, so the drive of the site is the real limit.

A strategic merge patch of a list puts the init containers that it names
first. None of them reads what another one writes.

## Memory

The lines of `../README.md`, "Memory":

| Container | Request | Limit |
| --- | --- | --- |
| `wazuh-indexer` | 3 GiB | 3 GiB (heap 1536 MiB) |
| `wazuh-manager` | 1280 MiB | 1280 MiB |
| `wazuh-dashboard` | 320 MiB | 320 MiB |
| `wazuh-settings` (`../wazuh-settings`, a CronJob) | 64 MiB | 128 MiB |

The init containers run before the main container of their pod, one at a
time, each with a limit of 128 MiB or less. In Wazuh 5.0 the indexer
container also runs the Wazuh engine (the image's entrypoint starts it), so
the indexer line covers both.

## The manager

`app/config/wazuh-manager.conf`, mounted at
`/wazuh-config-mount/etc/wazuh-manager.conf`. The image copies it over the
configuration at each start, and then writes the indexer host, the node and
the cluster key from the environment of the pod. A change takes effect at
the next start of the pod. What it sets, against the schema of the manager
([reference](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/docs/ref/configuration/manager/reference.md)):

- One listener, HTTPS on 1517, for enrollment and reports of 5.x agents. No
  `<legacy>` block, so port 1514 is closed, and `legacy_enrollment no`, so
  port 1515 is closed. A 4.x agent can not connect.
- `use_password yes`: with `no`, an agent could enroll with no secret. The
  agent roles enroll with a token of the manager API.
- Vulnerability detection on. The manager reads the CVE feed from the
  indexer, not from the internet
  ([migration guide](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/docs/guide/migration/vulnerability-detection-cti-feeds.md)).
- No remote agent upgrade (`task-manager`, `upgrade_enabled no`): the agent
  roles install a pinned package.
- The cluster key in the file is not a valid key, so the schema refuses it
  and the manager does not start if the image does not write the key of
  the Secret `wazuh-cluster-key`.

## Network

`app/networkpolicy.yaml` adds what the vendor's policies do not have. The
vendor's policies stay for the traffic between the parts: the dashboard and
the manager to the indexer on 9200, the dashboard to the manager API on
55000, and the cluster port 1516 between manager pods.

| Rule | From | To |
| --- | --- | --- |
| `wazuh-dns` | The three parts | DNS of this cluster, through Cilium's DNS proxy |
| `wazuh-indexer-feed` | The indexer | `api.pre.cloud.wazuh.com:443`, the default CTI API of the Content Manager at `v5.0.0-rc2` |
| `wazuh-agents` | world, a node of either cluster (through the LAN bridge) | The manager on 1517 |
| `wazuh-indexer-readers` | The settings job and the converter | The indexer on 9200 |

The vendor's `manager-egress-external.yaml` (any address on 443) is not
applied. The manager and the dashboard have no route out of the cluster, so
neither reaches an update service. A host on site 1 needs the LAN bridge of
site 2 for port 1517 (`docs/wsl/lan-exposure.md`), which is a change of the
Windows host, not of this app.

## Certificates

`app/certificates.yaml`: cert-manager makes a CA for the server in the
cluster (`wazuh-ca`), and the CA issues a certificate for the indexer node,
the indexer admin, the client of the manager to the indexer, the agent
listener, the manager API and the dashboard. No key is in git. The indexer
reads its node DN and the admin DN from the certificates at each start, so
the DNs are ours (`O=tnwks`). Each leaf lasts 90 days and renews 30 days
before its end, and Reloader restarts the workload when its Secret changes.

The certificate of the agent listener names `${WAZUH_AGENTS_HOST}` and
`${WAZUH_AGENTS_LB_ADDR}`. An enrollment token pins the CA, so a rebuild of
the cluster, which makes a new CA, means each agent enrolls again. The
stricter later step is a CA in SOPS, as `observability-pki` has.

## Secrets

The owner makes each one as a SOPS file in this app or in
`../wazuh-settings`, and lists it in that app's `kustomization.yaml`. No
session reads them.

| Secret | Key | Content |
| --- | --- | --- |
| `indexer-credentials` | `wazuh-credentials` | The lines `WAZUH_INDEXER_ADMIN_PASSWORD=`, `WAZUH_INDEXER_KIBANASERVER_PASSWORD=`, `WAZUH_INDEXER_MANAGER_PASSWORD=` |
| `manager-credentials` | `wazuh-credentials` | `WAZUH_INDEXER_MANAGER_PASSWORD=`, `WAZUH_MANAGER_API_PASSWORD=`, `WAZUH_MANAGER_WUI_PASSWORD=` |
| `dashboard-credentials` | `wazuh-credentials` | `WAZUH_INDEXER_KIBANASERVER_PASSWORD=`, `WAZUH_MANAGER_WUI_PASSWORD=` |
| `wazuh-cluster-key` | `key` | 32 characters from `A-Z a-z 0-9` |
| `wazuh-settings-accounts` | `username`, `password` | The account user of the settings job (see "Accounts") |
| `wazuh-settings-user` | `password` | The password of `tnwks-settings` |
| `wazuh-converter-reader` | `password` | The password of `tnwks-converter` |

The password rule of the Wazuh images: 12 to 64 characters from
`A-Z a-z 0-9 . , _ + : @ % ^ = ~ -`, with at least one lowercase letter,
one uppercase letter, one digit and one symbol
([credentials](https://github.com/wazuh/wazuh-kubernetes/blob/v5.0.0-rc2/docs/ref/credentials.md)).
The settings job holds its users to the same rule. Each part stores its
passwords at its first start: a later change of a Secret changes nothing,
and a password changes with the vendor's `password-tool.sh` (same page).

## Accounts

The indexer has the vendor's accounts (`admin`, `kibanaserver`,
`wazuh-manager`) from `indexer-credentials`. The settings job
(`../wazuh-settings`) makes the accounts of this repository from
`../wazuh-settings/app/config/accounts.json`:

| User | Role | For |
| --- | --- | --- |
| `tnwks-settings` | `tnwks-settings`: the ISM APIs, the raw events setting, and a read of the raw events stream. No Security API | The settings step of the job |
| `tnwks-converter` | `tnwks-converter`: a read of the index names of "Contract", with field-level security on the agent snapshots | The converter |

The account step of the job runs as an account user that the owner makes
once, by hand, with `admin`: an internal user mapped to the reserved role
`security_rest_api_access`, with its name and password in
`wazuh-settings-accounts`. That role can make users, roles and role mappings
that are not reserved. Which form step 2 uses is open at the design gate:
this split, or `admin` for both steps (simpler), or the admin certificate
later (stricter).

## Retention

The shipped ISM policies of the streams roll over only at 20 GB for each
primary shard, so at the volume of tnwks they delete nothing, and the Setup
plugin writes them again at each start
([retention](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/setup/retention.md)).
The settings job writes a policy of tnwks for each stream under its own ID
(`tnwks-stream-<stream>`, priority 1), from
`../wazuh-settings/app/config/retention.json`:

| Value | Now | Open at the design gate |
| --- | --- | --- |
| `rollover_age` | 30 days | |
| `delete_age` (events, findings, raw events, active responses) | 120 days | 90 days as a floor (120 days) or as a ceiling (90 days). The file holds the floor |
| `metrics_delete_age` | 60 days | |
| `index_raw_events` | `false` | |

One edit of `delete_age` changes four policies. The job moves the backing
indices that exist to the new policy or version and waits until ISM reports
it. Do not restart the indexer while a run waits: a restart drops a pending
change.

## Contract

What other apps and workstreams read from here:

| What | Value |
| --- | --- |
| Index names for the converter | `wazuh-states-sca`, `wazuh-states-vulnerabilities`, the agent snapshots `wazuh-metrics-agents` (one snapshot each 600 seconds: `@timestamp`, `wazuh.agent.id`, `wazuh.agent.name`, `wazuh.agent.status`, `wazuh.agent.last_seen`, `wazuh.agent.disconnected_at`), and `.wazuh-cti-consumers` for the state of the content feed. Sources: [fields](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/wcs/stateless/metrics/agents/docs/fields.csv), [writer](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/framework/wazuh/core/indexer/metrics_snapshot.py) |
| The read-only user of the converter | `tnwks-converter`, its password in the Secret `wazuh-converter-reader` (key `password`) |
| The indexer for the converter | `https://wazuh-indexer.security.svc.cluster.local:9200`, verified with `ca.crt` of the Secret `wazuh-ca` (never its key) |
| The manager for the agents | `${WAZUH_AGENTS_LB_ADDR}` port 1517 (TCP), with the name `${WAZUH_AGENTS_HOST}`. Both are keys of the cluster-settings of homelab-wsl-02 that the owner adds |
| The manager API | `https://wazuh-api.security.svc.cluster.local:55000`, in the cluster only. How the enrollment step of the agent roles reaches it belongs to the agents workstream |

## Before the merge that lists it

1. The owner adds `WAZUH_AGENTS_LB_ADDR` (a free address of the
   LoadBalancer pool) and `WAZUH_AGENTS_HOST` to the cluster-settings of
   homelab-wsl-02.
2. The owner makes the Secrets of "Secrets" and lists them.
3. The test cluster of task 8.2 has run this overlay at the pinned tag (see
   "Not yet proven on a cluster").
4. The listing adds `ks.yaml` and `../wazuh-settings/ks.yaml` to the apps of
   homelab-wsl-02, and runs
   `python3 kubernetes/apps/flux-system/admission/classlist.py write`.
5. After the first start of the server, the owner makes the account user
   (see "Accounts"). Until then the settings job fails each day and
   `KubeJobFailed` fires.

## Not yet proven on a cluster

These files are written from the vendor's sources at the tag, and checked
by the tests below. These points hold only after the test cluster runs them:

- That each patched pod starts: single-node discovery with the vendor's
  remaining settings, `node.store.allow_mmap`, the heap, the init
  containers in their new order, and the projected certificate volumes.
- That the indexer takes the DNs of the cert-manager certificates, and
  that OpenSearch reads their PKCS#8 keys.
- That the strict loader of the manager accepts `wazuh-manager.conf`, and
  that the copy of the image gives it the owner and mode that the manager
  needs.
- That the manager authenticates to the indexer with the client
  certificate of `wazuh-manager-indexer-client`.
- That the Content Manager reaches its feed through `wazuh-indexer-feed`
  only (the snapshot download can use another host).
- The role permissions of `tnwks-settings` and `tnwks-converter`, the FLS
  of the agent snapshots, and whether `.wazuh-cti-consumers` is a system
  index that a role can not read.
- That the ISM explain API reports `policy_seq_no`, and that a change to
  the same policy ID takes its new version.
- The default of `engine.index_raw_events`, the index names with live
  documents, and the count of shards at idle.

## Tests

- `security/tests/test_wazuh_manifests.py`: the tag and the ignored files,
  the images by digest, the node selector and the memory lines against the
  table above, nothing privileged, no `0.0.0.0/0` and no `world` egress, the
  Secrets that nothing in git holds, and the configuration of the manager.
  A render of the overlay against a local copy of the vendor's files runs
  when `WAZUH_KUBERNETES_DIR` names that copy and `kustomize` is installed.
- `security/tests/test_wazuh_settings.py`: the settings job against a fake
  indexer API.
