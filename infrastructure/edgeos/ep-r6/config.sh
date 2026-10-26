# EP-R6 (sce-ep01, 10.10.140.140) desired configuration, EdgeOS `set` form.
# Exactly what `show configuration commands` prints; ${name} placeholders come
# from secrets.sops.yaml. Plan/apply: task edgeos:plan / task edgeos:apply
# (infrastructure/edgeos/README.md). Edit here, not on the device.
set firewall all-ping enable
set firewall broadcast-ping disable
set firewall ipv6-receive-redirects disable
set firewall ipv6-src-route disable
set firewall ip-src-route disable
set firewall log-martians enable
set firewall name WAN_IN default-action accept
set firewall name WAN_IN description 'WAN to internal'
set firewall name WAN_IN rule 10 action accept
set firewall name WAN_IN rule 10 description 'Allow established/related'
set firewall name WAN_IN rule 10 state established enable
set firewall name WAN_IN rule 10 state related enable
set firewall name WAN_IN rule 20 action accept
set firewall name WAN_IN rule 20 description 'Drop invalid state'
set firewall name WAN_IN rule 20 log disable
set firewall name WAN_IN rule 20 state invalid enable
set firewall name WAN_LOCAL default-action accept
set firewall name WAN_LOCAL description 'WAN to router'
set firewall name WAN_LOCAL rule 10 action accept
set firewall name WAN_LOCAL rule 10 description 'Allow established/related'
set firewall name WAN_LOCAL rule 10 state established enable
set firewall name WAN_LOCAL rule 10 state related enable
set firewall name WAN_LOCAL rule 20 action accept
set firewall name WAN_LOCAL rule 20 description 'Drop invalid state'
set firewall name WAN_LOCAL rule 20 log disable
set firewall name WAN_LOCAL rule 20 state invalid enable
set firewall receive-redirects disable
set firewall send-redirects enable
set firewall source-validation disable
set firewall syn-cookies enable
set interfaces ethernet eth0 address dhcp
set interfaces ethernet eth0 description Internet
set interfaces ethernet eth0 duplex auto
set interfaces ethernet eth0 firewall in name WAN_IN
set interfaces ethernet eth0 firewall local name WAN_LOCAL
set interfaces ethernet eth0 speed auto
set interfaces ethernet eth1 description Local
set interfaces ethernet eth1 duplex auto
set interfaces ethernet eth1 speed auto
set interfaces ethernet eth2 description Local
set interfaces ethernet eth2 duplex auto
set interfaces ethernet eth2 poe output 24v
set interfaces ethernet eth2 speed auto
set interfaces ethernet eth3 description Local
set interfaces ethernet eth3 duplex auto
set interfaces ethernet eth3 poe output 24v
set interfaces ethernet eth3 speed auto
set interfaces ethernet eth4 description Local
set interfaces ethernet eth4 duplex auto
set interfaces ethernet eth4 poe output off
set interfaces ethernet eth4 speed auto
set interfaces ethernet eth5 duplex auto
set interfaces ethernet eth5 speed auto
set interfaces loopback lo
set interfaces switch switch0 address 10.60.10.1/24
set interfaces switch switch0 description Local
set interfaces switch switch0 mtu 1500
set interfaces switch switch0 switch-port interface eth1
set interfaces switch switch0 switch-port interface eth2
set interfaces switch switch0 switch-port interface eth3
set interfaces switch switch0 switch-port interface eth4
set interfaces switch switch0 switch-port vlan-aware disable
set service dhcp-server disabled false
set service dhcp-server hostfile-update disable
set service dhcp-server shared-network-name LAN authoritative enable
set service dhcp-server shared-network-name LAN subnet 10.60.10.0/24 default-router 10.60.10.1
set service dhcp-server shared-network-name LAN subnet 10.60.10.0/24 dns-server 10.10.53.4
set service dhcp-server shared-network-name LAN subnet 10.60.10.0/24 lease 86400
set service dhcp-server shared-network-name LAN subnet 10.60.10.0/24 start 10.60.10.38 stop 10.60.10.243
set service dhcp-server static-arp disable
set service dhcp-server use-dnsmasq disable
set service dns forwarding cache-size 150
set service dns forwarding listen-on eth0
set service dns forwarding name-server 10.10.53.4
set service gui http-port 80
set service gui https-port 443
set service gui older-ciphers enable
set service nat rule 5010 description 'masquerade for WAN'
set service nat rule 5010 log disable
set service nat rule 5010 outbound-interface eth0
set service nat rule 5010 protocol all
set service nat rule 5010 type masquerade
set service ssh port 22
set service ssh protocol-version v2
set service unms connection '${uisp_connection}'
set system analytics-handler send-analytics-report false
set system crash-handler send-crash-report false
set system host-name ubnt
set system login user ${admin_user} authentication encrypted-password '${admin_password_hash}'
set system login user ${admin_user} authentication plaintext-password ''
set system login user ${admin_user} level admin
set system name-server 10.10.53.4
set system ntp server 0.ubnt.pool.ntp.org
set system ntp server 1.ubnt.pool.ntp.org
set system ntp server 2.ubnt.pool.ntp.org
set system ntp server 3.ubnt.pool.ntp.org
set system syslog global facility all level notice
set system syslog global facility protocols level debug
set system time-zone UTC
set traffic-control optimized-queue policy global
