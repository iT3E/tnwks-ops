# BGP with the WSL sites (docs/wsl/multi-site.md, "BGP"). No VyOS counterpart.
#
# Each WSL distro runs FRR as its site's border router: it learns its own
# cluster's pod CIDRs and LoadBalancer addresses from the Cilium nodes and
# passes them here over its site tunnel (wg-site1, wg-site2), with itself as
# next hop. This router hands each site the other site's routes, so pod to
# pod across sites is routed with no NAT, and picks one site for a shared VIP
# that both announce.
#
# Filters: one input chain and one output chain per peer, one rule per allowed
# prefix range. A RouterOS filter chain rejects whatever no rule accepts, so a
# chain is an allow-list and the order of its rules doesn't matter as long as
# their prefixes don't overlap (Terraform doesn't order them).
#
# Graceful restart is not configurable here: the provider (v1.99.1) has no
# argument for it, and RouterOS 7's /routing/bgp/connection has no setting
# (the router advertises the capability itself, `gr` in /routing/bgp/session).
# The restart the design cares about, a Cilium agent's, is meant to be
# absorbed by graceful restart between Cilium and FRR in the distro, so it
# never reaches these sessions.

locals {
  bgp_peers = var.bgp == null ? {} : var.bgp.peers

  # One filter rule per allowed prefix range: "<peer>/<in|out>/<prefix>".
  bgp_filter_rules = merge([
    for peer_key, peer in local.bgp_peers : merge(
      {
        for e in peer.accept : "${peer_key}/in/${e.prefix}" => {
          chain      = "bgp-in-${peer_key}"
          prefix     = e.prefix
          min_len    = coalesce(e.min_len, tonumber(split("/", e.prefix)[1]))
          max_len    = coalesce(e.max_len, e.min_len, tonumber(split("/", e.prefix)[1]))
          local_pref = e.local_pref
          comment    = "${peer_key}: accept ${e.prefix}"
        }
      },
      {
        for e in peer.export : "${peer_key}/out/${e.prefix}" => {
          chain      = "bgp-out-${peer_key}"
          prefix     = e.prefix
          min_len    = coalesce(e.min_len, tonumber(split("/", e.prefix)[1]))
          max_len    = coalesce(e.max_len, e.min_len, tonumber(split("/", e.prefix)[1]))
          local_pref = null
          comment    = "${peer_key}: send ${e.prefix}"
        }
      },
    )
  ]...)
}

# RouterOS 7.20+ wants an explicit instance; connections name it.
resource "routeros_routing_bgp_instance" "this" {
  count = var.bgp == null ? 0 : 1

  name      = coalesce(var.bgp.instance, "as${var.bgp.as}")
  as        = tostring(var.bgp.as)
  router_id = var.bgp.router_id
  comment   = "WSL sites (terraform)"
}

resource "routeros_routing_filter_rule" "bgp" {
  for_each = local.bgp_filter_rules

  chain = each.value.chain
  rule = format(
    "if (dst in %s && %s) { %saccept }",
    each.value.prefix,
    each.value.min_len == each.value.max_len ? "dst-len == ${each.value.min_len}" : "dst-len in ${each.value.min_len}-${each.value.max_len}",
    each.value.local_pref == null ? "" : "set bgp-local-pref ${each.value.local_pref}; ",
  )
  comment = "${each.value.comment} (terraform)"
}

resource "routeros_routing_bgp_connection" "this" {
  for_each = local.bgp_peers

  name           = each.key
  instance       = routeros_routing_bgp_instance.this[0].name
  as             = tostring(var.bgp.as)
  hold_time      = var.bgp.hold_time
  keepalive_time = var.bgp.keepalive_time
  comment        = "${coalesce(each.value.comment, each.key)} (terraform)"

  local {
    role    = "ebgp"
    address = each.value.local_address
  }

  remote {
    address = each.value.remote_address
    as      = tostring(each.value.remote_as)
  }

  input {
    filter = "bgp-in-${each.key}"
  }

  output {
    filter_chain = "bgp-out-${each.key}"
  }

  # The chains exist before a session can come up. A named chain with no rules
  # would reject everything anyway, so this is about not starting a session
  # that then refreshes, not about safety.
  depends_on = [routeros_routing_filter_rule.bgp]
}
