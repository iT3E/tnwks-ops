###############################################################################################
#  https://forum.proxmox.com/threads/replace-dead-node-that-had-ceph-services-running.127207/ #
###############################################################################################

1. Remove MON on the node `ceph mon remove {mon-id}`

2. Remove entries in `/etc/pve/ceph.conf`

3. Remove OSDs `ceph osd purge {id} --yes-i-really-mean-it`

4. Remove from CRUSH map `ceph osd crush remove {bucket-name}`

5. Remove entries in `/etc/pve/priv/authorized_keys` , `/etc/pve/priv/authorized_keys`, and folder in `/etc/pve/nodes`.  Back up the folder if needed.
