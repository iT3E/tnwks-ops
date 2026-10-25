#!/bin/bash
# ---------------------------------------------------------------------------------------------------------------------
# MikroTik RB5009 bootstrap: RouterOS-side prerequisites Terraform cannot create.
#
# Terraform authenticates to the RouterOS REST API. It therefore cannot be the
# thing that creates the API listener, the certificate, the service account it
# logs in with, or the IP address it connects over. That is this script's job.
#
# HOW TO RUN
#   This is NOT executed from the repo. RouterOS runs it.
#
#   1. Reset WITHOUT the default configuration. Defconf creates its own bridge
#      (ether2-8 + sfp), its own firewall and a DHCP server; all of it would
#      collide with Terraform's bridge ports and leave stray rules outside the
#      ordering Terraform enforces:
#
#        /system reset-configuration no-defaults=yes skip-backup=yes
#
#   2. After the reboot there is no IP config at all. Connect a laptop to ether8
#      and open the serial console, or Winbox by MAC address, and paste this
#      script.
#   3. Give the laptop 10.98.0.2/24 on that cable. ether8 is the out-of-band
#      management port permanently: it is never added to the LAN bridge, and
#      the Terraform input chain always accepts SSH/REST/Winbox on it.
#
#   Order matters: run this, verify the API is reachable, THEN run
#   `task terraform:mikrotik:apply`.
#
# WHAT THIS DELIBERATELY DOES NOT DO
#   No VLANs, no firewall, no DHCP, no DNS, no WireGuard. All of that is
#   Terraform's. Anything configured here that Terraform also manages would
#   fight the next apply. Keep this file minimal.
# ---------------------------------------------------------------------------------------------------------------------
# shellcheck disable=all
#
# ---------------------------------------------------------------------------------------------------------------------
# 0. PREREQUISITES (physical, before any of the below)
# ---------------------------------------------------------------------------------------------------------------------
#   * RouterOS 7.x. Check with `/system package print`. If the unit shipped on
#     6.x, upgrade before anything else; the provider targets 7.x REST.
#   * The `container` package requires physical access to enable
#     (`/system device-mode update container=yes` forces a hardware
#     confirmation: press the reset button or power-cycle within the timeout).
#     The port does NOT use containers, so skip unless that changes.
#   * Out-of-band mgmt is 10.98.0.1/24 on ether8, a dedicated cable to the
#     laptop (or later a mgmt port on the switch). Nothing else lives in
#     10.98.0.0/24: the ERL's eth1 there was never used, and both routers'
#     10.98.0.0/24 routes pointed at a dead host.
#
# ---------------------------------------------------------------------------------------------------------------------
# 1. IDENTITY + ADMIN ACCOUNT
# ---------------------------------------------------------------------------------------------------------------------
# A no-defaults reset leaves a passwordless `admin`. Create the Terraform account
# first, verify login works, and only then remove admin. Do not reverse this
# order.
#
# The interactive admin account is NOT created here: Terraform creates it, with
# its password and SSH key from SOPS. Creating it here too would make the first
# apply fail on a duplicate user. Until then, the terraform account is the
# console fallback.

/system identity set name=sce-rtr01

# Terraform's service account. The password must match `routeros_password` in
# secrets.sops.yaml. Generate it with `openssl rand -base64 24`, do not reuse
# an existing device password (the homelab has credential-reuse debt already).
# No address= restriction: access is gated by this credential plus the
# Terraform input chain (LAN, personal VPN and the OOB port; never the WAN).
/user add name=terraform group=full password="CHANGEME-SEE-SOPS" \
  comment="Terraform service account (tnwks-ops)"

# Verify the terraform account works in a second session before running this.
/user remove [find name=admin]

# ---------------------------------------------------------------------------------------------------------------------
# 2. MANAGEMENT REACHABILITY
# ---------------------------------------------------------------------------------------------------------------------
# Terraform needs an IP to talk to. This is the one address the module does not
# manage, because removing it mid-apply would sever the connection doing the
# apply. Everything else is Terraform's.
#
# ether8 is excluded from Terraform's bridge (var.mgmt). It must stay that way:
# an address on a bridge member port goes invalid, which is an instant lockout.
#
# No default route here. Terraform owns it (upstream_gateway, then the WAN
# lease), and a bench router needs no internet to be configured.

/ip address add address=10.98.0.1/24 interface=ether8 comment="out-of-band mgmt (bootstrap-owned, do not remove)"

# ---------------------------------------------------------------------------------------------------------------------
# 3. REST API + TLS
# ---------------------------------------------------------------------------------------------------------------------
# The provider uses https://<host>/rest, which is the `www-ssl` service. It
# needs a certificate; RouterOS will not enable www-ssl without one.
#
# Self-signed is fine to start. The module sets `insecure = true` to match. Once
# the PKI project issues a real cert, import it, point www-ssl at it, and flip
# `insecure` to false in providers.tf.

/certificate add name=rest-api common-name=sce-rtr01.tnwks.local \
  key-usage=digital-signature,key-encipherment,tls-server days-valid=3650
/certificate sign rest-api

/ip service set www-ssl certificate=rest-api disabled=no port=443
/ip service set ssh port=22 disabled=no

# Everything else off. Terraform reasserts this, but leaving telnet/ftp/api open
# between bootstrap and first apply is a needless exposure window.
/ip service set telnet disabled=yes
/ip service set ftp disabled=yes
/ip service set www disabled=yes
/ip service set api disabled=yes
/ip service set api-ssl disabled=yes

# ---------------------------------------------------------------------------------------------------------------------
# 4. ROUTERBOARD / HARDWARE
# ---------------------------------------------------------------------------------------------------------------------
/system routerboard settings set auto-upgrade=no
/system note set note="Managed by Terraform (tnwks-ops). Do not edit via Winbox." show-at-login=yes

# VyOS ran `ethtool --set-eee <iface> eee off` on every interface from its
# post-config bootup script, because EEE caused link flaps on this switch
# topology. RouterOS has no EEE toggle, so there is nothing to port. Flagging it
# so nobody spends an afternoon looking: if link flaps appear after cutover,
# this is the first thing to suspect and it must be addressed on the Aruba side.

# ---------------------------------------------------------------------------------------------------------------------
# 5. VERIFY, THEN HAND OFF TO TERRAFORM
# ---------------------------------------------------------------------------------------------------------------------
# From the workstation:
#
#   curl -k -u terraform:<password> https://10.98.0.1/rest/system/resource
#
# A JSON body means Terraform can take over:
#
#   task terraform:mikrotik:init
#   task terraform:mikrotik:plan
#
# Read the plan. It should create the bridge, VLANs, addresses, DHCP, DNS,
# firewall and WireGuard. It must NOT list ether8 as a bridge port or touch the
# 10.98.0.1/24 address. If it does, stop and fix the module.
# ---------------------------------------------------------------------------------------------------------------------
