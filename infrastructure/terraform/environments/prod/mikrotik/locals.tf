## ---------------------------------------------------------------------------------------------------------------------
## GENERATED FILE - DO NOT EDIT BY HAND
##
## Produced by infrastructure/mikrotik/tools/vyos-to-locals.py from the VyOS
## config at github.com/iT3E/vyos-config (config-parts/*.sh + containers/*).
##
## Regenerate with:
##   task mikrotik:generate
##
## Values live here rather than in a .tfvars file to match the rest of this repo
## (environments/prod/aws, environments/prod/cloudflare and bootstrap/aws-init
## all inline their values and pull secrets from SOPS). Secrets are NOT here:
## see secrets.sops.yaml.
##
## Translation summary:
##   VLANs                5 (5 enabled)
##   DHCP servers         4
##   DHCP static leases   12
##   Address lists        5 (11 members)
##   Port lists           1
##   Zone-pair chains     17
##   Firewall accepts     10 (expanded from VyOS tcp_udp / port-limit splits)
##   Input-chain accepts  23
##   dstnat rules         8
##   Static routes        1
##   WireGuard listeners  2
##   DNS static records   5
##   DNS adlists          45
## ---------------------------------------------------------------------------------------------------------------------

locals {
  identity = "sce-rtr01"
  domain   = "tnwks.local"
  timezone = "America/Los_Angeles"

  # ether1 is WAN, ether8 is out-of-band mgmt, the rest form the
  # VLAN trunk bridge.
  wan_interface       = "ether1"
  lan_trunk_interface = "bridge-lan"
  bridge_name         = "bridge-lan"
  bridge_ports        = ["ether2", "ether3", "ether4", "ether5", "ether6", "ether7", "sfp-sfpplus1"]
  # Never bridged: this is the address Terraform reaches the router on.
  mgmt = {
    interface = "ether8"
    address   = "192.168.88.1/24"
  }
  # Untagged on the uplink only; the Aruba's management lives here.
  native_vlan = {
    zone    = "switch-mgmt"
    address = "10.98.0.1/24"
    ports   = ["ether2"]
    comment = "Aruba switch management (untagged on the uplink)"
  }

  # VyOS's internet egress went via transit-10; the WAN port joins it.
  outside_zone = "transit-10"
  # EdgeRouter Lite, still upstream during the staged cutover. Set to
  # null once the modem is on ether1 and the ERL is retired.
  upstream_gateway = "172.16.1.1"

  vlans = {
    UISP-140 = {
      vlan_id = 140
      address = "10.10.140.1/24"
      comment = "UISP-140"
      dhcp = {
        pool_start    = "10.10.140.100"
        pool_end      = "10.10.140.200"
        lease_time    = "1d"
        authoritative = "yes"
        leases = {
          sce-ep01 = {
            comment     = "sce-ep01"
            address     = "10.10.140.140"
            mac_address = "74:AC:B9:A3:5B:0B"
          }
        }
      }
    }
    seccam-610 = {
      vlan_id = 610
      address = "10.10.60.1/24"
      comment = "seccam-610"
      dhcp = {
        pool_start    = "10.10.60.30"
        pool_end      = "10.10.60.100"
        lease_time    = "1d"
        authoritative = "yes"
        leases = {
          RLC-410 = {
            comment     = "RLC-410"
            address     = "10.10.60.33"
            mac_address = "EC:71:DB:EE:71:40"
          }
          amcrest1 = {
            comment     = "amcrest1"
            address     = "10.10.60.31"
            mac_address = "9C:8E:CD:0B:48:58"
          }
          amcrest2 = {
            comment     = "amcrest2"
            address     = "10.10.60.32"
            mac_address = "9C:8E:CD:0B:47:AD"
          }
          loryta01 = {
            comment     = "loryta01"
            address     = "10.10.60.100"
            mac_address = "08:ED:ED:03:B4:4E"
          }
          loryta02 = {
            comment     = "loryta02"
            address     = "10.10.60.30"
            mac_address = "08:ED:ED:5E:8D:8D"
          }
          sv3c01 = {
            comment     = "sv3c01"
            address     = "10.10.60.34"
            mac_address = "C0:99:60:D6:3E:BC"
          }
          sv3c02 = {
            comment     = "sv3c02"
            address     = "10.10.60.35"
            mac_address = "C0:99:2F:62:9A:AB"
          }
        }
      }
    }
    transit-10 = {
      vlan_id = 10
      address = "172.16.1.250/24"
      comment = "transit-10"
    }
    unifi-frontend-910 = {
      vlan_id = 910
      address = "10.10.91.1/24"
      comment = "unifi-frontend-910"
      dhcp = {
        pool_start    = "10.10.91.100"
        pool_end      = "10.10.91.200"
        lease_time    = "1d"
        authoritative = "yes"
        leases = {
          robovac1 = {
            comment     = "robovac1"
            address     = "10.10.91.110"
            mac_address = "50:EC:50:03:62:5A"
          }
          wyze_cam_1 = {
            comment     = "wyze_cam_1"
            address     = "10.10.91.105"
            mac_address = "D0:3F:27:5C:0C:3F"
          }
          wyze_cam_2 = {
            comment     = "wyze_cam_2"
            address     = "10.10.91.109"
            mac_address = "D0:3F:27:05:55:11"
          }
          wyze_cam_3 = {
            comment     = "wyze_cam_3"
            address     = "10.10.91.179"
            mac_address = "2C:AA:8E:1C:47:B9"
          }
        }
      }
    }
    unifi-mgmt-900 = {
      vlan_id = 900
      address = "10.10.90.1/24"
      comment = "unifi-mgmt-900"
      dhcp = {
        pool_start    = "10.10.90.100"
        pool_end      = "10.10.90.200"
        lease_time    = "1d"
        authoritative = "yes"
        leases        = {}
      }
    }
  }

  static_routes = {
    "10_60_10_0_24" = {
      dst_address = "10.60.10.0/24"
      gateway     = "10.10.140.140"
    }
  }

  address_lists = {
    admin_peers = {
      it-mobile_10_10_31_10 = "10.10.31.10"
    }
    frigate = {
      "10_10_91_142" = "10.10.91.142"
    }
    monitoring = {
      "10_10_91_142" = "10.10.91.142"
    }
    security_cameras = {
      "10_10_60_30"  = "10.10.60.30"
      "10_10_60_31"  = "10.10.60.31"
      "10_10_60_32"  = "10.10.60.32"
      "10_10_60_33"  = "10.10.60.33"
      "10_10_60_34"  = "10.10.60.34"
      "10_10_60_35"  = "10.10.60.35"
      "10_10_60_100" = "10.10.60.100"
    }
    unifi_controller = {
      "10_10_91_142" = "10.10.91.142"
    }
  }

  # RouterOS has no port-group object; these render into dst_port strings.
  port_lists = {
    unifi_controller_ports = ["8080", "3478", "6789"]
  }

  # Order is semantic. Keys are NNN-from-to; routeros_move_items
  # re-sequences the forward chain to sorted key order after apply.
  zone_policies = {
    "010-unifi-frontend-910-unifi-mgmt-900" = {
      from    = "unifi-frontend-910"
      to      = "unifi-mgmt-900"
      comment = "unifi-frontend-910 -> unifi-mgmt-900"
      rules = [
        {
          comment          = "Rule: accept_unifi_controller (was containers-unifi-mgmt-900)"
          src_address_list = "unifi_controller"
          protocol         = "tcp"
          dst_port         = "22,3478"
        },
        {
          comment          = "Rule: accept_unifi_controller (was containers-unifi-mgmt-900)"
          src_address_list = "unifi_controller"
          protocol         = "udp"
          dst_port         = "22,3478"
        },
      ]
    }
    "020-UISP-140-transit-10" = {
      from           = "UISP-140"
      to             = "transit-10"
      comment        = "From uisp-140 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "030-unifi-frontend-910-transit-10" = {
      from           = "unifi-frontend-910"
      to             = "transit-10"
      comment        = "From unifi-frontend-910 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "040-unifi-mgmt-900-unifi-frontend-910" = {
      from    = "unifi-mgmt-900"
      to      = "unifi-frontend-910"
      comment = "unifi-mgmt-900 -> unifi-frontend-910"
      rules = [
        {
          comment          = "Rule: accept_unifi_ap (was unifi-mgmt-900-containers)"
          dst_address_list = "unifi_controller"
          dst_port_list    = "unifi_controller_ports"
          protocol         = "tcp"
        },
        {
          comment          = "Rule: accept_unifi_ap (was unifi-mgmt-900-containers)"
          dst_address_list = "unifi_controller"
          dst_port_list    = "unifi_controller_ports"
          protocol         = "udp"
        },
      ]
    }
    "050-unifi-mgmt-900-transit-10" = {
      from           = "unifi-mgmt-900"
      to             = "transit-10"
      comment        = "From unifi-mgmt-900 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "060-vpn-UISP-140" = {
      from           = "vpn"
      to             = "UISP-140"
      comment        = "From vpn to UISP-140"
      rules          = []
      default_action = "accept"
    }
    "070-vpn-mobile-unifi-frontend-910" = {
      from    = "vpn-mobile"
      to      = "unifi-frontend-910"
      comment = "From vpn-mobile to unifi-frontend-910"
      rules = [
        {
          comment          = "Rule: accept_8443_unifi_controller (was vpn-mobile-containers)"
          dst_address_list = "unifi_controller"
          protocol         = "tcp"
          dst_port         = "8443"
        },
      ]
      default_action = "accept"
    }
    "080-vpn-mobile-transit-10" = {
      from           = "vpn-mobile"
      to             = "transit-10"
      comment        = "From vpn-mobile to transit-10"
      rules          = []
      default_action = "accept"
    }
    "090-vpn-seccam-610" = {
      from           = "vpn"
      to             = "seccam-610"
      comment        = "From vpn to seccam-610"
      rules          = []
      default_action = "accept"
    }
    "100-vpn-transit-10" = {
      from           = "vpn"
      to             = "transit-10"
      comment        = "From vpn to transit-10"
      rules          = []
      default_action = "accept"
    }
    "110-vpn-unifi-frontend-910" = {
      from           = "vpn"
      to             = "unifi-frontend-910"
      comment        = "From vpn to unifi-frontend-910"
      rules          = []
      default_action = "accept"
    }
    "120-vpn-unifi-mgmt-900" = {
      from           = "vpn"
      to             = "unifi-mgmt-900"
      comment        = "From vpn to unifi-mgmt-900"
      rules          = []
      default_action = "accept"
    }
    "130-vpn-switch-mgmt" = {
      from           = "vpn"
      to             = "switch-mgmt"
      comment        = "From vpn to switch-mgmt"
      rules          = []
      default_action = "accept"
    }
    "140-unifi-frontend-910-seccam-610" = {
      from    = "unifi-frontend-910"
      to      = "seccam-610"
      comment = "From unifi-frontend-910 to seccam-610"
      rules = [
        {
          comment          = "Frigate (WSL cluster) RTSP pulls from the cameras"
          src_address_list = "frigate"
          dst_address_list = "security_cameras"
          protocol         = "tcp"
          dst_port         = "554"
        },
        {
          comment          = "Frigate (WSL cluster) RTSP pulls from the cameras"
          src_address_list = "frigate"
          dst_address_list = "security_cameras"
          protocol         = "udp"
          dst_port         = "554"
        },
      ]
    }
    "150-vpn-mobile-switch-mgmt" = {
      from    = "vpn-mobile"
      to      = "switch-mgmt"
      comment = "From vpn-mobile to switch-mgmt"
      rules = [
        {
          comment          = "admin peers (it-mobile)"
          src_address_list = "admin_peers"
        },
      ]
    }
    "160-vpn-mobile-unifi-mgmt-900" = {
      from    = "vpn-mobile"
      to      = "unifi-mgmt-900"
      comment = "From vpn-mobile to unifi-mgmt-900"
      rules = [
        {
          comment          = "admin peers (it-mobile)"
          src_address_list = "admin_peers"
        },
      ]
    }
    "170-vpn-mobile-UISP-140" = {
      from    = "vpn-mobile"
      to      = "UISP-140"
      comment = "From vpn-mobile to UISP-140"
      rules = [
        {
          comment          = "admin peers (it-mobile)"
          src_address_list = "admin_peers"
        },
      ]
    }
  }

  # Input chain. No VyOS counterpart: VyOS had no local zone so traffic
  # TO the router was unfiltered. Intentional hardening delta, and where
  # the ported client-DNS rules land now the resolver is the router.
  input_rules = {
    "010-dns-UISP-140-tcp" = {
      comment           = "DNS from UISP-140 (was UISP-140-containers)"
      in_interface_list = "UISP-140"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "020-dns-UISP-140-udp" = {
      comment           = "DNS from UISP-140 (was UISP-140-containers)"
      in_interface_list = "UISP-140"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "030-dns-seccam-610-tcp" = {
      comment           = "DNS from seccam-610 (was seccam-610-containers)"
      in_interface_list = "seccam-610"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "040-dns-seccam-610-udp" = {
      comment           = "DNS from seccam-610 (was seccam-610-containers)"
      in_interface_list = "seccam-610"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "050-dns-transit-10-tcp" = {
      comment      = "DNS from transit-10 (was transit-10-containers)"
      in_interface = "bridge-lan-vlan10"
      protocol     = "tcp"
      dst_port     = "53,853"
    }
    "060-dns-transit-10-udp" = {
      comment      = "DNS from transit-10 (was transit-10-containers)"
      in_interface = "bridge-lan-vlan10"
      protocol     = "udp"
      dst_port     = "53,853"
    }
    "070-dns-unifi-frontend-910-tcp" = {
      comment           = "DNS from unifi-frontend-910 (was unifi-frontend-910-containers)"
      in_interface_list = "unifi-frontend-910"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "080-dns-unifi-frontend-910-udp" = {
      comment           = "DNS from unifi-frontend-910 (was unifi-frontend-910-containers)"
      in_interface_list = "unifi-frontend-910"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "090-dns-unifi-mgmt-900-tcp" = {
      comment           = "DNS from unifi-mgmt-900 (was unifi-mgmt-900-containers)"
      in_interface_list = "unifi-mgmt-900"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "100-dns-unifi-mgmt-900-udp" = {
      comment           = "DNS from unifi-mgmt-900 (was unifi-mgmt-900-containers)"
      in_interface_list = "unifi-mgmt-900"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "110-dns-vpn-tcp" = {
      comment           = "DNS from vpn (was vpn-containers)"
      in_interface_list = "vpn"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "120-dns-vpn-udp" = {
      comment           = "DNS from vpn (was vpn-containers)"
      in_interface_list = "vpn"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "130-dns-vpn-mobile-tcp" = {
      comment           = "DNS from vpn-mobile (was vpn-mobile-containers)"
      in_interface_list = "vpn-mobile"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "140-dns-vpn-mobile-udp" = {
      comment           = "DNS from vpn-mobile (was vpn-mobile-containers)"
      in_interface_list = "vpn-mobile"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "150-dns-switch-mgmt-tcp" = {
      comment           = "DNS from switch-mgmt"
      in_interface_list = "switch-mgmt"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "160-dns-switch-mgmt-udp" = {
      comment           = "DNS from switch-mgmt"
      in_interface_list = "switch-mgmt"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "510-router-service" = {
      comment  = "ICMP for path MTU discovery and diagnostics"
      protocol = "icmp"
    }
    "520-router-service" = {
      comment           = "DHCP requests from LAN clients"
      protocol          = "udp"
      dst_port          = "67,68"
      in_interface_list = "lan"
    }
    "530-router-service" = {
      comment           = "NTP server mode for LAN clients only (never WAN)"
      protocol          = "udp"
      dst_port          = "123"
      in_interface_list = "lan"
    }
    "540-router-service" = {
      comment           = "SSH/REST/Winbox from the admin VPN (wg01)"
      protocol          = "tcp"
      dst_port          = "22,443,8291"
      in_interface_list = "vpn"
    }
    "550-router-service" = {
      comment           = "SSH/REST/Winbox from admin peers on vpn-mobile"
      protocol          = "tcp"
      dst_port          = "22,443,8291"
      in_interface_list = "vpn-mobile"
      src_address_list  = "admin_peers"
    }
    "560-router-service" = {
      comment           = "WireGuard from the LAN, to join the admin VPN from inside"
      protocol          = "udp"
      dst_port          = "51820,51821"
      in_interface_list = "lan"
    }
    "570-router-service" = {
      comment           = "WireGuard listeners (ERL port-forward, then WAN)"
      protocol          = "udp"
      dst_port          = "51820,51821"
      in_interface_list = "transit-10"
    }
  }

  dstnat_rules = {
    "102-tcp" = {
      comment      = "Force DNS for unifi-frontend-910"
      in_interface = "bridge-lan-vlan910"
      protocol     = "tcp"
      dst_port     = "53"
      to_port      = "53"
      action       = "redirect"
    }
    "102-udp" = {
      comment      = "Force DNS for unifi-frontend-910"
      in_interface = "bridge-lan-vlan910"
      protocol     = "udp"
      dst_port     = "53"
      to_port      = "53"
      action       = "redirect"
    }
    "103-tcp" = {
      comment      = "Force DNS for seccam-610"
      in_interface = "bridge-lan-vlan610"
      protocol     = "tcp"
      dst_port     = "53"
      to_port      = "53"
      action       = "redirect"
    }
    "103-udp" = {
      comment      = "Force DNS for seccam-610"
      in_interface = "bridge-lan-vlan610"
      protocol     = "udp"
      dst_port     = "53"
      to_port      = "53"
      action       = "redirect"
    }
    "110-udp" = {
      comment      = "Force NTP for unifi-mgmt-900"
      in_interface = "bridge-lan-vlan900"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.90.1"
      to_port      = "123"
      dst_address  = "!10.10.90.1"
    }
    "111-udp" = {
      comment      = "Force NTP for unifi-frontend-910"
      in_interface = "bridge-lan-vlan910"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.91.1"
      to_port      = "123"
      dst_address  = "!10.10.91.1"
    }
    "115-udp" = {
      comment      = "Force NTP for seccam-610"
      in_interface = "bridge-lan-vlan610"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.60.1"
      to_port      = "123"
      dst_address  = "!10.10.60.1"
    }
    "116-udp" = {
      comment      = "Force NTP for UISP-140"
      in_interface = "bridge-lan-vlan140"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.140.1"
      to_port      = "123"
      dst_address  = "!10.10.140.1"
    }
  }

  # Source NAT moves here from the EdgeRouter Lite (its
  # `service nat rule 5000 type masquerade outbound-interface eth0`).
  # VyOS had its own masquerade commented out because the ERL did it.
  masquerade_out_interface = "ether1"

  # Replaces the blocky + dnsdist + bind container stack with native
  # RouterOS DNS. See docs/mikrotik-vyos-port.md for what did not survive.
  dns = {
    upstream_servers = ["1.1.1.1", "1.0.0.1"]
    allow_remote     = true
    cache_size       = 10240
    cache_max_ttl    = "1d"
    static_records = {
      drop_zip = {
        name            = "zip"
        type            = "NXDOMAIN"
        match_subdomain = true
        comment         = "dnsdist DropAction"
      }
      internal_tnwks_us = {
        name            = "internal.tnwks.us"
        address         = "10.10.91.142"
        type            = "A"
        ttl             = "1h"
        match_subdomain = true
        comment         = "blocky customDNS"
      }
      sce_ep01_tnwks_local = {
        name    = "sce-ep01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.140.140"
      }
      unifi = {
        name    = "unifi"
        type    = "A"
        comment = "bind db.unifi"
        address = "10.10.91.142"
      }
      sce_rtr01_tnwks_local = {
        name    = "sce-rtr01.tnwks.local"
        type    = "A"
        address = "10.10.91.1"
        comment = "generator"
      }
    }
    adlists = [
      "https://raw.githubusercontent.com/PolishFiltersTeam/KADhosts/master/KADhosts.txt",
      "https://raw.githubusercontent.com/FadeMind/hosts.extras/master/add.Spam/hosts",
      "https://v.firebog.net/hosts/static/w3kbl.txt",
      "https://raw.githubusercontent.com/matomo-org/referrer-spam-blacklist/master/spammers.txt",
      "https://someonewhocares.org/hosts/zero/hosts",
      "https://raw.githubusercontent.com/VeleSila/yhosts/master/hosts",
      "https://winhelp2002.mvps.org/hosts.txt",
      "https://v.firebog.net/hosts/neohostsbasic.txt",
      "https://raw.githubusercontent.com/RooneyMcNibNug/pihole-stuff/master/SNAFU.txt",
      "https://paulgb.github.io/BarbBlock/blacklists/hosts-file.txt",
      "https://adaway.org/hosts.txt",
      "https://v.firebog.net/hosts/AdguardDNS.txt",
      "https://v.firebog.net/hosts/Admiral.txt",
      "https://raw.githubusercontent.com/anudeepND/blacklist/master/adservers.txt",
      "https://s3.amazonaws.com/lists.disconnect.me/simple_ad.txt",
      "https://v.firebog.net/hosts/Easylist.txt",
      "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext",
      "https://raw.githubusercontent.com/FadeMind/hosts.extras/master/UncheckyAds/hosts",
      "https://raw.githubusercontent.com/bigdargon/hostsVN/master/hosts",
      "https://raw.githubusercontent.com/jdlingyu/ad-wars/master/hosts",
      "https://v.firebog.net/hosts/Easyprivacy.txt",
      "https://v.firebog.net/hosts/Prigent-Ads.txt",
      "https://raw.githubusercontent.com/FadeMind/hosts.extras/master/add.2o7Net/hosts",
      "https://raw.githubusercontent.com/crazy-max/WindowsSpyBlocker/master/data/hosts/spy.txt",
      "https://hostfiles.frogeye.fr/firstparty-trackers-hosts.txt",
      "https://www.github.developerdan.com/hosts/lists/ads-and-tracking-extended.txt",
      "https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/android-tracking.txt",
      "https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/SmartTV.txt",
      "https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/AmazonFireTV.txt",
      "https://gitlab.com/quidsup/notrack-blocklists/raw/master/notrack-blocklist.txt",
      "https://raw.githubusercontent.com/DandelionSprout/adfilt/master/Alternate%20versions%20Anti-Malware%20List/AntiMalwareHosts.txt",
      "https://osint.digitalside.it/Threat-Intel/lists/latestdomains.txt",
      "https://s3.amazonaws.com/lists.disconnect.me/simple_malvertising.txt",
      "https://v.firebog.net/hosts/Prigent-Crypto.txt",
      "https://raw.githubusercontent.com/FadeMind/hosts.extras/master/add.Risk/hosts",
      "https://bitbucket.org/ethanr/dns-blacklists/raw/8575c9f96e5b4a1308f2f12394abd86d0927a4a0/bad_lists/Mandiant_APT1_Report_Appendix_D.txt",
      "https://phishing.army/download/phishing_army_blocklist_extended.txt",
      "https://gitlab.com/quidsup/notrack-blocklists/raw/master/notrack-malware.txt",
      "https://v.firebog.net/hosts/RPiList-Malware.txt",
      "https://v.firebog.net/hosts/RPiList-Phishing.txt",
      "https://raw.githubusercontent.com/Spam404/lists/master/main-blacklist.txt",
      "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/hosts",
      "https://urlhaus.abuse.ch/downloads/hostfile/",
      "https://malware-filter.gitlab.io/malware-filter/phishing-filter-hosts.txt",
      "https://v.firebog.net/hosts/Prigent-Malware.txt",
    ]
    use_doh_server  = "https://cloudflare-dns.com/dns-query"
    verify_doh_cert = true
  }

  # VyOS both consumed upstream NTP and served the LAN (allow-client),
  # so server_mode stays on.
  ntp = {
    servers     = ["us.pool.ntp.org"]
    server_mode = true
    client_mode = "unicast"
  }

  # VyOS shipped octet-counted TCP syslog to the k8s Vector aggregator.
  # RouterOS emits plain syslog, so the Vector source must accept it.
  syslog = {
    remote          = "10.10.91.142"
    remote_protocol = "tcp"
    remote_port     = 6001
  }

  # Address list allowed to reach the router's read-only API (mktxp).
  monitoring_source_list = "monitoring"

  # Username is injected from SOPS in main.tf (VyOS used
  # ${SSH_VYOS_USERNAME}), so only key material lives here.
  admin_ssh_keys = [
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDQ3xsijUu7JGOG+GVc4FffLRqLF4gWDRT0EWofYinTFGkzxJFhGoS76bbjCU7nUGun5YQbRS7QcWkVKfKBne/ydtc9Mm1OZ/7N3a03TNPnXeFsiNgXfrC8LQN/OKrqreXqMhnj2Hc7o/KTHR/Ui8OE1uDI3mMcETWb1hQJRaLKKMjP5n/N9rEA8tvTK48NptXl0Jf3dJecbYu4/Az+llms9csBBRK2lqGFhYrwn+a7dLYkM80NGjQkk5tIG+0HLCNwYP1vJYLKgEc/XSEvCoyP2gdE3387mMLjBwBF55aDSf/vmnIawkL6hMYCz5CMuiFl7NbxLZk4RKJwdcJiEfX5xzXEu4f3AVIA/uxyM0pTTX1lJrPL+lzKH8VMfVOFxLTuyBU2VBgcbN6J/kHKanjpjcQ4INvmCKsO7YjsGWcE8MzhWfJrFUbX0ri4hhoESk+eqfP+AgSY0x9ibT/pGD0NKJbU6M6QpV3KfbV/JZ1xgakiIJZbleRonFSL1sSX6x0=",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF6lX74KwH2/4FQHwIwKUHhsP9CxnvliHLKxcVPTLD2B",
  ]


  # Members of the aggregate zone-lan interface list. Input-chain
  # rules for DNS/NTP/DHCP match this so they cannot be reached from
  # the WAN, closing the EdgeRouter's open-resolver/open-NTP exposure.
  lan_interface_lists = ["UISP-140", "seccam-610", "transit-10", "unifi-frontend-910", "unifi-mgmt-900"]

  # Carried over from the EdgeRouter Lite, which sized conntrack for
  # the full internet-edge load (table-size 32768, tcp loose enable).
  connection_tracking = {
    enabled            = "yes"
    loose_tcp_tracking = "true"
  }

  # Replaces VyOS's cloudflare-ddns container. RouterOS has no Cloudflare
  # client, so the module renders a script + scheduler onto the router;
  # see modules/mikrotik/ddns.tf.
  # LOAD-BEARING: the WireGuard client configs use this hostname as
  # their endpoint, so a stale record breaks remote VPN access at the
  # next WAN address change. Host, domain and API token come from SOPS.
  ddns = {
    interval  = "5m"
    zone_id   = "42d48ba8c41db9079e8eed11ef0250cb"
    record_id = "b0b3f0ae9b559627df2814e83cd06f39"
  }

  # Private keys are injected from SOPS in main.tf, never stored here.
  wireguard_interfaces = {
    wg01 = {
      zone        = "vpn"
      address     = "10.10.30.1/24"
      listen_port = 51820
      comment     = "WIREGUARD"
      peers = {
        it-pc01 = {
          allowed_address      = ["10.10.30.10/32"]
          persistent_keepalive = "15s"
          public_key           = "G50MJOM94BeGaX9wVFvChFDOy8YAbu32MpTIVSctsSE="
        }
        macbook-it = {
          allowed_address      = ["10.10.30.11/32"]
          persistent_keepalive = "15s"
          public_key           = "cT+qa6B3XMAVENQ2EOK2nfpnXZUGp5RkUwkUSEQZWVc="
        }
        xps-it = {
          allowed_address      = ["10.10.30.12/32"]
          persistent_keepalive = "15s"
          public_key           = "ftYscBzFt6+7zBotvBR8MO9M1PhziWtg+s9FKJmZskc="
        }
      }
    }
    wg02 = {
      zone        = "vpn-mobile"
      address     = "10.10.31.1/24"
      listen_port = 51821
      comment     = "WIREGUARD-AO"
      peers = {
        it-mobile = {
          allowed_address      = ["10.10.31.10/32"]
          persistent_keepalive = "15s"
          public_key           = "K910+sQUVYHfbUfoN0HKlIObkqBY0P4Efu+kYw97Ylc="
        }
        mh-mobile = {
          allowed_address      = ["10.10.31.11/32"]
          persistent_keepalive = "15s"
          public_key           = "E8FPyFCG/gAr8XFSlQ8nhlJdikGX53yjQERiUnLifTY="
        }
      }
    }
  }

  # Translation notes (see docs/mikrotik-vyos-port.md):
  #   - UISP-140-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - ad-110-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - address list 3d_printer_controllers: not emitted, no remaining rule references it
  #   - address list domain_controllers: not emitted, no remaining rule references it
  #   - address list freenas: not emitted, no remaining rule references it
  #   - address list haproxy_all: not emitted, no remaining rule references it
  #   - address list haproxy_authenticated: not emitted, no remaining rule references it
  #   - address list haproxy_frontend: not emitted, no remaining rule references it
  #   - address list hass: not emitted, no remaining rule references it
  #   - address list ilo: not emitted, no remaining rule references it
  #   - address list it_pc: not emitted, no remaining rule references it
  #   - address list k8s_ingress: not emitted, no remaining rule references it
  #   - address list k8s_ingress_internal: not emitted, no remaining rule references it
  #   - address list k8s_vector_aggregator: not emitted, no remaining rule references it
  #   - address list pve_hosts: not emitted, no remaining rule references it
  #   - address list robovac1: not emitted, no remaining rule references it
  #   - address list seccam_nas: not emitted, no remaining rule references it
  #   - address list unifi_controller: VyOS members ['10.10.53.10'] replaced with ['10.10.91.142'] (workload moved)
  #   - address list windows_bastion: not emitted, no remaining rule references it
  #   - address list wyze_cameras: not emitted, no remaining rule references it
  #   - app-720-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - app-720-unifi-frontend-910: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - app-720-unifi-mgmt-900 rule 1: DROPPED; it names udmpro (stale UDM Pro entry pointing at the EdgeRouter)
  #   - bastion-410-ad-110: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - bastion-410-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - bastion-410-seccam-610: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - bastion-410-unifi-frontend-910: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - containers-k8s-120: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - containers-seccam-610 rule 1: DROPPED; haproxy -> backend traffic, and haproxy is not ported (its backends are gone)
  #   - containers-unifi-mgmt-900 rule 1: source retargeted to unifi_controller in unifi-frontend-910
  #   - dns: 36 bind A/CNAME records ported as RouterOS static entries; bind SOA/NS have no RouterOS equivalent and are dropped (RouterOS forwards and overrides, it is not authoritative)
  #   - dns: 45 blocky blackList URLs become ip_dns_adlist entries -- see the RAM warning in docs/mikrotik-vyos-port.md
  #   - dns: alertmanager.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: backbox-exporter.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: blocking and split-horizon now apply to EVERY VLAN; dnsdist only sent 10.10.91.0/24 and 10.10.31.0/24 through blocky. RouterOS adlists are global
  #   - dns: blocky customDNS.rewrite entries ['tnwks.us'] are no-ops (key == value) and are not ported
  #   - dns: blocky had 2 whiteList URL(s); RouterOS ip_dns_adlist has no allow-list concept, so these are NOT ported (false positives must be handled with static_records overrides)
  #   - dns: blocky used DoT (tcp-tls) upstreams; RouterOS has no DoT, so the same Cloudflare resolvers are configured with DoH (use_doh_server) plus plain-IP fallback
  #   - dns: blocky wildcard mappings (*.host) collapsed into single RouterOS records with match_subdomain=true: ['internal.tnwks.us']
  #   - dns: blueiris.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: changedetection.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: dnsdist per-subnet ControlD DoH pools ['controld_iot', 'controld_servers', 'controld_trusted'] are NOT ported; RouterOS resolves uniformly. Per-client policy needs ip_dns_forwarders or an off-router resolver
  #   - dns: frigate.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: hajimari.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: hass-code.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: hass.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: hubble.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: jellyfin.tnwks.local -> 10.10.53.8 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: loki.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: obsidian-couchdb.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: prometheus.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: prowlarr.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: qbittorrent.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: radarr.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: s3.tnwks.local DROPPED (Proxmox Ceph network, retired)
  #   - dns: sce-er01.tnwks.local DROPPED (EdgeRouter, retired)
  #   - dns: sce-hass01.tnwks.local -> 10.10.72.100 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve01-ceph.tnwks.local DROPPED (Proxmox Ceph network, retired)
  #   - dns: sce-pve01-ilo.tnwks.local -> 10.10.55.100 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve01.tnwks.local -> 10.10.11.10 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve02-ceph.tnwks.local DROPPED (Proxmox Ceph network, retired)
  #   - dns: sce-pve02-ilo.tnwks.local -> 10.10.55.101 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve02.tnwks.local -> 10.10.11.11 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve03-ceph.tnwks.local DROPPED (Proxmox Ceph network, retired)
  #   - dns: sce-pve03-ilo.tnwks.local -> 10.10.55.102 DROPPED; address is in a retired VLAN
  #   - dns: sce-pve03.tnwks.local -> 10.10.11.12 DROPPED; address is in a retired VLAN
  #   - dns: sce-uisp01.tnwks.local -> 10.10.72.101 DROPPED; address is in a retired VLAN
  #   - dns: sce-vyos01.tnwks.local DROPPED (VyOS router, retired)
  #   - dns: sonarr.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: synapse-admin.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: thanos.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - dns: uisp.tnwks.us DROPPED (UISP ran as a VyOS container; no UISP server runs now)
  #   - dns: unifi retargeted 10.10.53.10 -> 10.10.91.142 (container moved)
  #   - dns: unms.tnwks.us DROPPED (UISP ran as a VyOS container; no UISP server runs now)
  #   - dns: zwave.tnwks.local -> 10.10.120.51 DROPPED; address is in a retired VLAN
  #   - iLO-550-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - k8s-120-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - k8s-120-pve-11: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - k8s-120-seccam-610: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - k8s-120-unifi-frontend-910: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - nat rule 102 (Force DNS for unifi-frontend-910): dst-nat to 10.10.53.4 (DNS container) became a redirect to the router
  #   - nat rule 103 (Force DNS for seccam-610): dst-nat to 10.10.53.4 (DNS container) became a redirect to the router
  #   - nat rule 103 (Force DNS for seccam-610): exemption for retired host 10.10.60.210 removed
  #   - nat rule 112 (Force NTP for k8s-120): DROPPED; zone is retired
  #   - nat rule 113 (Force NTP for iLO-550): DROPPED; zone is retired
  #   - nat rule 117 (Force NTP for transit-10) DROPPED: redirect target 172.16.1.254 is dead, so this rule black-holes traffic today
  #   - nat rule 120 (Force NTP for app-720): DROPPED; zone is retired
  #   - port list ad_auth_ports: not emitted, no remaining rule references it
  #   - port list powershell_remoting: not emitted, no remaining rule references it
  #   - pve-11-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - pve-11-k8s-120: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - route 0.0.0.0/0 via 172.16.1.1: not a static route; becomes upstream_gateway (distance 10) for the staged cutover, and the WAN DHCP lease (distance 1) takes over when the modem moves
  #   - route 10.10.93.0/24 via 172.16.1.254 DROPPED: next-hop is dead (ip neigh FAILED, 100% ping loss). Both VyOS and the EdgeRouter still pointed production subnets at it
  #   - route 10.98.0.0/24 via 172.16.1.254 DROPPED: next-hop is dead (ip neigh FAILED, 100% ping loss). Both VyOS and the EdgeRouter still pointed production subnets at it
  #   - seccam-610-ad-110 rule 1: DROPPED; it names blue_iris (Blue Iris NVR, replaced by Frigate)
  #   - seccam-610-app-720 rule 1: DROPPED; it names blue_iris (Blue Iris NVR, replaced by Frigate)
  #   - seccam-610-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - seccam-610-transit-10 rule 1: DROPPED; it names blue_iris (Blue Iris NVR, replaced by Frigate)
  #   - seccam-610-unifi-frontend-910 rule 1: DROPPED; it names blue_iris (Blue Iris NVR, replaced by Frigate)
  #   - syslog: target 10.10.120.56 (Vector on the old cluster, gone) -> 10.10.91.142:6001, the WSL host's LAN bridge. Needs vector-aggregator running on WSL and a tcp/6001 lan-bridge relay
  #   - system: 1 SSH public key(s) ported from VyOS `system login user`; the username came from ${SSH_VYOS_USERNAME} and is injected from SOPS in main.tf
  #   - system: VyOS flow export (sFlow, for monitoring/flow-collector) is not generated; the root module's flow_export (IPFIX) replaces it
  #   - system: VyOS task-scheduler (dhcp-leases-textfile.py -> node-exporter dhcp_lease_info, used for flow-collector client names) has no RouterOS port; client names need a RouterOS lease exporter (e.g. mktxp)
  #   - system: identity is sce-rtr01, not the VyOS name sce-vyos01
  #   - transit-10-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - transit-10-k8s-120: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - transit-10-unifi-mgmt-900 rule 1: DROPPED; it names udmpro (stale UDM Pro entry pointing at the EdgeRouter)
  #   - unifi-frontend-910-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - unifi-frontend-910-containers rule 2: DROPPED; it targeted haproxy_frontend, which only existed for haproxy (not ported, its backends are gone)
  #   - unifi-mgmt-900-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - unifi-mgmt-900-containers rule 2: retargeted to unifi-mgmt-900 -> unifi-frontend-910 dst unifi_controller
  #   - vpn-containers (default-action accept): DNS moved to the input chain; other container access follows vpn -> unifi-frontend-910
  #   - vpn-mobile-containers rule 1: DROPPED; it targeted haproxy_authenticated, which only existed for haproxy (not ported, its backends are gone)
  #   - vpn-mobile-containers rule 2 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - vpn-mobile-containers rule 3: retargeted to vpn-mobile -> unifi-frontend-910 dst unifi_controller
  #   - vpn-mobile-containers rule 4: DROPPED; it targeted haproxy_frontend, which only existed for haproxy (not ported, its backends are gone)
  #   - wireguard: wg01/it-pc01 re-keyed for the RB5009 (WG_PEER_PUBLIC_KEYS)
  #   - zone ad-110: retired; VLAN, DHCP and every pair to or from it are not emitted
  #   - zone app-720: retired; VLAN, DHCP and every pair to or from it are not emitted
  #   - zone bastion-410: retired; VLAN, DHCP and every pair to or from it are not emitted
  #   - zone iLO-550: retired; VLAN, DHCP and every pair to or from it are not emitted
  #   - zone k8s-120: retired; VLAN, DHCP and every pair to or from it are not emitted
  #   - zone pve-11: retired; VLAN, DHCP and every pair to or from it are not emitted
}
