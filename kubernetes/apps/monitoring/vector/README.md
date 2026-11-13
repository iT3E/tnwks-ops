# Vector: the log pipeline

Two parts move logs to Loki. This file says what each one takes in, where it
listens and what it reports about itself.

| Part | Directory | Runs on | What it does |
| --- | --- | --- | --- |
| The aggregator | `aggregator/` (`ks.yaml`) | Each cluster, one pod | Takes in pod logs and the router's syslog, and writes them to the Loki of its cluster |
| The agent | `agent/` (`ks-agent.yaml`) | Each cluster that lists it, one pod on each node | Reads pod logs on its node and sends them to the aggregator. On the WSL clusters, Job pods only (`VECTOR_AGENT_POD_SELECTOR`) |

The config of the aggregator is one file, `aggregator/config/vector.yaml`,
for every cluster. A value that differs between clusters is a variable of
`cluster-settings`.

## The ports of the aggregator

| Port | What it is | Who reaches it |
| --- | --- | --- |
| 6000 | Pod logs from the agent (a `vector` source) | The Service `vector-aggregator`, a LoadBalancer |
| 6001/UDP | The router's syslog | The same Service, through the LAN bridge of the WSL host |
| 8686 | The Vector API | The same Service |
| 9598 | Vector's own metrics | The pod only. Prometheus reads it through the PodMonitor `vector-aggregator` |

The metrics port is not on the Service, because the Service is a
LoadBalancer. A new port goes on that Service only when a sender outside the
cluster needs it.

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
