# Manual tasks performed that need to be automated
## disable swap

```
swapoff /dev/pve/swap
lvchange -a n /dev/pve/swap
lvremove /dev/pve/swap
sudo sed -i '/\/dev\/pve\/swap/s/^/#/' /etc/fstab
```

## increase drive space of root
```
sudo lvresize -l +100%FREE /dev/pve/root
```
## add pve-no-subscription to apt sources
echo "deb http://download.proxmox.com/debian/pve bullseye pve-no-subscription" >> /etc/apt/sources.list

## add ceph quincy rather than pacific to apt sources
sed -i 's/pacific/quincy/' /etc/apt/sources.list.d/ceph.list

## create OSDs on each system
```
pveceph osd create /dev/sda
pveceph osd create /dev/sdb
```

## create mds on each system
```
pveceph mds create
```

## create a ceph file system on one
```
pveceph fs create --pg_num 128 --add-storage
```

# How to wipe ceph and reinstall

```
systemctl restart pvestatd
rm -rf /etc/systemd/system/ceph*
killall -9 ceph-mon ceph-mgr ceph-mds

sgdisk --zap-all /dev/sda
readlink /sys/block/sda
sgdisk --zap-all /dev/sdb
readlink /sys/block/sdb
#read the "host" section and use it for following commands
#../devices/pci0000:00/0000:00:01.1/0000:01:00.0/host5/port-5:10/end_device-5:10/target5:0:10/5:0:10:0/block/sdx
echo 1 > /sys/block/sda/device/delete
echo 1 > /sys/block/sdb/device/delete
echo "- - -" > /sys/class/scsi_host/host2/scan
#reboot

rm -rf /etc/ceph /etc/pve/ceph.conf /etc/pve/priv/ceph* /var/lib/ceph

#lsblk -> gives you a long ID starting with "ceph-"
#dmsetup remove <ID beginning with "ceph">


pveceph purge
systemctl restart pvestatd
apt purge ceph-mon ceph-osd ceph-mgr ceph-mds -y
systemctl restart pvestatd
rm /etc/init.d/ceph
for i in $(apt search ceph | grep installed | awk -F/ '{print $1}'); do apt reinstall $i; done
dpkg-reconfigure ceph-base
dpkg-reconfigure ceph-mds
dpkg-reconfigure ceph-common
dpkg-reconfigure ceph-fuse
for i in $(apt search ceph | grep installed | awk -F/ '{print $1}'); do apt reinstall $i; done
systemctl restart pvestatd
mkdir -p /etc/ceph
mkdir -p /var/lib/ceph/bootstrap-osd
mkdir -p /var/lib/ceph/mgr
mkdir -p /var/lib/ceph/mon
#reboot needed for disks to show back up
#GUI from here
```

# Copying local ssh cert and configuring user

```
username="yourusernamehere"
proxmox_host="10.10.11.12"

adduser "$username"
usermod -aG sudo "$username"
exit
ssh-copy-id "$username@$proxmox_host"
ssh "$username@$proxmox_host" echo "SSH authentication successful. Passwordless login is working."
sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

# Installing prometheus exporter
```
#run on each node
sudo apt install python3-pip -y
sudo python3 -m pip install prometheus-pve-exporter
```
