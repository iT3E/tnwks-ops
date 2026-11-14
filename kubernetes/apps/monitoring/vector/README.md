# Vector: the log pipeline

Three parts move logs to Loki. This file says what each one takes in, where it
listens and what it reports about itself.

| Part | Directory | Runs on | What it does |
| --- | --- | --- | --- |
| The aggregator | `aggregator/` (`ks.yaml`) | Each cluster, one pod | Takes in pod logs and the router's syslog, and writes them to the Loki of its cluster. Takes in the audit log of the API server, and writes the security streams |
| The agent | `agent/` (`ks-agent.yaml`) | Each cluster that lists it, one pod on each node | Reads pod logs on its node and sends them to the aggregator. On the WSL clusters, Job pods only (`VECTOR_AGENT_POD_SELECTOR`) |
| The audit reader | `audit-reader/` (`ks-audit-reader.yaml`) | Each cluster that lists it, one pod on the control plane node | Reads the audit log of the API server on its node and sends it to the audit source of the aggregator |

The config of the aggregator is one file, `aggregator/config/vector.yaml`,
for every cluster. A value that differs between clusters is a variable of
`cluster-settings`.

## The ports of the aggregator

| Port | What it is | Who reaches it |
| --- | --- | --- |
| 6000 | Pod logs from the agent (a `vector` source) | The Service `vector-aggregator`, a LoadBalancer |
| 6001/UDP | The router's syslog | The same Service, through the LAN bridge of the WSL host |
| 8686 | The Vector API | The same Service |
| 6010 | The audit log of the API server (a `vector` source) | The Service `vector-aggregator-audit`, a ClusterIP |
| 9598 | Vector's own metrics | The pod only. Prometheus reads it through the PodMonitor `vector-aggregator` |

The metrics port and the audit port are not on the Service
`vector-aggregator`, because that Service is a LoadBalancer. A new port goes
on it only when a sender outside the cluster needs it.

## The security streams

A security stream is a log that the security platform reads
(`security/README.md`). The aggregator of each cluster writes the audit log
and the router log to the Loki of homelab-wsl-02, and the security converter
writes the findings there, which keeps a stream with the label `security_stream`
for 90 days. This section is the contract for a part that sends such a log or
reads it.

| Stream | Stream labels in Loki | What a line is |
| --- | --- | --- |
| The audit log of an API server | `security_stream="k8s-audit"`, `k8s_cluster="<cluster>"` | One audit event, the JSON line as the API server wrote it. The time of the line is the `stageTimestamp` of the event |
| The router log | `security_stream="routeros"`, `k8s_cluster="<cluster>"` | One syslog message as JSON, the same as in the stream `job="routeros"` |
| The findings of Prowler and the API checks | `security_stream="findings"`, `source="<prowler or api-check>"`, `system="<system>"` | One finding that fails, a part of its OCSF record as JSON. The security converter of homelab-wsl-02 sends it, not Vector. Its contract is `security/converter/README.md`, "The stream `findings`" |

`k8s_cluster` is the name of the cluster that the log comes from
(`CLUSTER_NAME`: `homelab-wsl`, `homelab-wsl-02`), also for a line of
homelab-wsl that rests in the Loki of homelab-wsl-02. A security stream of
Vector has these two labels and no other, so each one is two streams in Loki
at most. The stream `findings` has no `k8s_cluster`: its `system` is a name of
`security/registry/systems.yaml`, and it is one stream for each source and
system.

| What | Address |
| --- | --- |
| The audit source, for the audit reader of the same cluster | `http://vector-aggregator-audit.monitoring.svc.cluster.local:6010` (the Vector protocol, version 2) |
| The Loki of the security streams, from homelab-wsl-02 | `http://loki-gateway.monitoring.svc.cluster.local` |
| The same Loki, from homelab-wsl | `https://security-loki.monitoring.svc.cluster.local`, with the client certificate `security-loki-client` (`../security-loki-proxy/README.md`) |

The aggregator takes the Loki address from `SECURITY_LOKI_ENDPOINT` of
`cluster-settings`. A cluster that does not set it writes to its own Loki.

The two streams of Vector behave in two ways when that Loki is away, and
each way is on purpose:

| Stream | Path in `vector.yaml` | When Loki is away |
| --- | --- | --- |
| The audit log | `audit_source`, `audit_time`, `loki_security_audit` | The sink waits and tries again, and each part before it waits too. No line is lost while the audit files on the node last. An acknowledgement goes back to the sender only when Loki took the line |
| The router log | `routeros_syslog`, `loki_security_routeros` | The sink holds 500 events and then drops the newest (`vector_buffer_discarded_events_total`). The sink `loki_routeros` to the Loki of the own cluster does not wait |

Three rules keep the other logs safe:

- **A sink that can wait has a source of its own.** A sink with
  acknowledgements, or with a buffer that blocks, holds its source when its
  store is away. `audit_source` feeds `loki_security_audit` only.
- **A second sink on a source that exists drops when it is full.** It sets
  `buffer.when_full: drop_newest`, as `loki_security_routeros` does.
- **Loki refuses a line that is older than 7 days, and a line that is more
  than one hour older than the newest line of its stream.** Vector does not
  send a refused request again. A refusal shows as
  `vector_component_errors_total{component_id="loki_security_audit",error_type="request_failed"}`.
  The audit reader sends the oldest file first, so a backlog arrives in the
  order of its events.

## The audit reader

The API server of a Talos control plane node writes its audit log to
`/var/log/audit/kube/` on the node: the live file `kube-apiserver.log`, and
up to 10 rotated files of 100 MB. The audit reader is a Vector pod on that
node, with the image of the aggregator. It reads the files and sends each
line to `vector-aggregator-audit:6010`.

| Property | Value |
| --- | --- |
| Where it runs | The control plane node (node selector and toleration), namespace `monitoring` |
| User | 65534, the user that writes the audit files. No capability, a read-only root file system, no token of a ServiceAccount |
| Audit files | The host path `/var/log/audit/kube`, read-only |
| Positions | The host path `/var/lib/vector-audit`. An init container gives the directory to user 65534: it runs as root with the capability `CHOWN` only |
| Sending | 200 events in a batch, one request at a time, a buffer of 200 events, 2 threads |
| Memory | A request of 64 MiB and a limit of 192 MiB. On homelab-wsl-02 the reader used 115.6 MiB at its peak while it sent its first backlog of about 880,000 lines |

The audit files on the node are the buffer of the path. The reader moves its
position in a file only when Loki took the line, so when the aggregator or
Loki is away the reader stops, and it goes on from its position after. It
loses a line only when the API server removes a file that the reader did not
finish: after 10 rotated files, or after 30 days.

At its first start on a node, the reader sends each file that is there,
the oldest first.

A cluster starts its audit stream in three steps:

1. Apply the audit policy of the cluster (`talos/README.md`), so the API
   server writes the events that the stream is for.
2. List `ks-audit-reader.yaml` in the apps of the cluster.
3. In the same pull request, add `stream` to the `sources` of the cluster in
   `security/registry/systems.yaml`, and run the generator
   (`security/README.md`).

## Metrics

The aggregator has an `internal_metrics` source and a `prometheus_exporter`
sink. The Prometheus of each cluster scrapes them once a minute. Each series
starts with `vector_`, and `component_id` is the name of the source, the
transform or the sink in `vector.yaml`.

| Series | What it counts |
| --- | --- |
| `vector_component_received_events_total` | The events that a source or a sink took in |
| `vector_component_sent_events_total` | The events that a source passed on, or that a sink delivered |
| `vector_component_errors_total` | The errors of a component, by `error_type` and `stage`. For a sink, a request that its store refused for good is `error_type="request_failed"` |
| `vector_component_discarded_events_total` | The events that a component dropped. `intentional="false"` is a loss |
| `vector_buffer_discarded_events_total` | The events that the buffer of a sink dropped because it was full |
| `vector_http_client_responses_total` | The answers that a sink got, by HTTP `status` |

The sink also serves one series that is not Vector's own:
`homelab_agent_platform_result_timestamp_seconds{pod_namespace, app, check,
result}`, the time of the newest result line of a pod in a namespace
`agents-*` (a line that starts with `<check> result=<result>`). The
transforms `agent_platform_results` and `agent_platform_result_metrics` make
it, and the alert `WorkflowAppDrift` reads it
(`docs/apps/agent-platform.md`, "Alerts"). Each sample carries the time of
its line, so Prometheus holds it for 5 minutes, its lookback, and the sink
drops the series a minute after its last line. A rule on it reads
`max_over_time` over a window longer than the time between two lines.

Two settings shape these series:

- **A counter starts at its first event.** Vector makes the series of an
  error when the first error happens, so its first value is 1, and
  `increase()` over a new series is 0. A rule on such a counter must also
  compare it with its value of some minutes before.
- **A metric stays for one hour after its last change**
  (`expire_metrics_secs`). The default of Vector is five minutes, and a
  counter that went away that fast could come back at the same value, which
  no rule can see.

The PodMonitor drops the histogram buckets (26 for each histogram), which
leaves about 170 series for each aggregator, and it drops Vector's `host`
label, which repeats `pod`.
