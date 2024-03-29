## outage 2023-07-05

### Root cause:
Power outage caused a reboot of all nodes. Ceph network was not listed in /etc/hosts on any hosts.  Updated the configs and rebooted each node.

### Details:
Current config:

```
$ cat /etc/hosts

127.0.0.1 localhost.localdomain localhost
10.15.15.50 sce-pve01 10.15.15.50
# The following lines are desirable for IPv6 capable hosts

::1     ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
ff02::3 ip6-allhosts
# BEGIN ANSIBLE MANAGED: Proxmox Cluster Hosts
sce-pve01-ceph.tnwks.local sce-pve01.tnwks.local sce-pve01

sce-pve02-ceph.tnwks.local sce-pve02.tnwks.local sce-pve02

sce-pve03-ceph.tnwks.local sce-pve03.tnwks.local sce-pve03

# END ANSIBLE MANAGED: Proxmox Cluster Hosts
```

after editing /etc/hosts, PVE hosts were again reachable and ceph started a recovery, however recovery performance was poor. Tried the following commands, but what fully resolved was simply a reboot of each node.

```
killall -9 corosync
systemctl restart pve-cluster
systemctl restart pvedaemon
systemctl restart pveproxy
systemctl restart pvestatd
```
```
systemctl restart ceph.target
```

Ran the below command on pve01 and pve02 to try and speed up recovery, but no change.
```
ceph tell 'osd.*' injectargs --osd-max-backfills=3 --osd-recovery-max-active=9
```

# Update 2023-07-27
```
When running ansible again, hosts file was reset.  In an effort to save time, i've manually added back the records, but made some additional records due to new understanding of the issue:
10.15.15.50 sce-pve01 10.15.15.50
10.15.15.51 sce-pve02 10.15.15.51
10.15.15.52 sce-pve03 10.15.15.52

entire hosts file on each system looks like the following:

127.0.0.1 localhost.localdomain localhost
10.15.15.50 sce-pve01 10.15.15.50
10.15.15.51 sce-pve02 10.15.15.51
10.15.15.52 sce-pve03 10.15.15.52
# The following lines are desirable for IPv6 capable hosts

::1     ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
ff02::3 ip6-allhosts
# BEGIN ANSIBLE MANAGED: Proxmox Cluster Hosts
sce-pve01-ceph.tnwks.local 10.15.15.50 sce-pve01

sce-pve02-ceph.tnwks.local sce-pve02 sce-pve02

sce-pve03-ceph.tnwks.local sce-pve03 sce-pve03

# END ANSIBLE MANAGED: Proxmox Cluster Hosts
```
