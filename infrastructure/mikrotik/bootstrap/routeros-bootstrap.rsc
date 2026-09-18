#!/bin/bash
# ---------------------------------------------------------------------------------------------------------------------
# MikroTik RB5009 bootstrap: RouterOS-side prerequisites Terraform cannot create.
#
# Terraform authenticates to the RouterOS REST API. It therefore cannot be the
# thing that creates the API listener, the certificate, the service account it
# logs in with, or the IP address it connects over. That is this script's job.
#
# HOW TO RUN
#   This is NOT executed from the repo. RouterOS runs it. Paste it into a Winbox
#   or serial terminal on a factory-reset RB5009 while connected to ether2 with
#   a DHCP client (RouterOS defconf hands out 192.168.88.0/24 there).
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
#   * Note the mgmt subnet: 10.98.0.0/24, matching the VyOS eth1 native address.
#     The Aruba trunk delivers it untagged.
#
# ---------------------------------------------------------------------------------------------------------------------
# 1. IDENTITY + ADMIN ACCOUNT
# ---------------------------------------------------------------------------------------------------------------------
# RouterOS ships a passwordless `admin`. Create the real accounts first, verify
# login works, and only then remove admin. Do not reverse this order.

/system identity set name=sce-rtr01

# Terraform's service account. The password must match `routeros_password` in
# secrets.sops.yaml. Generate it with `openssl rand -base64 24`, do not reuse
# an existing device password (the homelab has credential-reuse debt already).
/user add name=terraform group=full password="CHANGEME-SEE-SOPS" \
  comment="Terraform service account (tnwks-ops)" \
  address=10.98.0.0/24,10.10.91.0/24

# Interactive admin account, SSH key only.
/user add name=it3e group=full password="CHANGEME" comment="Ivan"
/user ssh-keys import public-key-file=id_rsa.pub user=it3e

# Verify BOTH accounts work in a second session before running this line.
/user remove [find name=admin]

# ---------------------------------------------------------------------------------------------------------------------
# 2. MANAGEMENT REACHABILITY
# ---------------------------------------------------------------------------------------------------------------------
# Terraform needs an IP to talk to. This is the one address the module does not
# manage, because removing it mid-apply would sever the connection doing the
# apply. Everything else is Terraform's.
#
# Put the mgmt address on the physical trunk port directly for bootstrap. Once
# Terraform builds the bridge, mgmt lives on the bridge instead; this address is
# then redundant and can be removed in the cutover's final step.

/ip address add address=10.98.0.1/24 interface=ether2 comment="bootstrap mgmt - remove after cutover"

# Default route to the EdgeRouter, matching the VyOS static route. Terraform
# also manages this; setting it here just keeps the box reachable meanwhile.
/ip route add dst-address=0.0.0.0/0 gateway=172.16.1.1 comment="bootstrap default - Terraform owns this"

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
# firewall and WireGuard, and it should NOT propose destroying the bootstrap
# mgmt address out from under itself. If it does, stop and fix the module.
# ---------------------------------------------------------------------------------------------------------------------
