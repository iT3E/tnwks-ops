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
##   VLANs                11 (11 enabled)
##   DHCP servers         9
##   DHCP static leases   21
##   Address lists        22 (38 members)
##   Port lists           3
##   Zone-pair chains     39
##   Firewall accepts     49 (expanded from VyOS tcp_udp / port-limit splits)
##   Input-chain accepts  31
##   dstnat rules         11
##   Static routes        1
##   WireGuard listeners  2
##   DNS static records   35
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
    address   = "10.98.0.1/24"
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
        lease_time    = "24h"
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
    ad-110 = {
      vlan_id = 110
      address = "10.10.10.1/24"
      comment = "ad-110"
      dhcp = {
        pool_start    = "10.10.10.100"
        pool_end      = "10.10.10.200"
        lease_time    = "24h"
        authoritative = "yes"
        leases        = {}
      }
    }
    app-720 = {
      vlan_id = 720
      address = "10.10.72.1/24"
      comment = "app-720"
      dhcp = {
        pool_start    = "10.10.72.100"
        pool_end      = "10.10.72.200"
        lease_time    = "24h"
        authoritative = "yes"
        leases        = {}
      }
    }
    bastion-410 = {
      vlan_id = 410
      address = "10.10.40.1/24"
      comment = "bastion-410"
      dhcp = {
        pool_start    = "10.10.40.100"
        pool_end      = "10.10.40.200"
        lease_time    = "24h"
        authoritative = "yes"
        leases        = {}
      }
    }
    iLO-550 = {
      vlan_id = 550
      address = "10.10.55.1/24"
      comment = "iLO-550"
      dhcp = {
        pool_start    = "10.10.55.100"
        pool_end      = "10.10.55.200"
        lease_time    = "24h"
        authoritative = "yes"
        leases = {
          sce-pve01-ilo = {
            comment     = "sce-pve01-ilo"
            address     = "10.10.55.102"
            mac_address = "A4:5D:36:FB:15:56"
          }
          sce-pve02-ilo = {
            comment     = "sce-pve02-ilo"
            address     = "10.10.55.100"
            mac_address = "2C:59:E5:3B:84:9C"
          }
          sce-pve03-ilo = {
            comment     = "sce-pve03-ilo"
            address     = "10.10.55.101"
            mac_address = "AC:16:2D:BE:93:22"
          }
        }
      }
    }
    k8s-120 = {
      vlan_id = 120
      address = "10.10.120.1/24"
      comment = "k8s-120"
      dhcp = {
        pool_start    = "10.10.120.100"
        pool_end      = "10.10.120.200"
        lease_time    = "24h"
        authoritative = "yes"
        leases = {
          sce-uk8sm01 = {
            comment     = "sce-uk8sm01"
            address     = "10.10.120.107"
            mac_address = "0E:DD:A7:62:16:91"
          }
          sce-uk8sm02 = {
            comment     = "sce-uk8sm02"
            address     = "10.10.120.112"
            mac_address = "B6:92:1A:44:B3:2C"
          }
          sce-uk8sm03 = {
            comment     = "sce-uk8sm03"
            address     = "10.10.120.110"
            mac_address = "D6:E4:BA:D5:5E:E8"
          }
          sce-uk8sw01 = {
            comment     = "sce-uk8sw01"
            address     = "10.10.120.108"
            mac_address = "56:55:48:8C:4A:96"
          }
          sce-uk8sw02 = {
            comment     = "sce-uk8sw02"
            address     = "10.10.120.109"
            mac_address = "C2:8E:CC:1F:89:3D"
          }
          sce-uk8sw03 = {
            comment     = "sce-uk8sw03"
            address     = "10.10.120.111"
            mac_address = "7E:A6:19:07:2E:5D"
          }
        }
      }
    }
    pve-11 = {
      vlan_id = 11
      address = "10.10.11.1/24"
      comment = "pve-11"
    }
    seccam-610 = {
      vlan_id = 610
      address = "10.10.60.1/24"
      comment = "seccam-610"
      dhcp = {
        pool_start    = "10.10.60.30"
        pool_end      = "10.10.60.100"
        lease_time    = "24h"
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
        lease_time    = "24h"
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
        lease_time    = "24h"
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
    "3d_printer_controllers" = {
      "10_1_3_56" = "10.1.3.56"
    }
    blue_iris = {
      "10_10_60_210" = "10.10.60.210"
    }
    domain_controllers = {
      "10_10_10_10" = "10.10.10.10"
      "10_10_10_11" = "10.10.10.11"
      "10_10_10_12" = "10.10.10.12"
    }
    freenas = {
      "10_10_40_10" = "10.10.40.10"
    }
    frigate = {
      "10_10_91_142" = "10.10.91.142"
    }
    haproxy_all = {
      "10_10_53_2" = "10.10.53.2"
      "10_10_53_8" = "10.10.53.8"
      "10_10_53_9" = "10.10.53.9"
    }
    haproxy_authenticated = {
      "10_10_53_9" = "10.10.53.9"
    }
    haproxy_frontend = {
      "10_10_53_8" = "10.10.53.8"
    }
    hass = {
      "10_10_72_201" = "10.10.72.201"
    }
    ilo = {
      "10_10_55_101" = "10.10.55.101"
      "10_10_55_102" = "10.10.55.102"
      "10_10_55_103" = "10.10.55.103"
    }
    it_pc = {
      "10_10_93_232" = "10.10.93.232"
    }
    k8s_ingress = {
      "10_10_120_51" = "10.10.120.51"
    }
    k8s_ingress_internal = {
      "10_10_120_52" = "10.10.120.52"
    }
    k8s_vector_aggregator = {
      "10_10_120_56" = "10.10.120.56"
    }
    pve_hosts = {
      "10_10_11_10" = "10.10.11.10"
      "10_10_11_11" = "10.10.11.11"
      "10_10_11_12" = "10.10.11.12"
    }
    robovac1 = {
      "10_10_91_110" = "10.10.91.110"
    }
    seccam_nas = {
      "10_10_60_10" = "10.10.60.10"
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
    udmpro = {
      "172_16_1_1" = "172.16.1.1"
    }
    unifi_controller = {
      "10_10_91_142" = "10.10.91.142"
    }
    windows_bastion = {
      "10_10_40_240" = "10.10.40.240"
    }
    wyze_cameras = {
      "10_10_91_105" = "10.10.91.105"
      "10_10_91_179" = "10.10.91.179"
      "10_10_91_109" = "10.10.91.109"
    }
  }

  # RouterOS has no port-group object; these render into dst_port strings.
  port_lists = {
    ad_auth_ports = [
      "389",
      "53",
      "3268",
      "3269",
      "88",
      "464",
      "636",
      "123",
      "135",
      "137",
      "138",
      "139",
      "445",
      "9389",
      "5985",
      "5986",
      "6000-6199",
      "49152-65535",
    ]
    powershell_remoting    = ["5985", "5986"]
    unifi_controller_ports = ["8080", "3478", "6789"]
  }

  # Order is semantic. Keys are NNN-from-to; routeros_move_items
  # re-sequences the forward chain to sorted key order after apply.
  zone_policies = {
    "010-ad-110-transit-10" = {
      from           = "ad-110"
      to             = "transit-10"
      comment        = "From ad-110 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "020-app-720-transit-10" = {
      from           = "app-720"
      to             = "transit-10"
      comment        = "From app-720 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "030-app-720-unifi-frontend-910" = {
      from    = "app-720"
      to      = "unifi-frontend-910"
      comment = "From app-720 to unifi-frontend-910"
      rules = [
        {
          comment          = "Rule: hass_to_all_frontend"
          src_address_list = "hass"
        },
      ]
    }
    "040-app-720-unifi-mgmt-900" = {
      from    = "app-720"
      to      = "unifi-mgmt-900"
      comment = "From app-720 to unifi-mgmt-900"
      rules = [
        {
          comment          = "Rule: hass_to_udmpro"
          src_address_list = "hass"
          dst_address_list = "udmpro"
          protocol         = "tcp"
          dst_port         = "80,443"
        },
        {
          comment          = "Rule: hass_to_udmpro"
          src_address_list = "hass"
          dst_address_list = "udmpro"
          protocol         = "udp"
          dst_port         = "80,443"
        },
      ]
    }
    "050-bastion-410-ad-110" = {
      from    = "bastion-410"
      to      = "ad-110"
      comment = "From bastion-410 to ad-110"
      rules = [
        {
          comment          = "rule 1 [ports 1/2]"
          src_address_list = "windows_bastion"
          protocol         = "tcp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 1 [ports 2/2]"
          src_address_list = "windows_bastion"
          protocol         = "tcp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
        {
          comment          = "rule 1 [ports 1/2]"
          src_address_list = "windows_bastion"
          protocol         = "udp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 1 [ports 2/2]"
          src_address_list = "windows_bastion"
          protocol         = "udp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
        {
          comment          = "rule 2"
          src_address_list = "windows_bastion"
          protocol         = "tcp"
          dst_port         = "3389"
        },
        {
          comment          = "rule 3"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "tcp"
        },
        {
          comment          = "rule 3"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "udp"
        },
        {
          comment          = "rule 4 [ports 1/2]"
          src_address_list = "freenas"
          protocol         = "tcp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 4 [ports 2/2]"
          src_address_list = "freenas"
          protocol         = "tcp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
        {
          comment          = "rule 4 [ports 1/2]"
          src_address_list = "freenas"
          protocol         = "udp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 4 [ports 2/2]"
          src_address_list = "freenas"
          protocol         = "udp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
      ]
    }
    "060-bastion-410-seccam-610" = {
      from    = "bastion-410"
      to      = "seccam-610"
      comment = "From bastion-410 to seccam-610"
      rules = [
        {
          comment          = "rule 1"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "tcp"
        },
        {
          comment          = "rule 1"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "udp"
        },
        {
          comment          = "rule 2"
          src_address_list = "windows_bastion"
          protocol         = "tcp"
          dst_port         = "3389"
        },
      ]
    }
    "070-bastion-410-transit-10" = {
      from           = "bastion-410"
      to             = "transit-10"
      comment        = "From bastion-410 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "080-bastion-410-unifi-frontend-910" = {
      from    = "bastion-410"
      to      = "unifi-frontend-910"
      comment = "From bastion-410 to unifi-frontend-910"
      rules = [
        {
          comment          = "rule 1"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "tcp"
        },
        {
          comment          = "rule 1"
          src_address_list = "windows_bastion"
          dst_port_list    = "powershell_remoting"
          protocol         = "udp"
        },
        {
          comment          = "rule 2"
          src_address_list = "windows_bastion"
          protocol         = "tcp"
          dst_port         = "3389"
        },
      ]
    }
    "090-unifi-frontend-910-unifi-mgmt-900" = {
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
    "100-k8s-120-unifi-frontend-910" = {
      from    = "k8s-120"
      to      = "unifi-frontend-910"
      comment = "From k8s-120 to unifi-frontend-910"
      rules = [
        {
          comment          = "Rule: accept_8443_unifi_controller (was k8s-120-containers)"
          dst_address_list = "unifi_controller"
          protocol         = "tcp"
          dst_port         = "8443"
        },
        {
          comment          = "Rule: allow_tcp_udp_robovac2"
          dst_address_list = "robovac1"
          protocol         = "tcp"
        },
        {
          comment          = "Rule: allow_tcp_udp_robovac2"
          dst_address_list = "robovac1"
          protocol         = "udp"
        },
        {
          comment          = "Rule: allow_tcp_udp_wyze"
          dst_address_list = "wyze_cameras"
          protocol         = "tcp"
          dst_port         = "554"
        },
        {
          comment          = "Rule: allow_tcp_udp_wyze"
          dst_address_list = "wyze_cameras"
          protocol         = "udp"
          dst_port         = "554"
        },
      ]
    }
    "110-k8s-120-pve-11" = {
      from    = "k8s-120"
      to      = "pve-11"
      comment = "From k8s-120 to pve-11"
      rules = [
        {
          comment          = "Rule: allow_9221_to_pve"
          dst_address_list = "pve_hosts"
          protocol         = "tcp"
          dst_port         = "9221"
        },
        {
          comment          = "Rule: allow_9100_to_pve"
          dst_address_list = "pve_hosts"
          protocol         = "tcp"
          dst_port         = "9100"
        },
        {
          comment          = "Rule: allow_9089_to_pve"
          dst_address_list = "pve_hosts"
          protocol         = "tcp"
          dst_port         = "9089"
        },
      ]
    }
    "120-k8s-120-seccam-610" = {
      from    = "k8s-120"
      to      = "seccam-610"
      comment = "From k8s-120 to seccam-610"
      rules = [
        {
          comment          = "Rule: allow_rtsp_to_seccam"
          dst_address_list = "security_cameras"
          protocol         = "tcp"
          dst_port         = "554"
        },
        {
          comment          = "Rule: allow_rtsp_to_seccam"
          dst_address_list = "security_cameras"
          protocol         = "udp"
          dst_port         = "554"
        },
      ]
    }
    "130-k8s-120-transit-10" = {
      from           = "k8s-120"
      to             = "transit-10"
      comment        = "From k8s-120 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "140-pve-11-k8s-120" = {
      from    = "pve-11"
      to      = "k8s-120"
      comment = "From pve-11 to k8s-120"
      rules = [
        {
          comment          = "Rule: accept_syslog_6003"
          dst_address_list = "k8s_vector_aggregator"
          protocol         = "tcp"
          dst_port         = "6003"
        },
      ]
    }
    "150-pve-11-transit-10" = {
      from           = "pve-11"
      to             = "transit-10"
      comment        = "From pve-11 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "160-seccam-610-ad-110" = {
      from    = "seccam-610"
      to      = "ad-110"
      comment = "From seccam-610 to ad-110"
      rules = [
        {
          comment          = "rule 1 [ports 1/2]"
          src_address_list = "blue_iris"
          dst_address_list = "domain_controllers"
          protocol         = "tcp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 1 [ports 2/2]"
          src_address_list = "blue_iris"
          dst_address_list = "domain_controllers"
          protocol         = "tcp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
        {
          comment          = "rule 1 [ports 1/2]"
          src_address_list = "blue_iris"
          dst_address_list = "domain_controllers"
          protocol         = "udp"
          dst_port         = "389,53,3268,3269,88,464,636,123,135,137,138,139,445,9389,5985"
        },
        {
          comment          = "rule 1 [ports 2/2]"
          src_address_list = "blue_iris"
          dst_address_list = "domain_controllers"
          protocol         = "udp"
          dst_port         = "5986,6000-6199,49152-65535"
        },
      ]
    }
    "170-seccam-610-app-720" = {
      from    = "seccam-610"
      to      = "app-720"
      comment = "From seccam-610 to app-720"
      rules = [
        {
          comment          = "Rule: mosquitto_mqtt"
          src_address_list = "blue_iris"
          dst_address_list = "hass"
          protocol         = "tcp"
          dst_port         = "1883"
        },
        {
          comment          = "Rule: mosquitto_mqtt"
          src_address_list = "blue_iris"
          dst_address_list = "hass"
          protocol         = "udp"
          dst_port         = "1883"
        },
      ]
    }
    "180-seccam-610-transit-10" = {
      from    = "seccam-610"
      to      = "transit-10"
      comment = "From seccam-610 to transit-10"
      rules = [{
        comment          = "rule 1"
        src_address_list = "blue_iris"
      }]
    }
    "190-seccam-610-unifi-frontend-910" = {
      from    = "seccam-610"
      to      = "unifi-frontend-910"
      comment = "From seccam-610 to unifi-frontend-910"
      rules = [
        {
          comment          = "rule 1"
          src_address_list = "blue_iris"
          dst_address_list = "wyze_cameras"
        },
      ]
    }
    "200-transit-10-k8s-120" = {
      from    = "transit-10"
      to      = "k8s-120"
      comment = "From transit-10 to k8s-120"
      rules = [
        {
          comment          = "Rule: allow_80_443"
          dst_address_list = "k8s_ingress"
          protocol         = "tcp"
          dst_port         = "80,443"
        },
      ]
    }
    "210-transit-10-unifi-mgmt-900" = {
      from    = "transit-10"
      to      = "unifi-mgmt-900"
      comment = "From transit-10 to unifi-mgmt-900"
      rules = [
        {
          comment          = "Rule: allow_udmpro"
          src_address_list = "udmpro"
          protocol         = "tcp"
        },
        {
          comment          = "Rule: allow_udmpro"
          src_address_list = "udmpro"
          protocol         = "udp"
        },
      ]
    }
    "220-UISP-140-transit-10" = {
      from           = "UISP-140"
      to             = "transit-10"
      comment        = "From uisp-140 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "230-unifi-frontend-910-transit-10" = {
      from           = "unifi-frontend-910"
      to             = "transit-10"
      comment        = "From unifi-frontend-910 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "240-unifi-mgmt-900-unifi-frontend-910" = {
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
    "250-unifi-mgmt-900-transit-10" = {
      from           = "unifi-mgmt-900"
      to             = "transit-10"
      comment        = "From unifi-mgmt-900 to transit-10"
      rules          = []
      default_action = "accept"
    }
    "260-vpn-UISP-140" = {
      from           = "vpn"
      to             = "UISP-140"
      comment        = "From vpn to UISP-140"
      rules          = []
      default_action = "accept"
    }
    "270-vpn-ad-110" = {
      from           = "vpn"
      to             = "ad-110"
      comment        = "From vpn to ad-110"
      rules          = []
      default_action = "accept"
    }
    "280-vpn-app-720" = {
      from           = "vpn"
      to             = "app-720"
      comment        = "From vpn to app-720"
      rules          = []
      default_action = "accept"
    }
    "290-vpn-bastion-410" = {
      from           = "vpn"
      to             = "bastion-410"
      comment        = "From vpn to bastion-410"
      rules          = []
      default_action = "accept"
    }
    "300-vpn-iLO-550" = {
      from           = "vpn"
      to             = "iLO-550"
      comment        = "From vpn to iLO-550"
      rules          = []
      default_action = "accept"
    }
    "310-vpn-k8s-120" = {
      from           = "vpn"
      to             = "k8s-120"
      comment        = "From vpn to k8s-120"
      rules          = []
      default_action = "accept"
    }
    "320-vpn-mobile-unifi-frontend-910" = {
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
    "330-vpn-mobile-transit-10" = {
      from           = "vpn-mobile"
      to             = "transit-10"
      comment        = "From vpn-mobile to transit-10"
      rules          = []
      default_action = "accept"
    }
    "340-vpn-pve-11" = {
      from           = "vpn"
      to             = "pve-11"
      comment        = "From vpn to pve-11"
      rules          = []
      default_action = "accept"
    }
    "350-vpn-seccam-610" = {
      from           = "vpn"
      to             = "seccam-610"
      comment        = "From vpn to seccam-610"
      rules          = []
      default_action = "accept"
    }
    "360-vpn-transit-10" = {
      from           = "vpn"
      to             = "transit-10"
      comment        = "From vpn to transit-10"
      rules          = []
      default_action = "accept"
    }
    "370-vpn-unifi-frontend-910" = {
      from           = "vpn"
      to             = "unifi-frontend-910"
      comment        = "From vpn to unifi-frontend-910"
      rules          = []
      default_action = "accept"
    }
    "380-vpn-unifi-mgmt-900" = {
      from           = "vpn"
      to             = "unifi-mgmt-900"
      comment        = "From vpn to unifi-mgmt-900"
      rules          = []
      default_action = "accept"
    }
    "390-unifi-frontend-910-seccam-610" = {
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
    "030-dns-ad-110-tcp" = {
      comment           = "DNS from ad-110 (was ad-110-containers)"
      in_interface_list = "ad-110"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "040-dns-ad-110-udp" = {
      comment           = "DNS from ad-110 (was ad-110-containers)"
      in_interface_list = "ad-110"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "050-dns-bastion-410-tcp" = {
      comment           = "DNS from bastion-410 (was bastion-410-containers)"
      in_interface_list = "bastion-410"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "060-dns-bastion-410-udp" = {
      comment           = "DNS from bastion-410 (was bastion-410-containers)"
      in_interface_list = "bastion-410"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "070-dns-iLO-550-tcp" = {
      comment           = "DNS from iLO-550 (was iLO-550-containers)"
      in_interface_list = "iLO-550"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "080-dns-iLO-550-udp" = {
      comment           = "DNS from iLO-550 (was iLO-550-containers)"
      in_interface_list = "iLO-550"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "090-dns-k8s-120-tcp" = {
      comment           = "DNS from k8s-120 (was k8s-120-containers)"
      in_interface_list = "k8s-120"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "100-dns-k8s-120-udp" = {
      comment           = "DNS from k8s-120 (was k8s-120-containers)"
      in_interface_list = "k8s-120"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "110-dns-pve-11-tcp" = {
      comment           = "DNS from pve-11 (was pve-11-containers)"
      in_interface_list = "pve-11"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "120-dns-pve-11-udp" = {
      comment           = "DNS from pve-11 (was pve-11-containers)"
      in_interface_list = "pve-11"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "130-dns-seccam-610-tcp" = {
      comment           = "DNS from seccam-610 (was seccam-610-containers)"
      in_interface_list = "seccam-610"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "140-dns-seccam-610-udp" = {
      comment           = "DNS from seccam-610 (was seccam-610-containers)"
      in_interface_list = "seccam-610"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "150-dns-transit-10-tcp" = {
      comment      = "DNS from transit-10 (was transit-10-containers)"
      in_interface = "bridge-lan-vlan10"
      protocol     = "tcp"
      dst_port     = "53,853"
    }
    "160-dns-transit-10-udp" = {
      comment      = "DNS from transit-10 (was transit-10-containers)"
      in_interface = "bridge-lan-vlan10"
      protocol     = "udp"
      dst_port     = "53,853"
    }
    "170-dns-unifi-frontend-910-tcp" = {
      comment           = "DNS from unifi-frontend-910 (was unifi-frontend-910-containers)"
      in_interface_list = "unifi-frontend-910"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "180-dns-unifi-frontend-910-udp" = {
      comment           = "DNS from unifi-frontend-910 (was unifi-frontend-910-containers)"
      in_interface_list = "unifi-frontend-910"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "190-dns-unifi-mgmt-900-tcp" = {
      comment           = "DNS from unifi-mgmt-900 (was unifi-mgmt-900-containers)"
      in_interface_list = "unifi-mgmt-900"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "200-dns-unifi-mgmt-900-udp" = {
      comment           = "DNS from unifi-mgmt-900 (was unifi-mgmt-900-containers)"
      in_interface_list = "unifi-mgmt-900"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "210-dns-vpn-tcp" = {
      comment           = "DNS from vpn (was vpn-containers)"
      in_interface_list = "vpn"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "220-dns-vpn-udp" = {
      comment           = "DNS from vpn (was vpn-containers)"
      in_interface_list = "vpn"
      protocol          = "udp"
      dst_port          = "53,853"
    }
    "230-dns-vpn-mobile-tcp" = {
      comment           = "DNS from vpn-mobile (was vpn-mobile-containers)"
      in_interface_list = "vpn-mobile"
      protocol          = "tcp"
      dst_port          = "53,853"
    }
    "240-dns-vpn-mobile-udp" = {
      comment           = "DNS from vpn-mobile (was vpn-mobile-containers)"
      in_interface_list = "vpn-mobile"
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
      comment           = "No management (SSH/REST/Winbox/API) from the camera VLAN"
      action            = "drop"
      protocol          = "tcp"
      dst_port          = "22,443,8291,8729"
      in_interface_list = "seccam-610"
    }
    "550-router-service" = {
      comment           = "SSH/REST/Winbox from LAN (credential-protected)"
      protocol          = "tcp"
      dst_port          = "22,443,8291"
      in_interface_list = "lan"
    }
    "560-router-service" = {
      comment           = "SSH/REST/Winbox from the personal VPN (wg01)"
      protocol          = "tcp"
      dst_port          = "22,443,8291"
      in_interface_list = "vpn"
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
      src_address  = "!10.10.60.210"
      action       = "redirect"
    }
    "103-udp" = {
      comment      = "Force DNS for seccam-610"
      in_interface = "bridge-lan-vlan610"
      protocol     = "udp"
      dst_port     = "53"
      to_port      = "53"
      src_address  = "!10.10.60.210"
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
    "112-udp" = {
      comment      = "Force NTP for k8s-120"
      in_interface = "bridge-lan-vlan120"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.120.1"
      to_port      = "123"
      dst_address  = "!10.10.120.1"
    }
    "113-udp" = {
      comment      = "Force NTP for iLO-550"
      in_interface = "bridge-lan-vlan550"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.55.1"
      to_port      = "123"
      dst_address  = "!10.10.55.1"
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
    "120-udp" = {
      comment      = "Force NTP for app-720"
      in_interface = "bridge-lan-vlan720"
      protocol     = "udp"
      dst_port     = "123"
      to_address   = "10.10.72.1"
      to_port      = "123"
      dst_address  = "!10.10.72.1"
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
      alertmanager_tnwks_local = {
        name    = "alertmanager.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      backbox_exporter_tnwks_local = {
        name    = "backbox-exporter.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      changedetection_tnwks_local = {
        name    = "changedetection.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      frigate_tnwks_local = {
        name    = "frigate.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      hajimari_tnwks_local = {
        name    = "hajimari.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      hass_code_tnwks_local = {
        name    = "hass-code.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      hubble_tnwks_local = {
        name    = "hubble.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      internal_tnwks_us = {
        name            = "internal.tnwks.us"
        address         = "10.10.91.142"
        type            = "A"
        ttl             = "1h"
        match_subdomain = true
        comment         = "blocky customDNS"
      }
      loki_tnwks_local = {
        name    = "loki.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      prometheus_tnwks_local = {
        name    = "prometheus.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      prowlarr_tnwks_local = {
        name    = "prowlarr.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      qbittorrent_tnwks_local = {
        name    = "qbittorrent.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      radarr_tnwks_local = {
        name    = "radarr.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      s3_tnwks_local = {
        name    = "s3.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.15.15.50"
      }
      sce_ep01_tnwks_local = {
        name    = "sce-ep01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.140.140"
      }
      sce_er01_tnwks_local = {
        name    = "sce-er01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "172.16.1.1"
      }
      sce_hass01_tnwks_local = {
        name    = "sce-hass01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.72.100"
      }
      sce_pve01_ceph_tnwks_local = {
        name    = "sce-pve01-ceph.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.15.15.50"
      }
      sce_pve01_ilo_tnwks_local = {
        name    = "sce-pve01-ilo.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.55.100"
      }
      sce_pve01_tnwks_local = {
        name    = "sce-pve01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.11.10"
      }
      sce_pve02_ceph_tnwks_local = {
        name    = "sce-pve02-ceph.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.15.15.51"
      }
      sce_pve02_ilo_tnwks_local = {
        name    = "sce-pve02-ilo.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.55.101"
      }
      sce_pve02_tnwks_local = {
        name    = "sce-pve02.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.11.11"
      }
      sce_pve03_ceph_tnwks_local = {
        name    = "sce-pve03-ceph.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.15.15.52"
      }
      sce_pve03_ilo_tnwks_local = {
        name    = "sce-pve03-ilo.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.55.102"
      }
      sce_pve03_tnwks_local = {
        name    = "sce-pve03.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.11.12"
      }
      sce_uisp01_tnwks_local = {
        name    = "sce-uisp01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.72.101"
      }
      sce_vyos01_tnwks_local = {
        name    = "sce-vyos01.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "172.16.1.250"
      }
      sonarr_tnwks_local = {
        name    = "sonarr.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      synapse_admin_tnwks_local = {
        name    = "synapse-admin.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      thanos_tnwks_local = {
        name    = "thanos.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
      }
      uisp_tnwks_us = {
        name            = "uisp.tnwks.us"
        address         = "10.10.91.1"
        type            = "A"
        ttl             = "1h"
        match_subdomain = false
        comment         = "blocky customDNS"
      }
      unifi = {
        name    = "unifi"
        type    = "A"
        comment = "bind db.unifi"
        address = "10.10.91.142"
      }
      unms_tnwks_us = {
        name            = "unms.tnwks.us"
        address         = "10.10.91.1"
        type            = "A"
        ttl             = "1h"
        match_subdomain = false
        comment         = "blocky customDNS"
      }
      zwave_tnwks_local = {
        name    = "zwave.tnwks.local"
        type    = "A"
        comment = "bind db.tnwks.local"
        address = "10.10.120.51"
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

  # Username is injected from SOPS in main.tf (VyOS used
  # ${SSH_VYOS_USERNAME}), so only key material lives here.
  admin_ssh_keys = [
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDQ3xsijUu7JGOG+GVc4FffLRqLF4gWDRT0EWofYinTFGkzxJFhGoS76bbjCU7nUGun5YQbRS7QcWkVKfKBne/ydtc9Mm1OZ/7N3a03TNPnXeFsiNgXfrC8LQN/OKrqreXqMhnj2Hc7o/KTHR/Ui8OE1uDI3mMcETWb1hQJRaLKKMjP5n/N9rEA8tvTK48NptXl0Jf3dJecbYu4/Az+llms9csBBRK2lqGFhYrwn+a7dLYkM80NGjQkk5tIG+0HLCNwYP1vJYLKgEc/XSEvCoyP2gdE3387mMLjBwBF55aDSf/vmnIawkL6hMYCz5CMuiFl7NbxLZk4RKJwdcJiEfX5xzXEu4f3AVIA/uxyM0pTTX1lJrPL+lzKH8VMfVOFxLTuyBU2VBgcbN6J/kHKanjpjcQ4INvmCKsO7YjsGWcE8MzhWfJrFUbX0ri4hhoESk+eqfP+AgSY0x9ibT/pGD0NKJbU6M6QpV3KfbV/JZ1xgakiIJZbleRonFSL1sSX6x0=",
  ]


  # Members of the aggregate zone-lan interface list. Input-chain
  # rules for DNS/NTP/DHCP match this so they cannot be reached from
  # the WAN, closing the EdgeRouter's open-resolver/open-NTP exposure.
  lan_interface_lists = [
    "UISP-140",
    "ad-110",
    "app-720",
    "bastion-410",
    "iLO-550",
    "k8s-120",
    "pve-11",
    "seccam-610",
    "transit-10",
    "unifi-frontend-910",
    "unifi-mgmt-900",
  ]

  # Carried over from the EdgeRouter Lite, which sized conntrack for
  # the full internet-edge load (table-size 32768, tcp loose enable).
  connection_tracking = {
    enabled            = "yes"
    loose_tcp_tracking = "yes"
  }

  # From the EdgeRouter's `service dns dynamic` (namecheap), not VyOS.
  # RouterOS has no Namecheap client, so the module renders a script +
  # scheduler onto the router; see modules/mikrotik/ddns.tf.
  # LOAD-BEARING: the WireGuard client configs use this hostname as
  # their endpoint, so a stale record breaks remote VPN access at the
  # next WAN address change. The password comes from SOPS.
  ddns = {
    provider = "namecheap"
    host     = "ddns"
    domain   = "ddns-domain.example"
    interval = "5m"
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
          public_key           = "RLV0A32MXIFkqLuwLdgpJVDaerxLcCexUCaGOkNte3w="
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
  #   - ad-110-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - address list unifi_controller: VyOS members ['10.10.53.10'] replaced with ['10.10.91.142'] (workload moved)
  #   - app-720-containers: orphaned ruleset, no `firewall zone ... from` binding in the VyOS config, so it never applied (pre-existing VyOS drift)
  #   - bastion-410-ad-110 rule 1: port-group ad_auth_ports has 18 entries (RouterOS max 15); inlined and split across 2 rules
  #   - bastion-410-ad-110 rule 4: port-group ad_auth_ports has 18 entries (RouterOS max 15); inlined and split across 2 rules
  #   - bastion-410-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - containers-k8s-120 rule 1: DROPPED; haproxy -> backend traffic, and haproxy is not ported (its backends are gone)
  #   - containers-seccam-610 rule 1: DROPPED; haproxy -> backend traffic, and haproxy is not ported (its backends are gone)
  #   - containers-unifi-mgmt-900 rule 1: source retargeted to unifi_controller in unifi-frontend-910
  #   - dns: 36 bind A/CNAME records ported as RouterOS static entries; bind SOA/NS have no RouterOS equivalent and are dropped (RouterOS forwards and overrides, it is not authoritative)
  #   - dns: 45 blocky blackList URLs become ip_dns_adlist entries -- see the RAM warning in docs/mikrotik-vyos-port.md
  #   - dns: blocking and split-horizon now apply to EVERY VLAN; dnsdist only sent 10.10.91.0/24 and 10.10.31.0/24 through blocky. RouterOS adlists are global
  #   - dns: blocky customDNS.rewrite entries ['tnwks.us'] are no-ops (key == value) and are not ported
  #   - dns: blocky had 2 whiteList URL(s); RouterOS ip_dns_adlist has no allow-list concept, so these are NOT ported (false positives must be handled with static_records overrides)
  #   - dns: blocky used DoT (tcp-tls) upstreams; RouterOS has no DoT, so the same Cloudflare resolvers are configured with DoH (use_doh_server) plus plain-IP fallback
  #   - dns: blocky wildcard mappings (*.host) collapsed into single RouterOS records with match_subdomain=true: ['internal.tnwks.us']
  #   - dns: blueiris.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: dnsdist DropAction rules are NOT ported (no RouterOS equivalent in the DNS path; use firewall rules instead)
  #   - dns: dnsdist per-subnet ControlD DoH pools ['controld_iot', 'controld_servers', 'controld_trusted'] are NOT ported; RouterOS resolves uniformly. Per-client policy needs ip_dns_forwarders or an off-router resolver
  #   - dns: hass.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: jellyfin.tnwks.local -> 10.10.53.8 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: obsidian-couchdb.tnwks.local -> 10.10.53.9 DROPPED; that was a haproxy container, and the app is served on *.internal.tnwks.us by the WSL ingress now
  #   - dns: unifi retargeted 10.10.53.10 -> 10.10.91.142 (container moved)
  #   - iLO-550-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - k8s-120-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - k8s-120-containers rule 2: retargeted to k8s-120 -> unifi-frontend-910 dst unifi_controller
  #   - k8s-120-containers rule 3: DROPPED; it targeted haproxy_all, which only existed for haproxy (not ported, its backends are gone)
  #   - nat rule 102 (Force DNS for unifi-frontend-910): dst-nat to 10.10.53.4 (DNS container) became a redirect to the router
  #   - nat rule 103 (Force DNS for seccam-610): dst-nat to 10.10.53.4 (DNS container) became a redirect to the router
  #   - nat rule 117 (Force NTP for transit-10) DROPPED: redirect target 172.16.1.254 is dead, so this rule black-holes traffic today
  #   - pve-11-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - route 0.0.0.0/0 via 172.16.1.1: not a static route; becomes upstream_gateway (distance 10) for the staged cutover, and the WAN DHCP lease (distance 1) takes over when the modem moves
  #   - route 10.10.93.0/24 via 172.16.1.254 DROPPED: next-hop is dead (ip neigh FAILED, 100% ping loss). Both VyOS and the EdgeRouter still pointed production subnets at it
  #   - route 10.98.0.0/24 via 172.16.1.254 DROPPED: next-hop is dead (ip neigh FAILED, 100% ping loss). Both VyOS and the EdgeRouter still pointed production subnets at it
  #   - seccam-610-ad-110 rule 1: port-group ad_auth_ports has 18 entries (RouterOS max 15); inlined and split across 2 rules
  #   - seccam-610-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - syslog: target 10.10.120.56 (Vector on the old cluster, gone) -> 10.10.91.142:6001, the WSL host's LAN bridge. Needs vector-aggregator running on WSL and a tcp/6001 lan-bridge relay
  #   - system: 1 SSH public key(s) ported from VyOS `system login user`; the username came from ${SSH_VYOS_USERNAME} and is injected from SOPS in main.tf
  #   - system: VyOS flow export (sFlow, for monitoring/flow-collector) is not generated; the root module's flow_export (IPFIX) replaces it
  #   - system: VyOS task-scheduler (dhcp-leases-textfile.py -> node-exporter dhcp_lease_info, used for flow-collector client names) has no RouterOS port; client names need a RouterOS lease exporter (e.g. mktxp)
  #   - system: identity is sce-rtr01, NOT the VyOS name sce-vyos01 -- the two run concurrently until the trunk swings. The bind zone has an A record for sce-vyos01 (172.16.1.250); add one for sce-rtr01 before cutover
  #   - transit-10-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - unifi-frontend-910-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - unifi-frontend-910-containers rule 2: DROPPED; it targeted haproxy_frontend, which only existed for haproxy (not ported, its backends are gone)
  #   - unifi-mgmt-900-containers rule 1 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - unifi-mgmt-900-containers rule 2: retargeted to unifi-mgmt-900 -> unifi-frontend-910 dst unifi_controller
  #   - vpn-containers (default-action accept): DNS moved to the input chain; other container access follows vpn -> unifi-frontend-910
  #   - vpn-mobile-containers rule 1: DROPPED; it targeted haproxy_authenticated, which only existed for haproxy (not ported, its backends are gone)
  #   - vpn-mobile-containers rule 2 (accept_dns): moved to the input chain; native RouterOS DNS replaces the dnsdist container
  #   - vpn-mobile-containers rule 3: retargeted to vpn-mobile -> unifi-frontend-910 dst unifi_controller
  #   - vpn-mobile-containers rule 4: DROPPED; it targeted haproxy_frontend, which only existed for haproxy (not ported, its backends are gone)
}
