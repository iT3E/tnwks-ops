## ---------------------------------------------------------------------------------------------------------------------
## WSL site tunnels and BGP (docs/wsl/multi-site.md, "Network"). One WireGuard interface per site, each its own firewall
## zone, and an eBGP session per site to the distro's FRR over it. The router prefers site 1 for the shared VIPs.
## Hand-written: locals.tf is generated from the VyOS config. main.tf merges these into the module inputs.
##
## Keys: the router's private keys are wireguard_wg-site{1,2}_private_key in secrets.sops.yaml (the module's
## wireguard_public_keys output has their public halves for the distro side). Each distro's private key is
## wsl_site_wireguard_private_key in infrastructure/ansible/inventory/host_vars/<distro>/secrets.sops.yml.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  site_router_as = 64600

  # Shared service addresses (ingress VIP, MQTT): the same address on both clusters' Cilium LB IPAM. Only /32s are
  # accepted from it, so both sites' announcements of a VIP are the same prefix and local preference decides between
  # them. A shorter prefix from one site would win on length whatever its preference.
  site_vip_pool = "10.10.95.0/24"

  # The pool in two halves, one per primary site: a VIP in a site's half is preferred there (100) and taken over by
  # the other site (50) only while that site doesn't announce it. So an app's VIP lands on the site that runs it,
  # without a second trip through the tunnels. Halves, not per-/32 rules: a RouterOS filter chain stops at its first
  # accept, so overlapping rules would depend on their order.
  site_vip_halves = {
    "10.10.95.0/25"   = "wsl-site1" # homelab-wsl's apps (10.10.95.10: its internal ingress)
    "10.10.95.128/25" = "wsl-site2" # homelab-wsl-02's apps (10.10.95.200: its internal ingress)
  }

  wsl_sites = {
    wsl-site1 = {
      cluster         = "homelab-wsl"
      interface       = "wg-site1"
      host_address    = "10.10.91.142" # DESKTOP-HKE05KI on VLAN 910; the distro's tunnel leaves through it
      listen_port     = 51823
      router_address  = "10.10.33.1/30"
      peer_address    = "10.10.33.2"
      peer_public_key = "VIsJtdr7dHsCCIjeCywO0r58PjCMrVoVZY4mL99SqAY="
      peer_as         = 64611
      node_subnet     = "10.5.0.0/24"
      pod_cidr        = "10.46.0.0/16" # Cilium cluster-pool since 2026-11-06 (10.42 under Flannel)
    }
    wsl-site2 = {
      cluster         = "homelab-wsl-02"
      interface       = "wg-site2"
      host_address    = "10.10.91.112" # WSL-02 on VLAN 910 (wifi)
      listen_port     = 51824
      router_address  = "10.10.34.1/30"
      peer_address    = "10.10.34.2"
      peer_public_key = "BBqoyiwD8J448J0keluxMQxmLte8VGG3z9fJVoVYHwQ="
      peer_as         = 64612
      node_subnet     = "10.6.0.0/24"
      pod_cidr        = "10.44.0.0/16"
    }
  }

  # Internal zones whose clients reach the sites: the client VLAN, and the admin VPN (which the VyOS port lets reach
  # every internal zone).
  site_client_zones = ["unifi-frontend-910", "vpn"]

  # --- WireGuard -------------------------------------------------------------------------------------------------------
  # The distro dials in (WSL's NAT and address don't matter), so the router side has no endpoint. allowed_address is
  # everything the site routes: WireGuard drops packets from or to any other address on the interface.
  site_wireguard_interfaces = {
    for zone, s in local.wsl_sites : s.interface => {
      zone        = zone
      address     = s.router_address
      listen_port = s.listen_port
      mtu         = "1420"
      mgmt_port   = false
      comment     = "${s.cluster} site tunnel"
      peers = {
        "${s.cluster}-distro" = {
          public_key      = s.peer_public_key
          allowed_address = ["${s.peer_address}/32", s.node_subnet, s.pod_cidr, local.site_vip_pool]
          comment         = "${s.cluster} WSL distro (FRR)"
        }
      }
    }
  }

  # --- Firewall --------------------------------------------------------------------------------------------------------
  site_address_lists = merge(
    { for zone, s in local.wsl_sites : replace(zone, "-", "_") => { nodes = s.node_subnet, pods = s.pod_cidr } },
    { wsl_shared_vips = { pool = local.site_vip_pool } },
  )

  # Clients -> each site, to its nodes and pods and to the shared VIPs; site <-> site for Cluster Mesh (nodes and pods
  # only). Replies are the global established/related accept. Nothing is opened from a site to the LAN or the internet:
  # the clusters' own egress (cameras, internet) keeps going out through Docker, WSL and Windows' NAT.
  site_zone_policies = merge(
    {
      for i, pair in setproduct(local.site_client_zones, sort(keys(local.wsl_sites))) :
      format("%03d-%s-%s", 200 + 10 * i, pair[0], pair[1]) => {
        from    = pair[0]
        to      = pair[1]
        comment = "${pair[0]} -> ${pair[1]}"
        rules = [
          {
            comment          = "${local.wsl_sites[pair[1]].cluster} nodes and pods"
            dst_address_list = replace(pair[1], "-", "_")
          },
          {
            comment          = "shared VIPs (BGP picks the site)"
            dst_address_list = "wsl_shared_vips"
          },
        ]
      }
    },
    # The mobile VPN reaches the internal names (the shared VIPs) and nothing else at the sites. Its default accept
    # to VLAN 910 covered the old per-host addresses.
    {
      for i, zone in sort(keys(local.wsl_sites)) :
      format("%03d-vpn-mobile-%s", 260 + 10 * i, zone) => {
        from    = "vpn-mobile"
        to      = zone
        comment = "vpn-mobile -> ${zone}"
        rules = [{
          comment          = "shared VIPs (BGP picks the site)"
          dst_address_list = "wsl_shared_vips"
        }]
      }
    },
    {
      "300-wsl-site1-wsl-site2" = {
        from    = "wsl-site1"
        to      = "wsl-site2"
        comment = "wsl-site1 -> wsl-site2"
        rules = [{
          comment          = "Cluster Mesh: homelab-wsl nodes and pods to homelab-wsl-02's"
          src_address_list = "wsl_site1"
          dst_address_list = "wsl_site2"
        }]
      }
      "310-wsl-site2-wsl-site1" = {
        from    = "wsl-site2"
        to      = "wsl-site1"
        comment = "wsl-site2 -> wsl-site1"
        rules = [{
          comment          = "Cluster Mesh: homelab-wsl-02 nodes and pods to homelab-wsl's"
          src_address_list = "wsl_site2"
          dst_address_list = "wsl_site1"
        }]
      }
    },
  )

  # To the router: each site's WireGuard from its Windows host only, and BGP from its distro's tunnel address only.
  site_input_rules = merge(
    {
      for i, zone in sort(keys(local.wsl_sites)) : format("%03d-%s-wireguard", 610 + 20 * i, zone) => {
        comment           = "WireGuard ${local.wsl_sites[zone].interface} from ${local.wsl_sites[zone].cluster}'s Windows host"
        protocol          = "udp"
        dst_port          = tostring(local.wsl_sites[zone].listen_port)
        in_interface_list = "unifi-frontend-910"
        src_address       = local.wsl_sites[zone].host_address
      }
    },
    {
      for i, zone in sort(keys(local.wsl_sites)) : format("%03d-%s-bgp", 620 + 20 * i, zone) => {
        comment           = "BGP from ${local.wsl_sites[zone].cluster}'s distro FRR"
        protocol          = "tcp"
        dst_port          = "179"
        in_interface_list = zone
        src_address       = local.wsl_sites[zone].peer_address
      }
    },
  )

  # --- BGP -------------------------------------------------------------------------------------------------------------
  # From each site: its node subnet (exactly), its per-node pod CIDRs (/16 to /24 inside its range), and shared VIPs
  # (/32 only, with the site's local preference). To each site: the other site's node subnet and pod CIDRs, so pod to
  # pod across sites is routed. The VIPs aren't sent: each site serves them itself.
  site_bgp = {
    as        = local.site_router_as
    router_id = "10.10.91.1" # the VLAN 910 SVI
    peers = {
      for zone, s in local.wsl_sites : zone => {
        remote_address = s.peer_address
        remote_as      = s.peer_as
        local_address  = split("/", s.router_address)[0]
        comment        = "${s.cluster} distro FRR over ${s.interface}"
        accept = concat([
          { prefix = s.node_subnet },
          { prefix = s.pod_cidr, max_len = 24 },
          ],
          [
            for half, primary in local.site_vip_halves :
            { prefix = half, min_len = 32, max_len = 32, local_pref = primary == zone ? 100 : 50 }
        ])
        export = flatten([
          for other_zone, o in local.wsl_sites : [
            { prefix = o.node_subnet },
            { prefix = o.pod_cidr, max_len = 24 },
          ] if other_zone != zone
        ])
      }
    }
  }
}
