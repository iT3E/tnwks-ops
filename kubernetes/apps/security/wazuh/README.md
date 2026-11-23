# wazuh

The Wazuh server, step 2 of the security platform, on homelab-wsl-02 only:
one manager node, one indexer node and the dashboard, at the tag
`v5.0.0-rc2`. The agents of the hosts report to the manager, and the
converter (`../converter`) reads the state of the hosts from the indexer.

homelab-wsl-02 lists `ks.yaml` and `../wazuh-settings/ks.yaml`
(`kubernetes/clusters/wsl-02/apps/kustomization.yaml`). No other cluster
lists either (see "The merges that list it").

## How it is built

The vendor's manifests of the server are in this repository, in
`server/upstream/`: a byte-for-byte copy of the files of
`wazuh/wazuh-kubernetes` at `v5.0.0-rc2` that this app uses, with the
vendor's licence (`server/UPSTREAM.md` lists them, the commit, and how to
update). This repository patches them. Flux builds both Kustomizations
(`ks.yaml`) from the repository itself, so each object of the server is in
git, and a change of the vendor's files comes as a reviewed diff.

Why a copy, and not a source of the vendor's repository (the Flux admission
policy `flux-child-scope`, `../../flux-system/admission`):

- The class of the policy is what git of this repository builds at a class
  path. A Kustomization whose source is another repository is outside the
  class, and the objects it applies into a class namespace (here `security`)
  do not pass the rules for objects from outside the class.
- The walk that makes the class list (`classlist.py`) builds the path inside
  this repository, whatever the source says, so it can not see the objects
  of another source.
- A tag of another repository can move, and its content does not pass the
  review of this repository. So the manifests are here, at the commit of the
  pinned tag, with a check against the vendor's repository
  (`server/UPSTREAM.md`).

| Kustomization | What it applies |
| --- | --- |
| `security-wazuh` | `app/`: the four Secrets, the certificates, the network rules, the agents Service and the configuration of the manager |
| `security-wazuh-server` | `server/`: the vendor's manifests that this app keeps, with the images by digest and the patches of `ks.yaml` |

The vendor's files left out, so they never reach a cluster:

- `wazuh/kustomization.yml`: it makes the Secrets from local files and sets
  the namespace `wazuh`.
- `wazuh/base/`: the namespace, a storage class, the Traefik routes, and a
  deny-all and DNS policy for the whole namespace. The namespace `security`
  has its own (`../namespace.yaml`), and `app/networkpolicy.yaml` brings the
  DNS rule.
- The worker node, its Service of port 1514 and its policy: one manager
  node only.
- The Service of port 1515: legacy enrollment is off.
- `manager-egress-external.yaml`: egress to any address on port 443 (see
  "Network").
- The agents Service: `app/service-agents.yaml` replaces it.

The patches of `security-wazuh-server`:

- A node selector on each pod, a request and a memory limit on each
  container (see "Memory"), and no CPU limit, as for the other apps here.
- One indexer node. The `opensearch.yml` of the image names `node-1` as the
  initial cluster manager and sets no discovery type
  ([opensearch.prod.yml](https://github.com/wazuh/wazuh-indexer/blob/v5.0.0-rc2/distribution/src/config/opensearch.prod.yml#L2-L4)),
  so the vendor's variable `cluster.initial_cluster_manager_nodes`
  (`wazuh-indexer-0`) stays, and `discovery.seed_hosts` is this one pod, by
  a name of its certificate, because the image enforces host name
  verification on the transport. The vendor's privileged init container that
  sets `vm.max_map_count` is gone, so each pod meets the `baseline` level of
  the namespace. OpenSearch enforces its bootstrap checks, because the node
  publishes an address that is not a loopback, and
  `node.store.allow_mmap: false` keeps the indexer off memory maps, so the
  check of `vm.max_map_count` does not apply and the indexer needs no sysctl
  of the node
  ([BootstrapChecks.java](https://github.com/opensearch-project/OpenSearch/blob/3.6.0/server/src/main/java/org/opensearch/bootstrap/BootstrapChecks.java#L201-L205),
  [the check](https://github.com/opensearch-project/OpenSearch/blob/3.6.0/server/src/main/java/org/opensearch/bootstrap/BootstrapChecks.java#L461-L470)).
- The indexer heap at 1536 MiB, under half of its memory line (see "Memory").
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
  for the indexer (Design Doc 0004, section 10.1), and the vendor's 1 GiB
  PVC of the dashboard's configuration (`wazuh-dashboard-config`). The class
  enforces no size, so the drive of the site is the real limit.

A strategic merge patch of a list puts the init containers that it names
first. None of them reads what another one writes.

## Memory

The lines of `../README.md`, "Memory":

| Container | Request | Limit |
| --- | --- | --- |
| `wazuh-indexer` | 4 GiB | 4 GiB (heap 1536 MiB) |
| `wazuh-manager` | 1280 MiB | 1280 MiB |
| `wazuh-dashboard` | 320 MiB | 320 MiB |
| `wazuh-accounts` (`../wazuh-settings`, step 1, a Job) | 64 MiB | 128 MiB |
| `wazuh-settings` (`../wazuh-settings`, step 2, a Job and a CronJob) | 64 MiB | 128 MiB |

The init containers run before the main container of their pod, one at a
time, each with a limit of 128 MiB or less. In Wazuh 5.0 the indexer
container also runs the Wazuh engine (the image's entrypoint starts it), so
the indexer line covers both.

The limit of the indexer is 4 GiB, by the owner's decision for the budget
of step 2 (`../README.md`, "Memory"); the heap stays at 1536 MiB. The heap
is only one part of what the container uses:

- OpenSearch recommends a heap of half of the memory of the system, not all
  of it
  ([important settings](https://github.com/opensearch-project/documentation-website/blob/d4a12efae197ed01306f752eb1406072ad18bb58/_install-and-configure/install-opensearch/index.md#L103-L105)).
  A heap of 1536 MiB is under half of 4 GiB.
- The entrypoint of the image starts the Wazuh engine as a process of its
  own next to OpenSearch, outside the JVM and its heap
  ([entrypoint.sh](https://github.com/wazuh/wazuh-docker/blob/v5.0.0-rc2/build-docker-images/wazuh-indexer/config/entrypoint.sh#L175-L182)).
- The memory controller of the kernel counts the page cache of the files
  that the container reads, as well as its anonymous memory, against the
  limit of its cgroup
  ([cgroup v2](https://github.com/torvalds/linux/blob/v6.12/Documentation/admin-guide/cgroup-v2.rst#L1205-L1210)).
  Lucene reads its index files through that cache.

So a higher limit gives room to the engine, to the memory of the JVM
outside the heap and to the page cache, and a higher heap would take it
back. The request equals the limit, as for the manager and the dashboard,
so the scheduler reserves the whole line on worker 1.

A change of a memory line of the indexer changes the pod template of its
StatefulSet, so the StatefulSet controller replaces the pod `wazuh-indexer-0`
once, on the same volume.

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
- The manager logs in to the indexer as the user `wazuh-manager`, with
  `WAZUH_INDEXER_MANAGER_PASSWORD` of `manager-credentials`, which the image
  stores in the keystore of the manager
  ([credentials](https://github.com/wazuh/wazuh-kubernetes/blob/v5.0.0-rc2/docs/ref/credentials.md#L123)).
  The client certificate of `wazuh-manager-indexer-client` serves the TLS
  connection only: the security configuration of the image keeps the client
  certificate domain off
  ([config.yml, OpenSearch security](https://github.com/opensearch-project/security/blob/3.6.0.0/config/config.yml#L142-L145)).

## Network

`app/networkpolicy.yaml` adds what the vendor's policies do not have. The
vendor's policies stay for the traffic between the parts: the dashboard and
the manager to the indexer on 9200, the dashboard to the manager API on
55000, and the cluster port 1516 between manager pods.

| Rule | From | To |
| --- | --- | --- |
| `wazuh-dns` | The three parts | DNS of this cluster, through Cilium's DNS proxy |
| `wazuh-indexer-feed` | The indexer | `api.pre.cloud.wazuh.com:443`, the default CTI API of the Content Manager at `v5.0.0-rc2` |
| `wazuh-agents` | world, or a node of either cluster (the shared VIP, with `externalTrafficPolicy: Cluster`) | The manager on 1517 |
| `wazuh-indexer-readers` | The two steps of the settings and the converter | The indexer on 9200 |

The vendor's `manager-egress-external.yaml` (any address on 443) is not
applied. The manager and the dashboard have no route out of the cluster, so
neither reaches an update service.

No route reaches the dashboard: its Service is ClusterIP, there is no
Ingress or HTTPRoute, and no network rule lets traffic in. How a person
reaches it, and as which user, is a later change.

The agents reach the manager on a shared VIP (`app/service-agents.yaml`,
`docs/wsl/multi-site.md`, "Shared VIPs"): `WAZUH_AGENTS_LB_ADDR`, an address
of `10.10.95.0/24` in the half that the router prefers on site 2. The label
`tnwks.us/shared-vip: "true"` puts the Service in the pool `shared-vip` and in
the BGP advertisement of the nodes (`kubernetes/apps/kube-system/cilium/bgp`).
Only homelab-wsl-02 lists this app, so only its nodes announce the address. A
host reaches it over the routed network, from a network that the router lets
reach the shared VIPs. The agents dial `WAZUH_AGENTS_HOST`, which resolves to
the VIP through its site record, `vip_wazuh_agents` in
`infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf` (see "The
merges that list it"). Without that record the name answers with the
`internal.tnwks.us` wildcard, which is not the server, so no agent (a later
workstream) enrolls before the record is applied.

## Certificates

`app/certificates.yaml`: cert-manager makes a CA for the server in the
cluster (`wazuh-ca`), and the CA issues a certificate for the indexer node,
the indexer admin, the client of the manager to the indexer, the agent
listener, the manager API and the dashboard. No key is in git. The indexer
reads its node DN and the admin DN from the certificates at each start, so
the DNs are ours (`O=tnwks`). Each leaf lasts 90 days and renews 30 days
before its end, and Reloader restarts the workload when its Secret changes.
A restart of the indexer drops a move of ISM policy that waits, so step 2 of
the settings starts no move in the hour before a renewal (see "Retention").

The certificate of the agent listener names `${WAZUH_AGENTS_HOST}` and
`${WAZUH_AGENTS_LB_ADDR}`. An enrollment token pins the CA, so a rebuild of
the cluster, which makes a new CA, means each agent enrolls again. The
stricter later step is a CA in SOPS, as `observability-pki` has.

## Secrets

Each one is a SOPS file in the app of the table, listed in that app's
`kustomization.yaml`. No session reads them, and no
password and no hash is in a plain file of this repository. Each password is
only in the container of the part that logs in with it.

| Secret | App | Key | Content | Mounted by |
| --- | --- | --- | --- | --- |
| `indexer-credentials` | this app | `wazuh-credentials` | The lines `WAZUH_INDEXER_ADMIN_PASSWORD=`, `WAZUH_INDEXER_KIBANASERVER_PASSWORD=`, `WAZUH_INDEXER_MANAGER_PASSWORD=` | The indexer |
| `manager-credentials` | this app | `wazuh-credentials` | `WAZUH_INDEXER_MANAGER_PASSWORD=`, `WAZUH_MANAGER_API_PASSWORD=`, `WAZUH_MANAGER_WUI_PASSWORD=` | The manager |
| `dashboard-credentials` | this app | `wazuh-credentials` | `WAZUH_INDEXER_KIBANASERVER_PASSWORD=`, `WAZUH_MANAGER_WUI_PASSWORD=` | The dashboard |
| `wazuh-cluster-key` | this app | `key` | 32 characters from `A-Z a-z 0-9` | The manager |
| `wazuh-accounts-user` | `../wazuh-settings/accounts` | `username`, `password` | The account user (see "Accounts") | Step 1 only |
| `wazuh-accounts-hashes` | `../wazuh-settings/accounts` | `tnwks-settings`, `tnwks-converter`: one key for each user of `accounts.json` | The bcrypt hash of the password of that user | Step 1 only |
| `wazuh-settings-user` | `../wazuh-settings/app` | `password` | The password of `tnwks-settings` | Step 2 only |
| `wazuh-converter-reader` | `../converter` | `password` | The password of `tnwks-converter` | The converter only. Its SOPS file is in `../converter/app`, and no kustomization lists it: the change that adds the Wazuh source to the converter lists it and mounts it |

The password rule of the Wazuh images: 12 to 64 characters from
`A-Z a-z 0-9 . , _ + : @ % ^ = ~ -`, with at least one lowercase letter,
one uppercase letter, one digit and one symbol
([credentials](https://github.com/wazuh/wazuh-kubernetes/blob/v5.0.0-rc2/docs/ref/credentials.md)).
The users of `accounts.json` follow the same rule, and step 2 refuses a
password of `tnwks-settings` that breaks it. Each hash is made from its
password with a bcrypt tool, as the hash tool of the OpenSearch security
plugin (`plugins/opensearch-security/tools/hash.sh`).

The vendor's parts store their passwords at their first start: a later
change of one of their Secrets changes nothing, and a password changes with
the vendor's `password-tool.sh` (same page). A password of a user of
`accounts.json` changes in one merge of the owner: the new password in the
Secret of the part that logs in, and its hash in `wazuh-accounts-hashes`.
Then the owner deletes the Job of step 1, and Flux runs it again.

Flux substitutes the variables of the cluster in the objects of both
Kustomizations of the settings. The parent of the apps of homelab-wsl-02
(`kubernetes/clusters/wsl-02/apps.yaml`) gives each child without the label
`substitution.flux.home.arpa/disabled` the SOPS decryption and
`postBuild.substituteFrom`, and the two have no such label.
kustomize-controller substitutes each object after it decrypts it, so a
decrypted Secret too
([the build](https://github.com/fluxcd/kustomize-controller/blob/v1.10.1/internal/controller/kustomization_controller.go#L862-L879)).
A bcrypt hash holds three `$` signs (`$2y$12$` and 53 characters from
`./A-Za-z0-9`). The envsubst of Flux reads a variable only at `${`, and
`$$` as an escape; a `$` before any other character stays as it is
([the scanner](https://github.com/fluxcd/pkg/blob/envsubst/v1.9.0/envsubst/parse/scan.go#L189-L232)),
and a hash holds neither `${` nor `$$`. The Secret `wazuh-accounts-hashes`
also turns the substitution off for itself, with the annotation
`kustomize.toolkit.fluxcd.io/substitute: disabled`: the controller skips
each object with that label or annotation
([SubstituteVariables](https://github.com/fluxcd/pkg/blob/kustomize/v1.44.0/kustomize/kustomize_varsub.go#L112-L132)).
The annotation is in the plain metadata of the SOPS file (the rule of
`.sops.yaml` for `kubernetes/` encrypts only `data` and `stringData`), so
the object that the controller reads after the decryption has it.
`security/tests/test_wazuh_manifests.py` pins the annotation, the labels of
the two Kustomizations and the form of a hash that step 1 accepts.

## Accounts

The indexer has the vendor's accounts (`admin`, `kibanaserver`,
`wazuh-manager`) from `indexer-credentials`. Step 1 of the settings
(`../wazuh-settings/accounts`, the Job `wazuh-accounts-<hash>`) makes the accounts of
this repository from `../wazuh-settings/app/config/accounts.json`:

| User | Role | For |
| --- | --- | --- |
| `tnwks-settings` | `tnwks-settings`: the five ISM actions that step 2 calls ([`policy/get`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/getpolicy/GetPolicyAction.kt#L13), [`policy/write`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/indexpolicy/IndexPolicyAction.kt#L13), [`managedindex/add`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/addpolicy/AddPolicyAction.kt#L14), [`managedindex/change`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/changepolicy/ChangePolicyAction.kt#L14), [`managedindex/explain`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/explain/ExplainAction.kt#L13), each under `cluster:admin/opendistro/ism/`), and the ISM index actions on the patterns of the four streams of "Retention". `policy/write` lets the user write a policy under any ID: the role can not limit the ID, so step 2 refuses to write a shipped ID. Also the raw events setting, with a write of the settings document in `.wazuh-settings` (the Setup plugin indexes it with the rights of the caller, which OpenSearch runs as a bulk request: the cluster action `indices:data/write/bulk`, and `indices:data/write/index` and `indices:data/write/bulk[s]` on that index, [SettingsIndex.java](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/plugins/setup/src/main/java/com/wazuh/setup/index/SettingsIndex.java#L106-L113)), and the index stats of the two streams that hold no document. Also what ISM calls with this role for the policies of tnwks (see below): the index stats and the rollover on the names of the four streams, and the index stats and the delete on their backing indices. No Security API, and no read of a document | Step 2 |
| `tnwks-converter` | `tnwks-converter`: a read of the index names of "Contract", with field-level security on the agent snapshots, and the index stats (the size) of each index of Wazuh | The converter |

Step 1 runs only when its files change: `accounts.json`, `indexer.py` and
`accounts.py`. The name of its Job carries the first 10 hex digits of the
sha256 of the three files, one after another, so a change makes a new Job,
Flux runs it once, and prune removes the old one. It does not run each day.
`security/tests/test_wazuh_settings.py` fails when the name does not match
the files, and names the right one. To run it again with the same files,
for example after a failure, delete the Job: Flux makes it again. So step 1
runs when `accounts.json`, its script or a hash changes: a change of a file
gives the Job a new name, and at a change of a hash only (in
`wazuh-accounts-hashes`) the owner deletes the Job.

It runs as the account user, which its own step makes once with `admin`
(see "The merges that list it"), from `../wazuh-settings/app/config/account-user.json`: an internal
user mapped to the reserved role `security_rest_api_access`, with its name
and password in `wazuh-accounts-user`. The file holds no hash, and step 1
never reads it. It is applied through the Security API: a `PUT` of
the user with the hash of its password, and a `PUT` of the role mapping of
`security_rest_api_access` with the user. The role mappings of the image have
no mapping for that role (as the defaults of the security plugin,
[roles_mapping.yml](https://github.com/opensearch-project/security/blob/3.6.0.0/config/roles_mapping.yml)),
so the `PUT` makes it; if a mapping exists, the user is added to its
`users` and the others stay. The image of the indexer gives the
Security API to that role and to `all_access` only
([opensearch.yml](https://github.com/wazuh/wazuh-indexer/blob/v5.0.0-rc2/build-scripts/docker/config/opensearch.yml#L49-L51)). That role can make, change and delete users, roles
and role mappings that are neither reserved nor hidden, so it can also give
itself wide rights on the data
([API permissions, OpenSearch](https://github.com/opensearch-project/documentation-website/blob/d4a12efae197ed01306f752eb1406072ad18bb58/_security/access-control/api.md#L67)).
Only the pod of step 1 mounts its Secret.

ISM runs each step of a policy with the roles of the user that wrote or
moved the policy, not with the rights of ISM:

- A write of a policy stores the caller as its user
  ([TransportIndexPolicyAction.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/indexpolicy/TransportIndexPolicyAction.kt#L232)),
  and so do `add`
  ([TransportAddPolicyAction.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/addpolicy/TransportAddPolicyAction.kt#L328))
  and `change_policy`
  ([TransportChangePolicyAction.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/changepolicy/TransportChangePolicyAction.kt#L369),
  [ManagedIndexRunner.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexRunner.kt#L773)).
  A backing index that ISM puts on a policy by its `ism_template` gets the
  policy with its stored user
  ([ManagedIndexCoordinator.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexCoordinator.kt#L316-L327)).
- The runner runs each step with the roles of that user
  ([ManagedIndexRunner.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexRunner.kt#L453-L459),
  [OpenSearchExtensions.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/opensearchapi/OpenSearchExtensions.kt#L302)),
  and with `all_access` only for a policy with no user, as the shipped ones
  ([SecurityUtils.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/util/SecurityUtils.kt#L23)).

Step 2 writes and moves the policies of tnwks as `tnwks-settings`, so ISM
runs their steps with the role `tnwks-settings`, and the role holds what
those steps call:

- The rollover step asks for the stats of the backing index
  ([AttemptRolloverStep.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/step/rollover/AttemptRolloverStep.kt#L74-L76),
  [the call](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/step/rollover/AttemptRolloverStep.kt#L216-L219)),
  then rolls over the data stream by its name
  ([the rollover](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/step/rollover/AttemptRolloverStep.kt#L251)).
  The rollover of a data stream asks for the stats of the stream name
  ([TransportRolloverAction.java](https://github.com/opensearch-project/OpenSearch/blob/3.6.0/server/src/main/java/org/opensearch/action/admin/indices/rollover/TransportRolloverAction.java#L170-L175)),
  and makes the new backing index in its own change of the cluster state,
  with no other action to check
  ([MetadataRolloverService.java](https://github.com/opensearch-project/OpenSearch/blob/3.6.0/server/src/main/java/org/opensearch/action/admin/indices/rollover/MetadataRolloverService.java#L205-L229)).
  So: `indices:monitor/stats` on the names and on the backing indices, and
  `indices:admin/rollover` on the names.
- The delete step deletes the backing index
  ([AttemptDeleteStep.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/step/delete/AttemptDeleteStep.kt#L30)):
  `indices:admin/delete` on the backing indices only.

A test on a throwaway indexer of the pinned image, with a copy of the
policies of tnwks at ages of minutes, written and moved by a user with
exactly this role, ran the rollover and the delete to the end, also for an
index moved by `add` and for the new backing index of a rollover. With the
role before this change, the rollover step failed at the stats of the
backing index, and the index stayed on its policy. With the delete on the
backing indices only and the stats on them only, the rollover failed at the
stats of the stream name. After the role of a failed index got the rights of
this change, the index stayed at its failed step until a retry by `admin`,
and then rolled over. The check of step 2 for a failed step, applied to the
explain answers of that test, counted the failed index and no other.

So `tnwks-settings` can roll over and delete each backing index of the four
streams. Who holds its password can delete the security data of those
streams. The password is in one SOPS Secret, `wazuh-settings-user`, which
only the pod of step 2 mounts. The index permissions of the role, all of
them (`app/config/accounts.json`, pinned by
`test_the_index_permissions_of_each_role_are_pinned`):

| Index patterns | Actions |
| --- | --- |
| The names of the four streams and of their backing indices: `wazuh-events-v5*`, `.ds-wazuh-events-v5-*`, `wazuh-findings-v5*`, `.ds-wazuh-findings-v5-*`, `wazuh-events-raw-v5*`, `.ds-wazuh-events-raw-v5-*`, `wazuh-metrics*`, `.ds-wazuh-metrics-*` | `indices:admin/opensearch/ism/*` (the ISM index actions of the moves and of explain) |
| The names of the four streams: `wazuh-events-v5*`, `wazuh-findings-v5*`, `wazuh-events-raw-v5*`, `wazuh-metrics*` | `indices:monitor/stats`, `indices:admin/rollover` |
| Their backing indices: `.ds-wazuh-events-v5-*`, `.ds-wazuh-findings-v5-*`, `.ds-wazuh-events-raw-v5-*`, `.ds-wazuh-metrics-*` | `indices:monitor/stats`, `indices:admin/delete` |
| The active responses stream: `.ds-wazuh-active-responses-*`, `wazuh-active-responses*` | `indices:monitor/stats` |
| `.wazuh-settings` | `indices:data/write/index`, `indices:data/write/bulk[s]` |

The image also maps every user to its reserved role `wazuh_ai_assistant`,
which reads and writes only the sessions of the AI assistant of that user
([roles_mapping.wazuh.yml](https://github.com/wazuh/wazuh-indexer/blob/v5.0.0-rc2/distribution/src/config/security/roles_mapping.wazuh.yml#L23-L27),
[roles.wazuh.yml](https://github.com/wazuh/wazuh-indexer/blob/v5.0.0-rc2/distribution/src/config/security/roles.wazuh.yml#L100-L115)).

What step 1 does (`../wazuh-settings/app/scripts/accounts.py`):

- It owns only what carries its mark: the attribute
  `tnwks_managed_by: wazuh-accounts` on a user, and a description that
  starts with `[wazuh-accounts]` on a role or a role mapping.
- It never touches a resource that is reserved, hidden or static, a user of
  the vendor, or the account user. Each name of `accounts.json` starts with
  `tnwks-`, and a name of the vendor or of the account user stops the run.
- When a name of `accounts.json` exists without its mark, or is reserved,
  hidden or static, it stops before any write.
- It writes each role, user and role mapping that is missing or differs,
  and sets the hash of each user from `wazuh-accounts-hashes`. It holds no
  password of a user that it makes.
- It deletes each user, role and role mapping that carries its mark and that
  `accounts.json` no longer names.
- It reads back what carries its mark, and fails when it is not what
  `accounts.json` says.

## Retention

The shipped ISM policies of the streams roll over only at 20 GB for each
primary shard or 200,000,000 documents, and ISM deletes an index only after
its rollover. At the volume of tnwks the write index never rolls over, so
the shipped policies delete nothing. The Setup plugin writes its own policies
again by their IDs at each election of a cluster manager, which includes each
restart, and the vendor says that its deletion ages "cannot be used as
privacy or compliance statements"
([retention](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/setup/retention.md)).
So step 2 of the settings writes a policy of tnwks for four streams, under
IDs of its own (`tnwks-stream-<stream>`, priority 1, above the shipped
priority 0), from `../wazuh-settings/app/config/retention.json`. This is the
way that the vendor gives for a custom policy: priority 1 wins over the
shipped policy for newly created backing indices
([retention](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/setup/retention.md#L118-L121)).

Which policy an index of a stream starts on:

- The first backing index of each stream, and the newest one at a start of
  the indexer if ISM does not manage it yet: the shipped policy. At its
  start the Setup plugin reads `index.plugins.index_state_management.policy_id`
  of the last backing index of the stream, which the stream template sets to
  the shipped ID, and registers that index with ISM when it has no managed
  index yet
  ([StreamIndex.java](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/plugins/setup/src/main/java/com/wazuh/setup/index/StreamIndex.java#L163-L203),
  for example [events.json](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/plugins/setup/src/main/resources/templates/streams/events.json#L28)).
- A backing index that a rollover makes: the policy of tnwks. At each new
  index, ISM takes the policy with the highest `ism_template` priority whose
  template is older than the index, and that the user who wrote the policy
  may apply: it runs `indices:admin/opensearch/ism/managedindex` as that
  user on the name that the template matched (for a backing index, the name
  of its data stream, [the lookup](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexCoordinator.kt#L336-L357)), and skips the policy when the user lacks it
  ([ManagedIndexCoordinator.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexCoordinator.kt#L368-L398),
  [the check](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/ManagedIndexCoordinator.kt#L441-L455)). That user is
  `tnwks-settings`, whose role gives the ISM index actions on the names of
  the four streams and on the names of their backing indices
  (`wazuh-events-v5*` and `.ds-wazuh-events-v5-*`, and the others of
  `accounts.json`; a backing index is named `.ds-<stream>-<generation>`,
  [DataStream.java](https://github.com/opensearch-project/OpenSearch/blob/3.6.0/server/src/main/java/org/opensearch/cluster/metadata/DataStream.java#L191-L193)).
  This path does not read the `policy_id` setting of the template. The
  new index gets the policy with its stored user, so ISM runs its steps
  with the role `tnwks-settings` (see "Accounts").

Step 2 moves each index that is not on its policy of tnwks at each run: the
first index of a stream, or one that ISM put on another policy.

| Value | Now | Why |
| --- | --- | --- |
| `rollover_age` | 30 days | |
| `delete_age` (host events, findings, raw events) | 120 days | The design of effort 0004 sets 90 days as a floor: 90 days plus one rollover period, so each record stays 90 to 120 days |
| `metrics_delete_age` | 60 days | |
| `index_raw_events` | `false` | |

Each policy rolls over at `rollover_age` and deletes an index at its delete
age, counted from the creation of the index, so a record stays from the
delete age less `rollover_age` up to the delete age. ISM acts at each run of
its job: each 5 minutes by default, with a random delay of up to 60% of that
([ISM settings](https://github.com/opensearch-project/documentation-website/blob/d4a12efae197ed01306f752eb1406072ad18bb58/_im-plugin/ism/settings.md#L19-L20)).

What each store of Wazuh keeps, at `v5.0.0-rc2`:

| Store | Its rule here |
| --- | --- |
| Host events, `wazuh-events-v5-<category>` | `tnwks-stream-events`: a rollover at 30 days and a delete at 120 days |
| Findings, `wazuh-findings-v5-<category>` | `tnwks-stream-findings`: the same ages |
| Raw events, `wazuh-events-raw-v5` | Off: step 2 sets `engine.index_raw_events` to `false`, the default at the tag ([settings API](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/setup/api-reference.md)). `tnwks-stream-raw-events`, with the ages of host events, guards the stream. Step 2 writes the count of its documents to `indices.json` |
| Metrics, `wazuh-metrics-*` | `tnwks-stream-metrics`: a rollover at 30 days and a delete at 60 days |
| Active responses, `wazuh-active-responses` | The shipped rule: a rollover by size and a delete at 3 days. The design of effort 0004 runs no active response, so the stream holds no document. Step 2 writes the count of its documents to `indices.json` |
| AI assistant sessions, `wazuh-ai-assistant-sessions` | The shipped rule: a rollover at 1 day and a delete at 7 days ([the policy](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/plugins/setup/src/main/resources/policies/ai-assistant-sessions-policy.json)) |
| The alert history of the Alerting plugin, `.opendistro-alerting-alert-history-*` | The default of the plugin: it deletes each history index at 60 days ([alerting settings, OpenSearch](https://github.com/opensearch-project/documentation-website/blob/d4a12efae197ed01306f752eb1406072ad18bb58/_observing-your-data/alerting/settings.md#L70)) |
| The audit log of the security plugin | The tag ships no policy for it ([the policies](https://github.com/wazuh/wazuh-indexer-plugins/tree/v5.0.0-rc2/plugins/setup/src/main/resources/policies)). Whether the pinned image writes it is not known yet (see "Not yet proven on a cluster"). If it does, a stream with the vendor's ages of its later policy (a rollover at 30 days and a delete at 90 days) goes into `retention.json` |
| The state indices: `wazuh-states-*`, `wazuh-agent-config`, `wazuh-agent-stats` | No age. Each holds one document for each item, written again at each report ([the Setup plugin](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/docs/ref/modules/setup/index.md)). The owner removes the agent of a host that leaves tnwks |
| The rotated logs of the manager, on its volume | 31 days, the default of the manager ([task manager](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/docs/ref/modules/task_manager/configuration.md#L274)) |
| `alerts.json` and `archives.json`, on the volume of the manager | Whether the manager writes them by default at the tag is not known yet (see "Not yet proven on a cluster") |

### The merges that delete data

The merge that first lists `../wazuh-settings/ks.yaml` applies the policies,
and ISM then deletes each index older than its age. A merge that lowers an
age does the same at the new age. Deleted data has no way back. Each such
merge is the owner's.

What the first listing can delete: ISM counts each age from the creation of
the index (`index.creation_date`), for the rollover
([AttemptRolloverStep.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/step/rollover/AttemptRolloverStep.kt#L97-L105))
and for the move to the state `delete`
([checkIndexAge](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/util/ManagedIndexUtils.kt#L218-L224)).
A policy of tnwks deletes an index only in its state `delete`, which an
index reaches only after its rollover, at the earliest when it is
`delete_age` old: 120 days for host events, findings and raw events, 60
days for metrics. Each index of a stream is made by the indexer, at the
first start of the indexer or later. So on an indexer that first started
less than 60 days before the first listing, no index is old enough for a
delete, and less than 30 days before, none for a rollover. The first
deletes come at the earliest 60 days after the first start (metrics) and
120 days after it (the other three). The listing changes no shipped policy:
the active responses stream and the AI assistant sessions keep their
shipped ages from the first start of the indexer on.

`security/tests/test_wazuh_settings.py` pins each age of each policy
(`PINNED`). A change of an age, or a stream that goes, fails CI until the
same change edits the pin, and the message names a lowered age as a merge
that deletes data. The pin is in the test, not a compare with the base
branch: it runs on each run of CI, with no need of the history of git, and a
lower age needs a deliberate edit that the review sees.

### No restart while a move waits

Step 2 moves only an index that the ISM explain API does not show on its
policy of tnwks, at the version that the run reads (`change_policy`, or
`add` for an index with no policy), and waits until explain names the new
version on each one. A run that writes no policy starts a move for each
index that is not on its policy (the first index of a stream, or one that
ISM put on another policy), and no other. A restart of the indexer before a
move applies drops it (same page, "Opting in to a hard time ceiling"). What
restarts the indexer:

- Reloader, when a certificate of the indexer or the CA renews (the
  annotation of the StatefulSet in `ks.yaml`). Step 2 reads the end of the
  certificate that the indexer serves and of the CA, and starts no move in
  the hour before either one renews (`--renew-before=720h`, the
  `renewBefore` of `app/certificates.yaml`, and `--margin=3600`, more than
  the deadline of a run). It still writes its counts, and the next run
  moves. The node and admin certificates of the indexer come from one apply,
  with the same duration and `renewBefore`, so the certificate that the
  indexer serves stands for both. The guard needs no right in Kubernetes:
  it reads the certificate in the TLS handshake with the indexer and the CA
  file of its pod, and the pod has no token of the API server.
- A merge that changes the StatefulSet of the indexer, its configuration or
  its Secrets, or the tag. Merge such a change apart from a change of
  `retention.json`, and not while a run of step 2 runs:
  `kubectl -n security get jobs -l app.kubernetes.io/name=wazuh-settings`
  shows no active Job.

A kill at the memory limit or a crash can still meet a move. Then the index
keeps its old policy, the count of `indices.json` is above zero, and the
next daily run moves it again, up to about one day later.

### What step 2 does

Step 2 (`../wazuh-settings/app/scripts/settings.py`) runs as
`tnwks-settings` at each merge that changes its files (the Job
`wazuh-settings-git-<hash>`, named as the Job of step 1 from
`retention.json`, `indexer.py` and `settings.py`) and once a day (the CronJob
`wazuh-settings`, at 08:22 UTC). Both run one pod. In this order, it:

1. waits up to 300 seconds until `tnwks-settings` can log in;
2. writes each policy of tnwks that is missing or differs, and never a
   shipped policy ID;
3. sets `engine.index_raw_events`. The tag documents no read of the setting,
   so the check is the answer of the API and the count of documents in the
   raw events stream;
4. moves the indices (see above), and waits up to 1200 seconds for ISM. Two
   runs of the ISM job of an index come up to 8 minutes apart: the interval
   of 5 minutes plus a random delay of 0 to 60% of it
   ([`DEFAULT_JOB_INTERVAL` and `DEFAULT_JITTER`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/settings/ManagedIndexSettings.kt#L19-L20),
   [the delay](https://github.com/opensearch-project/job-scheduler/blob/3.6.0.0/src/main/java/org/opensearch/jobscheduler/scheduler/JobScheduler.java#L161-L178)).
   1200 seconds holds more than two such gaps, and the deadline of a pod
   (`activeDeadlineSeconds`, 1800) is more than the two waits and the calls;
5. counts the indices of the four patterns that are not on the policy of
   tnwks of their stream, with those that have no policy, those whose
   policy stopped at a failed step (`failed`), and the documents of the raw
   events and active responses streams, from the index stats. It writes
   them to `wazuh-settings/indices.json` on the volume of results
   (`security/converter/README.md`, "The count file of the Wazuh settings").
   An index counts as failed when explain shows its step at `failed` (ISM
   may still retry the action), its action failed (the retries of the
   action are used up), or its policy failed (`retry_info`): ISM counts the
   last two as failed itself
   ([ManagedIndexMetaData.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/spi/src/main/kotlin/org.opensearch.indexmanagement.spi/indexstatemanagement/model/ManagedIndexMetaData.kt#L93-L137)).

A run fails at the first error, and, after it writes the counts, when a
move does not apply in time or an index is at a failed step. `KubeJobFailed`
fires 15 minutes after the failure (the `for` of the rule of the chart).

A failed step stays failed after its cause is fixed: ISM runs no further
step of that index until a retry. After the fix (for example a merge that
gives the role a right that it lacked, which step 1 applies), the owner, as
`admin`, retries each failed index:
`POST _plugins/_ism/retry/<index>`, which needs
`cluster:admin/opendistro/ism/managedindex/retry`
([RetryFailedManagedIndexAction.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/retryfailedmanagedindex/RetryFailedManagedIndexAction.kt#L14)).
The retry keeps the stored user of the policy
([TransportRetryFailedManagedIndexAction.kt](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/retryfailedmanagedindex/TransportRetryFailedManagedIndexAction.kt#L255-L282): it resets the metadata of the step and enables the job, and changes no policy),
so the steps still run with the role `tnwks-settings`. Step 2 does not
retry: its role does not hold the retry, because a retry before the cause
is fixed fails again, and the cause needs a person.

The two steps are in two Flux Kustomizations. `security-wazuh-accounts`
(`../wazuh-settings/accounts`: the Job of step 1, its ConfigMap and the
network rule of both steps) has `wait: true`, so it is ready only when the
Job of step 1 completes. `security-wazuh-settings` (`../wazuh-settings/app`:
step 2) depends on it, so Flux applies a change of step 2 after step 1 has
written the rights of the same merge. When the Job of step 1 fails, Flux
applies no change of step 2, and the CronJob that is already there runs as
before.

A failed Job keeps `KubeJobFailed` on until the Job is deleted:

- The Jobs of git, `wazuh-accounts-<hash>` and `wazuh-settings-git-<hash>`,
  have no TTL. A failed one stays until the owner deletes it (Flux then
  makes it again and it runs again) or a merge changes its files (Flux
  prunes the Job of the old hash).
- The Jobs of the CronJob have `ttlSecondsAfterFinished: 86400`, and the
  CronJob keeps one failed Job (`failedJobsHistoryLimit: 1`). A failed run
  stays up to one day after it ends.

While runs of step 2 fail:

- An index that is not on its policy of tnwks stays on the policy that it
  has until a run moves it. On the shipped policy at low volume, the first
  index of a stream neither rolls over nor is deleted. A move later than
  `rollover_age` after its creation rolls it over at once, and its delete
  still counts from its creation, so its newest records stay less than 90
  days. A move later than `delete_age` after its creation deletes it at
  once.
- `KubeJobFailed` shows the failed run.
- Step 2 writes `indices.json` after the moves. A run that fails before
  that writes no file, so the file of the last run that reached it stays,
  with its `time`.

## Contract

What other apps and workstreams read from here:

| What | Value |
| --- | --- |
| Index names for the converter | `wazuh-states-sca`, `wazuh-states-vulnerabilities`, the agent snapshots `wazuh-metrics-agents` (one snapshot each 600 seconds: `@timestamp`, `wazuh.agent.id`, `wazuh.agent.name`, `wazuh.agent.status`, `wazuh.agent.last_seen`, `wazuh.agent.disconnected_at`), and `.wazuh-cti-consumers` for the state of the content feed. Sources: [fields](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/wcs/stateless/metrics/agents/docs/fields.csv), [writer](https://github.com/wazuh/wazuh/blob/v5.0.0-rc2/framework/wazuh/core/indexer/metrics_snapshot.py) |
| The read-only user of the converter | `tnwks-converter`, its password in the Secret `wazuh-converter-reader` (key `password`) |
| The indexer for the converter | `https://wazuh-indexer.security.svc.cluster.local:9200`, verified with `ca.crt` of the Secret `wazuh-ca` (never its key) |
| The manager for the agents | `${WAZUH_AGENTS_LB_ADDR}` port 1517 (TCP), with the name `${WAZUH_AGENTS_HOST}`. Both are keys of the cluster-settings of homelab-wsl-02. The name resolves to the address through its site record (see "The merges that list it") |
| The manager API | `https://wazuh-api.security.svc.cluster.local:55000`, in the cluster only. How the enrollment step of the agent roles reaches it belongs to the agents workstream |

## The merges that list it

Before the first merge, the memory limit of the node container of worker 1
of homelab-wsl-02 goes up to the figure of `../README.md`, "Placement", so
the server fits in the budget of `../README.md`, "Memory". That is a change
of its own, merged by the session that the owner names; its play runs on
site 2 on the owner's go.

1. The first merge, merged after an independent review, lists `ks.yaml` in
   the apps of homelab-wsl-02, with:
   - `WAZUH_AGENTS_LB_ADDR` and `WAZUH_AGENTS_HOST` in the cluster-settings
     of homelab-wsl-02;
   - the four Secrets of this app ("Secrets"), listed in
     `app/kustomization.yaml`;
   - the output of
     `python3 kubernetes/apps/flux-system/admission/classlist.py write`.
     Both Kustomizations build a path of this repository under
     `kubernetes/apps/security/`, so the admission policy
     `flux-child-scope` (`../../flux-system/admission`) lists both in its
     class.

   After it, check that:
   - both Kustomizations, `security-wazuh` and `security-wazuh-server`, are
     Ready;
   - the three pods (`wazuh-manager-master-0`, `wazuh-indexer-0` and the
     dashboard) are Ready;
   - the Service `wazuh-agents` holds `WAZUH_AGENTS_LB_ADDR`.

   `security-wazuh-server` has `wait: false`, so Flux stays Ready while a
   pod fails. The rules of the platform then tell: `KubePodNotReady` and
   the replica mismatch of a StatefulSet or a Deployment, after 15 minutes;
   `KubePodCrashLooping` for a container that restarts again and again, as
   with a bad credential; `OOMKilled` for a container killed at its memory
   limit; `CertManagerCertNotReady` for a certificate that cert-manager does
   not issue; and `FluxResourceNotReady` for a Kustomization that does not
   apply. The table of shared VIPs in `docs/wsl/multi-site.md` gets its row
   for the address once the address is live.

   Nothing orders `flux-admission` and the parent of the apps: no
   `dependsOn` joins them, and the parent applies `flux-admission` as one of
   its children. So when the parent applies a new child of the class before
   the admission policy holds the new list, the parent's apply fails on that
   site until `flux-admission` has reconciled, and nothing is pruned (Flux
   prunes only after an apply succeeds). Read `flux-admission` Ready first,
   then the parent of the apps on both sites.
2. The site record of the agents' name: done by its own change, which adds
   `vip_wazuh_agents` to `site_dns_records` in
   `infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf`. The
   Deploy workflow applies it at the merge (the root `prod/mikrotik`). The
   name and the address are the values of the cluster-settings, and
   `test_the_site_record_of_the_agents_is_that_of_the_cluster_settings`
   checks that they match. No agent enrolls before this record is applied.
3. Done by its own step: the account user, made once with `admin` from
   `account-user.json` (see "Accounts"), and the four Secrets of the
   settings ("Secrets").
4. The second merge, the owner's, because it sets the retention, lists
   `../wazuh-settings/ks.yaml` in the apps of homelab-wsl-02, with:
   - the three Secrets of the settings, listed in
     `../wazuh-settings/accounts/kustomization.yaml` and
     `../wazuh-settings/app/kustomization.yaml`, and the Secret of the
     converter in `../converter/app`, not listed;
   - the output of `classlist.py write` for `security-wazuh-accounts` and
     `security-wazuh-settings`.

   What its first run does, in this order:
   1. Flux applies `security-wazuh-accounts` after `security-wazuh-server`
      is Ready: the two Secrets of step 1, its ConfigMap, the network rule
      of both steps and the Job `wazuh-accounts-<hash>`. The Kustomization
      waits for the Job (`wait: true`, `timeout: 15m`), and the Job has 600
      seconds (`activeDeadlineSeconds`) and no retry (`backoffLimit: 0`).
   2. Step 1 waits up to 300 seconds while the indexer gives no answer or
      answers 503: `security-wazuh-server` has `wait: false`, so Flux can
      apply the Job while the indexer starts or restarts. A 401 stops it at
      once. Step 1 logs in as the account user, which exists. It never changes
      that user, and a name of it in `accounts.json` stops the run. It makes
      the roles `tnwks-settings` and `tnwks-converter`, their users with the
      hashes of `wazuh-accounts-hashes`, and their role mappings, each with
      its mark, then reads them back.
   3. Flux then applies `security-wazuh-settings`: the Secret of step 2,
      its ConfigMap, the Job `wazuh-settings-git-<hash>` and the CronJob.
      The Job has 1800 seconds and no retry.
   4. Step 2 waits up to 300 seconds for the login of `tnwks-settings`,
      writes the four policies of tnwks, sets `engine.index_raw_events` to
      `false`, and moves each index of the four streams from its shipped
      policy to the policy of tnwks of its stream (`change_policy`). ISM
      applies such a move at the next run of the job of the index, not at
      the end of the state: the policies of tnwks have the states and the
      actions of the shipped ones (`hot` with a rollover, `delete` with a
      delete), and ISM marks a change of policy safe, and applies it at
      once, when the current state has the same actions in both
      ([isSafeToChange](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/util/ManagedIndexUtils.kt#L467-L487),
      [shouldChangePolicy](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/util/ManagedIndexUtils.kt#L428-L438)).
      Step 2 waits up to 1200 seconds until explain names the policy of
      tnwks on each index, then writes `wazuh-settings/indices.json`.
   5. The CronJob runs step 2 again each day at 08:22 UTC.

   After it, check that:
   - `flux-admission` is Ready, then the parent of the apps on both sites
     (the same order as after the first merge);
   - `security-wazuh-accounts` is Ready, and the Job of step 1 is Complete;
     its log ends with the read back and `done`;
   - `security-wazuh-settings` is Ready, and the Job
     `wazuh-settings-git-<hash>` is Complete; its log names the four
     policies, the raw events setting and a move of each index, and ends
     with `done`;
   - `wazuh-settings/indices.json` on the volume of results has `moves`
     `applied`, and `off_policy`, `no_policy` and `failed` at 0;
   - no `KubeJobFailed`.

   A failed Job of step 1 keeps `security-wazuh-accounts` not Ready, so
   Flux applies nothing of step 2; `FluxResourceNotReady` fires after an
   hour, and `KubeJobFailed` 15 minutes after the failure. Read its log,
   fix the cause, then delete the Job: Flux makes it again and it runs
   again. A failed Job of step 2 fires `KubeJobFailed` 15 minutes after
   the failure ("What step 2 does"); with `failed` above 0, read the `info`
   of the ISM explain API of each failed index, fix the cause, and retry
   the index as `admin`.
5. The owner checks each point of "Not yet proven on a cluster": the path
   to the feed after the first merge, the rights of step 2 at its first
   run (the moves and the explain calls of the Job of the second merge),
   the first rollover of each stream on the policy of tnwks (`rollover_age`
   after the second merge, through the daily counts of `indices.json`,
   with `failed` at 0), and the files of the last point at the first
   start.

## The way back

For the first merge: a revert of the merge that lists `ks.yaml` also reverts, in the same pull
request, what `classlist.py write` wrote for the two Kustomizations (in
`policy.yaml` and in the name list of `kubernetes/clusters/wsl-02/apps.yaml`),
and the pinned listing in `security/tests/test_wazuh_manifests.py`. Without
them, `classlist.py check` and the test fail. Flux then deletes both
Kustomizations and what they applied: the four Secrets, the certificates
and issuers, the network rules, the Service of the agents (its address
leaves the BGP advertisement), the workloads and the PVC of the dashboard.
The storage class deletes the volume of a deleted PVC (`reclaimPolicy:
Delete`), so the dashboard's configuration is lost. Flux leaves the PVCs of
the StatefulSets (`wazuh-manager-master-wazuh-manager-master-0`,
`wazuh-indexer-wazuh-indexer-0`): the StatefulSet controller makes them,
they are not in the inventory of Flux, and the default retention of a
StatefulSet (`whenDeleted: Retain`) keeps them when Flux deletes the
StatefulSet, with the stored passwords and the data. It also
leaves the Secrets that cert-manager made (`wazuh-ca` and the six leaf
Secrets), because nothing gives them an owner reference to their
Certificates. The owner deletes them, and keeps them only for a return with
the same four Secrets, because the vendor's parts keep what they stored at
their first start.

For the second merge: a revert of the merge that lists
`../wazuh-settings/ks.yaml` also reverts, in the same pull request, what
`classlist.py write` wrote for its two Kustomizations and the pinned
listings in `security/tests/test_wazuh_manifests.py`, as for the first
merge. It also removes the four SOPS files of the settings from git: a
return brings them back, with the same values or new ones (step 1 sets the
hashes again at each run). Flux then deletes both Kustomizations and what
they applied: the three Secrets, the ConfigMaps, the network rule, the Jobs
with their logs, and the CronJob. Nothing of the indexer goes, and no data
is lost: the revert deletes no index. What stays in the indexer:

- the four policies of tnwks, and each index on them. ISM keeps applying
  their ages to those indices, with the role `tnwks-settings`, and gives
  each new backing index of a rollover the policy of tnwks (priority 1);
- the users, roles and role mappings of `accounts.json`, and the account
  user;
- `engine.index_raw_events` at `false`, the default at the tag.

To stop the ages, the owner, as `admin`, works in this order: move each
index of the four streams off the policies of tnwks (`change_policy`, for
example to the shipped policy of its stream), and wait until explain names
the new policy on each; delete the four policies; then delete the users,
roles and role mappings of `accounts.json` through the Security API, or
keep them for a return. Not the other way round: ISM runs the steps of a
policy of tnwks with the role names stored with the policy, so after a
delete of the role `tnwks-settings` each step of those policies fails for
lack of rights, and the indices stay on them.

The file `wazuh-settings/indices.json` stays on the volume of results with
the `time` of its last run.

## Not yet proven on a cluster

These files are written from the vendor's sources at the tag, and checked
by the tests below. The first run on site 2 shows these points:

- The Content Manager of the indexer reaches its feed through
  `wazuh-indexer-feed` only (the snapshot download can use another host).
- Step 2 needs no ISM action beyond the five of its role on site 2. Each ISM action
  that moves or explains an index also runs
  `indices:admin/opensearch/ism/managedindex` on the index with the rights
  of the caller ([add](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/addpolicy/TransportAddPolicyAction.kt#L166),
  [change](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/changepolicy/TransportChangePolicyAction.kt#L129-L131),
  [explain](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/explain/TransportExplainAction.kt#L413-L415)),
  which the ISM index actions of the role give.
- The first rollover and delete of each stream on site 2, at its real
  ages. A test on a throwaway indexer of the pinned image ran them at ages
  of minutes ("Accounts"): the rollover and the delete with the role
  `tnwks-settings`, for an index moved by `change_policy` and by `add`,
  and the new backing index of a rollover on the policy of tnwks by its
  `ism_template`, run with the same role. The streams of findings and of
  metrics were not in that test: the indexer had made no backing index of
  them yet.
- Whether the pinned image writes the audit log of the security plugin, and
  whether the manager writes `alerts.json` or `archives.json` by default.

## Tests

- `security/tests/test_wazuh_manifests.py`: the copy of the vendor's files
  against its sums and `server/UPSTREAM.md`, the files that `server/` builds,
  the images by digest, the node selector and the memory lines against the
  table above, nothing privileged, no `0.0.0.0/0` and no `world` egress, the
  Secrets that nothing in git holds and the SOPS file of each, the Secret of
  the converter that no kustomization lists yet, no substitution of Flux in
  the hashes ("Secrets"), and the configuration of the manager.
  A render of `server/` with the patches of `ks.yaml` runs when `kustomize`
  is installed.
- `security/tests/test_wazuh_settings.py`: the two steps against a fake
  indexer API; the pin of each age; the hash in the name of each Job; each
  Secret in the one pod that uses it; the guard of a renewal; no password
  and no hash in git.
