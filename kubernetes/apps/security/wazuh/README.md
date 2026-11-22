# wazuh

The Wazuh server, step 2 of the security platform, on homelab-wsl-02 only:
one manager node, one indexer node and the dashboard, at the tag
`v5.0.0-rc2`. The agents of the hosts report to the manager, and the
converter (`../converter`) reads the state of the hosts from the indexer.

No cluster lists this app yet. It starts nothing until a pull request adds
`ks.yaml`, and then `../wazuh-settings/ks.yaml`, to
`kubernetes/clusters/wsl-02/apps/kustomization.yaml` (see "Before the merge
that lists it").

## How it is built

The vendor's manifests of the server are in this repository, in
`server/upstream/`: a byte-for-byte copy of the files of
`wazuh/wazuh-kubernetes` at `v5.0.0-rc2` that this app uses, with the
vendor's licence (`server/UPSTREAM.md` lists them, the commit, and how to
update). This repository patches them. Flux builds both Kustomizations
(`ks.yaml`) from the repository itself, so each object of the server is in
git, and a change of the vendor's files comes as a reviewed diff.

| Kustomization | What it applies |
| --- | --- |
| `security-wazuh` | `app/`: the certificates, the network rules, the agents Service and the configuration of the manager |
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
| `wazuh-accounts` (`../wazuh-settings`, step 1, a Job) | 64 MiB | 128 MiB |
| `wazuh-settings` (`../wazuh-settings`, step 2, a Job and a CronJob) | 64 MiB | 128 MiB |

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
| `wazuh-agents` | world, a node of either cluster (through the LAN bridge) | The manager on 1517 |
| `wazuh-indexer-readers` | The two steps of the settings and the converter | The indexer on 9200 |

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
A restart of the indexer drops a move of ISM policy that waits, so step 2 of
the settings starts no move in the hour before a renewal (see "Retention").

The certificate of the agent listener names `${WAZUH_AGENTS_HOST}` and
`${WAZUH_AGENTS_LB_ADDR}`. An enrollment token pins the CA, so a rebuild of
the cluster, which makes a new CA, means each agent enrolls again. The
stricter later step is a CA in SOPS, as `observability-pki` has.

## Secrets

The owner makes each one as a SOPS file in the app of the table, and lists
it in that app's `kustomization.yaml`. No session reads them, and no
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
| `wazuh-converter-reader` | `../converter` | `password` | The password of `tnwks-converter` | The converter only. The change that adds the Wazuh source to the converter adds it |

The password rule of the Wazuh images: 12 to 64 characters from
`A-Z a-z 0-9 . , _ + : @ % ^ = ~ -`, with at least one lowercase letter,
one uppercase letter, one digit and one symbol
([credentials](https://github.com/wazuh/wazuh-kubernetes/blob/v5.0.0-rc2/docs/ref/credentials.md)).
The users of `accounts.json` follow the same rule, and step 2 refuses a
password of `tnwks-settings` that breaks it. The owner makes each hash from
its password with a bcrypt tool, as the hash tool of the OpenSearch security
plugin (`plugins/opensearch-security/tools/hash.sh`).

The vendor's parts store their passwords at their first start: a later
change of one of their Secrets changes nothing, and a password changes with
the vendor's `password-tool.sh` (same page). A password of a user of
`accounts.json` changes in one merge of the owner: the new password in the
Secret of the part that logs in, and its hash in `wazuh-accounts-hashes`.
Then the owner deletes the Job of step 1, and Flux runs it again.

## Accounts

The indexer has the vendor's accounts (`admin`, `kibanaserver`,
`wazuh-manager`) from `indexer-credentials`. Step 1 of the settings
(`../wazuh-settings/accounts`, the Job `wazuh-accounts-<hash>`) makes the accounts of
this repository from `../wazuh-settings/app/config/accounts.json`:

| User | Role | For |
| --- | --- | --- |
| `tnwks-settings` | `tnwks-settings`: the five ISM actions that step 2 calls ([`policy/get`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/getpolicy/GetPolicyAction.kt#L13), [`policy/write`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/indexpolicy/IndexPolicyAction.kt#L13), [`managedindex/add`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/addpolicy/AddPolicyAction.kt#L14), [`managedindex/change`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/changepolicy/ChangePolicyAction.kt#L14), [`managedindex/explain`](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/explain/ExplainAction.kt#L13), each under `cluster:admin/opendistro/ism/`), and the ISM index actions on the patterns of the four streams of "Retention". `policy/write` lets the user write a policy under any ID: the role can not limit the ID, so step 2 refuses to write a shipped ID. Also the raw events setting, with a write of the settings document in `.wazuh-settings` (the Setup plugin indexes it with the rights of the caller, which OpenSearch runs as a bulk request: the cluster action `indices:data/write/bulk`, and `indices:data/write/index` and `indices:data/write/bulk[s]` on that index, [SettingsIndex.java](https://github.com/wazuh/wazuh-indexer-plugins/blob/v5.0.0-rc2/plugins/setup/src/main/java/com/wazuh/setup/index/SettingsIndex.java#L106-L113)), and the index stats of the two streams that hold no document. No Security API, and no read of a document | Step 2 |
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

It runs as the account user, which the owner makes once, by hand, with
`admin`, from `../wazuh-settings/app/config/account-user.json`: an internal
user mapped to the reserved role `security_rest_api_access`, with its name
and password in `wazuh-accounts-user`. The file holds no hash, and step 1
never reads it. The owner applies it through the Security API: a `PUT` of
the user with the hash of its password, and a `PUT` of the role mapping of
`security_rest_api_access` with the user. The role mappings of the image have
no mapping for that role (as the defaults of the security plugin,
[roles_mapping.yml](https://github.com/opensearch-project/security/blob/3.6.0.0/config/roles_mapping.yml)),
so the `PUT` makes it; if a mapping exists, the owner adds the user to its
`users` and keeps the others. The image of the indexer gives the
Security API to that role and to `all_access` only
([opensearch.yml](https://github.com/wazuh/wazuh-indexer/blob/v5.0.0-rc2/build-scripts/docker/config/opensearch.yml#L49-L51)). That role can make, change and delete users, roles
and role mappings that are neither reserved nor hidden, so it can also give
itself wide rights on the data
([API permissions, OpenSearch](https://github.com/opensearch-project/documentation-website/blob/d4a12efae197ed01306f752eb1406072ad18bb58/_security/access-control/api.md#L67)).
Only the pod of step 1 mounts its Secret.

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
  This path does not read the `policy_id` setting of the template.

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
`wazuh-settings`, at 07:17 UTC). Both run one pod. In this order, it:

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
   tnwks of their stream, with those that have no policy, and the documents
   of the raw events and active responses streams, from the index stats. It
   writes them to `wazuh-settings/indices.json` on the volume of results
   (`security/converter/README.md`, "The count file of the Wazuh settings").

A run fails, and `KubeJobFailed` fires, at the first error, and when a move
does not apply in time (after it writes the counts).

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
| The manager for the agents | `${WAZUH_AGENTS_LB_ADDR}` port 1517 (TCP), with the name `${WAZUH_AGENTS_HOST}`. Both are keys of the cluster-settings of homelab-wsl-02 that the owner adds |
| The manager API | `https://wazuh-api.security.svc.cluster.local:55000`, in the cluster only. How the enrollment step of the agent roles reaches it belongs to the agents workstream |

## Before the merge that lists it

1. The owner adds `WAZUH_AGENTS_LB_ADDR` (a free address of the
   LoadBalancer pool) and `WAZUH_AGENTS_HOST` to the cluster-settings of
   homelab-wsl-02.
2. The owner makes the Secrets of this app ("Secrets") and lists them.
3. A first merge adds `ks.yaml` to the apps of homelab-wsl-02, and runs
   `python3 kubernetes/apps/flux-system/admission/classlist.py write`. Both
   Kustomizations build a path of this repository under
   `kubernetes/apps/security/`, so the admission policy `flux-child-scope`
   (`../../flux-system/admission`) lists both in its class.
4. After the first start of the server, the owner makes the account user
   with `admin` from `account-user.json` (see "Accounts"), and the Secrets
   of `../wazuh-settings`.
5. A second merge, the owner's, adds `../wazuh-settings/ks.yaml`. It first
   applies the retention policies, and ISM deletes the data older than its
   ages (see "The merges that delete data").
6. The owner checks each point of "Not yet proven on a cluster": the path
   to the feed after the first merge, the rights of step 2 at its first
   run, the policy of a new backing index at the first rollover of each
   stream (`rollover_age` after the second merge), and the files of the
   last point at the first start.

## Not yet proven on a cluster

These files are written from the vendor's sources at the tag, and checked
by the tests below. The first run on site 2 shows these points:

- The Content Manager of the indexer reaches its feed through
  `wazuh-indexer-feed` only (the snapshot download can use another host).
- Step 2 needs no ISM action beyond the five of its role. Each ISM action
  that moves or explains an index also runs
  `indices:admin/opensearch/ism/managedindex` on the index with the rights
  of the caller ([add](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/addpolicy/TransportAddPolicyAction.kt#L166),
  [change](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/changepolicy/TransportChangePolicyAction.kt#L129-L131),
  [explain](https://github.com/opensearch-project/index-management/blob/3.6.0.0/src/main/kotlin/org/opensearch/indexmanagement/indexstatemanagement/transport/action/explain/TransportExplainAction.kt#L413-L415)),
  which the ISM index actions of the role give.
- A backing index that a rollover makes takes the policy of tnwks of its
  stream: ISM picks it by its `ism_template` priority when `tnwks-settings`
  holds `indices:admin/opensearch/ism/managedindex` on the stream ("Which
  policy an index of a stream starts on", above).
- Whether the pinned image writes the audit log of the security plugin, and
  whether the manager writes `alerts.json` or `archives.json` by default.

## Tests

- `security/tests/test_wazuh_manifests.py`: the copy of the vendor's files
  against its sums and `server/UPSTREAM.md`, the files that `server/` builds,
  the images by digest, the node selector and the memory lines against the
  table above, nothing privileged, no `0.0.0.0/0` and no `world` egress, the
  Secrets that nothing in git holds, and the configuration of the manager.
  A render of `server/` with the patches of `ks.yaml` runs when `kustomize`
  is installed.
- `security/tests/test_wazuh_settings.py`: the two steps against a fake
  indexer API; the pin of each age; the hash in the name of each Job; each
  Secret in the one pod that uses it; the guard of a renewal; no password
  and no hash in git.
