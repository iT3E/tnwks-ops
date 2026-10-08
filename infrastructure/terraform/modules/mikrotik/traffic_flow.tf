# WAN flow export for tnwks-ops monitoring/flow-collector.
# Chain: RB5009 IPFIX -> Windows host udp/2055 (Start-TnwksUdpRelay.ps1)
#        -> WSL socat (tnwks-lan-bridge@netflow) -> MetalLB 10.5.0.204.
# The collector is router-agnostic: VyOS sends sFlow today, this sends IPFIX,
# and both feed the same wan_client_* metrics and alert line.
# Requires var.fasttrack = false, or bulk traffic never reaches traffic-flow.

resource "routeros_ip_traffic_flow" "this" {
  count = var.flow_export == null ? 0 : 1

  # No `enabled` here: provider v1.99.1 (latest release) lacks the attribute.
  # It was added upstream in e97e9187 (unreleased). Until then the cutover
  # runbook enables it by hand: /ip traffic-flow set enabled=yes
  # TODO: add `enabled = true` once the provider releases > v1.99.1.
  interfaces            = coalesce(var.flow_export.interfaces, var.wan_interface)
  active_flow_timeout   = var.flow_export.active_flow_timeout
  inactive_flow_timeout = var.flow_export.inactive_flow_timeout
}

resource "routeros_ip_traffic_flow_target" "collector" {
  count = var.flow_export == null ? 0 : 1

  dst_address = var.flow_export.collector_address
  port        = var.flow_export.collector_port
  src_address = var.flow_export.src_address
  version     = var.flow_export.version

  depends_on = [routeros_ip_traffic_flow.this]
}
