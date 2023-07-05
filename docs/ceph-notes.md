### ceph troubleshooting:

```
ceph health detail
```
shows general ceph cluster detail. will show when pg's are broken.


```
ceph pg ls-by-pool pool-hdd-hostfd
```
shows PG placement per OSD [1,5,4]p1 means the pg is on 3 disks, osd1, osd5, and osd4, with osd1 being the 'primary'.

```
ceph osd tree
```
shows OSD mapping on all nodes.

```
ceph -s
```
shows recovery detail

### outage 2023-07-05

ceph network was not listed in /etc/hosts.  Current config:

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
