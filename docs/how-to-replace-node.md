## Begin new install

1. Connect via iLO, attach Proxmox ISO, and begin new install on new drive


## Remove references to old node

#######################################################################################################
#####  https://forum.proxmox.com/threads/replace-dead-node-that-had-ceph-services-running.127207/ #####
#######################################################################################################

1. Remove MON on the node `ceph mon remove {mon-id}`

2. Remove entries in `/etc/pve/ceph.conf`

3. Remove OSDs `ceph osd purge {id} --yes-i-really-mean-it`

4. Remove from CRUSH map `ceph osd crush remove {bucket-name}`

5. Remove entries in `/etc/pve/priv/authorized_keys` , `/etc/pve/priv/authorized_keys`, and folder in `/etc/pve/nodes`.  Back up the folder if needed.


## Set network to use VLAN correctly

1. Edit /etc/network/interfaces

```
auto vmbr0
iface vmbr0 inet manual
        bridge-ports eno4
        bridge-stp off
        bridge-fd 0
        bridge-vlan-aware yes
        bridge-vids 2-4094

auto vmbr0.11
iface vmbr0.11 inet static
        address {{ lookup('dig', ansible_host) }}
        netmask 255.255.255.0
        gateway 10.10.11.1
        vlan-raw-device vmbr0
        vlan-id 11
```

2. `ifreload -a`


## Set local user

1. ssh using root credentials
2. Run the following:
```
username="yourusernamehere"
proxmox_host="10.10.11.11"

adduser "$username"
usermod -aG sudo "$username"
exit
ssh-copy-id "$username@$proxmox_host"
ssh "$username@$proxmox_host" echo "SSH authentication successful. Passwordless login is working."
sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

## Install and configure prereq software
1. `apt-get update`
2. `apt-get install sudo`
3. sudo adduser `<your_username>` sudo
4. `apt install openvswitch-switch -y`

Comment out:
`root@sce-pve02:~# cat /etc/apt/sources.list.d/pve-enterprise.list
#deb https://enterprise.proxmox.com/debian/pve bullseye pve-enterprise`

## Zap existing drives to wipe:
```
sgdisk --zap-all /dev/sdc
readlink /sys/block/sdc
sgdisk --zap-all /dev/sdd
readlink /sys/block/sdd
sgdisk --zap-all /dev/sda
readlink /sys/block/sda
sgdisk --zap-all /dev/sdb
readlink /sys/block/sdb

echo 1 > /sys/block/sdc/device/delete
echo 1 > /sys/block/sdd/device/delete
echo 1 > /sys/block/sda/device/delete
echo 1 > /sys/block/sdb/device/delete
echo "- - -" > /sys/class/scsi_host/host2/scan

#reboot
```

## Run Ansible
1. `<your_username>@THO-PC02:~/git/tnwks-ops/infrastructure/ansible$ ansible-playbook -i inventory/sce-pvecl01-hosts.yml playbooks/proxmox-configure.yml`

## Install customizations
- run through the software at provision.md
