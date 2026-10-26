locals {
  # Only VLANs flagged enabled get provisioned; enabled=false keeps a
  # definition in git without creating an SVI.
  active_vlans = { for k, v in var.vlans : k => v if coalesce(v.enabled, true) }

  # VLANs that additionally want a DHCP server.
  dhcp_vlans = { for k, v in local.active_vlans : k => v if v.dhcp != null }

  # Flatten VLAN static mappings into one map so each lease is an independent
  # Terraform address: "<vlan>/<hostname>".
  dhcp_leases = merge([
    for vlan_key, vlan in local.dhcp_vlans : {
      for lease_key, lease in coalesce(vlan.dhcp.leases, {}) :
      "${vlan_key}/${lease_key}" => {
        vlan_key    = vlan_key
        hostname    = lease_key
        address     = lease.address
        mac_address = lease.mac_address
        comment     = coalesce(lease.comment, lease_key)
      }
    }
  ]...)

  # Flatten address-groups into one member-per-resource map.
  address_list_members = merge([
    for list_name, members in var.address_lists : {
      for member_key, address in members :
      "${list_name}/${member_key}" => {
        list    = list_name
        address = address
        comment = member_key
      }
    }
  ]...)

  # RouterOS has no port-group primitive. Render each VyOS port-group into the
  # comma-joined dst-port string RouterOS accepts.
  port_list_strings = { for name, ports in var.port_lists : name => join(",", ports) }

  # One RouterOS interface-list per zone. Zone membership is how the ported
  # rules keep the VyOS from/to semantics: forward-chain rules match on
  # in_interface_list / out_interface_list rather than raw interface names.
  zone_interfaces = merge(
    { for k, v in local.active_vlans : k => "${var.bridge_name}-vlan${v.vlan_id}" },
    # The native (untagged) network is routed on the bridge interface itself.
    var.native_vlan == null ? {} : { (var.native_vlan.zone) = var.bridge_name },
    # Keyed by VyOS zone (vpn, vpn-mobile), not interface name (wg01, wg02):
    # the zone-pair and input rules are written against zone names.
    { for k, v in var.wireguard_interfaces : coalesce(v.zone, k) => k },
  )

  # Every zone's interface list. The outside zone always has one, even with
  # its VLAN disabled: it holds the WAN port (outside_wan).
  zone_names = toset(concat(keys(local.zone_interfaces), var.outside_zone == null ? [] : [var.outside_zone]))

  # Zone pairs whose both sides actually exist. A VyOS pair referencing a
  # disabled VLAN is dropped rather than silently creating an orphan chain.
  active_zone_policies = {
    for k, v in var.zone_policies : k => v
    if contains(local.zone_names, v.from) && contains(local.zone_names, v.to)
  }

  # Flatten the accept rules. Sort key is "<pair-key>/<NNN>" so the sorted map
  # iteration order equals intended firewall order.
  zone_rules = merge([
    for pair_key, pair in local.active_zone_policies : {
      for idx, rule in coalesce(pair.rules, []) :
      "${pair_key}/${format("%03d", idx)}" => {
        pair_key         = pair_key
        from             = pair.from
        to               = pair.to
        comment          = "${pair.from} -> ${pair.to}: ${rule.comment}"
        protocol         = rule.protocol
        src_address_list = rule.src_address_list
        dst_address_list = rule.dst_address_list
        src_address      = rule.src_address
        dst_address      = rule.dst_address
        # dst_port_list resolves through port_lists; dst_port is a literal.
        dst_port = rule.dst_port_list != null ? local.port_list_strings[rule.dst_port_list] : rule.dst_port
      }
    }
  ]...)

  # The terminating default for each pair, evaluated after that pair's
  # accepts. Usually a drop; an accept for the pairs VyOS defaulted open.
  zone_defaults = {
    for pair_key, pair in local.active_zone_policies :
    pair_key => {
      from    = pair.from
      to      = pair.to
      action  = pair.default_action
      comment = "${pair.from} -> ${pair.to}: default ${pair.default_action}"
      log     = pair.default_action == "drop" && coalesce(pair.log_default, true)
    }
  }
}
