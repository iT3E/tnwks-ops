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

# Installing prometheus pve-exporter
https://github.com/prometheus-pve/prometheus-pve-exporter/wiki/PVE-Exporter-on-Proxmox-VE-Node-in-a-venv
```
#first create user in PVE with PVEAuditor role.  This is done with ansible currently.
#then create an API token which has to be done manually

#run commands on each node
sudo useradd -s /bin/false pve-exporter

sudo apt update
sudo apt install python3-venv -y

sudo python3 -m venv /opt/prometheus-pve-exporter

sudo /opt/prometheus-pve-exporter/bin/pip install prometheus-pve-exporter

sudo mkdir -p /etc/prometheus
sudo nano /etc/prometheus/pve.yml
###
default:
    user: pve-exporter@pam
    token_name: "<tokenname>"
    token_value: "<tokensecret>"
    verify_ssl: false
###

sudo nano /etc/systemd/system/prometheus-pve-exporter.service

###
[Unit]
Description=Prometheus exporter for Proxmox VE
Documentation=https://github.com/znerol/prometheus-pve-exporter

[Service]
Restart=always
User=pve-exporter
ExecStart=/opt/prometheus-pve-exporter/bin/pve_exporter /etc/prometheus/pve.yml

[Install]
WantedBy=multi-user.target
###

sudo systemctl daemon-reload
sudo systemctl start prometheus-pve-exporter
sudo systemctl enable prometheus-pve-exporter

#test if port is listening
sudo ss -tuln | grep 9221
```

# Installing prometheus node-exporter (for additional non-pve monitors)
### download node-exporter
curl -s https://api.github.com/repos/prometheus/node_exporter/releases/latest| grep browser_download_url|grep linux-amd64|cut -d '"' -f 4|wget -qi -

tar -xvf node_exporter*.tar.gz
cd  node_exporter*/
sudo cp node_exporter /usr/local/bin

### confirm install
node_exporter --version

### create node_exporter service
sudo tee /etc/systemd/system/node_exporter.service <<EOF
[Unit]
Description=Node Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=pve-exporter
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=default.target
EOF

### reload node_exporter service
sudo systemctl daemon-reload
sudo systemctl start node_exporter
sudo systemctl enable node_exporter

### test if port is listening
sudo ss -tuln | grep 9100


# Installing RADOSGW Exporter
### official documentation install:
```
git clone git@github.com:blemmenes/radosgw_usage_exporter.git
cd radosgw_usage_exporter
pip install requirements.txt
```
### actual install
```
curl -LJO https://github.com/blemmenes/radosgw_usage_exporter/archive/refs/heads/master.zip
unzip radosgw_usage_exporter-main.zip
pip install requirements.txt
```

-----------------------------------
# shipping syslog
```
sudo echo "*.* @@10.10.120.56:6003" | sudo tee /etc/rsyslog.d/99-vector.conf
sudo systemctl restart rsyslog

```

# prevent logs from being written locally:
```
sudo cp /etc/rsyslog.conf /etc/rsyslog.conf.bak
sudo nano /etc/rsyslog.conf

comment out lines like below:

#auth,authpriv.*                 /var/log/auth.log
#*.*;auth,authpriv.none          -/var/log/syslog
##cron.*                         /var/log/cron.log
#daemon.*                        -/var/log/daemon.log
#kern.*                          -/var/log/kern.log
#lpr.*                           -/var/log/lpr.log
#mail.*                          -/var/log/mail.log
#user.*                          -/var/log/user.log
#mail.info                       -/var/log/mail.info
#mail.warn                       -/var/log/mail.warn
#mail.err                        /var/log/mail.err
#*.=debug;\
#        auth,authpriv.none;\
#        mail.none               -/var/log/debug
#*.=info;*.=notice;*.=warn;\
#        auth,authpriv.none;\
#        cron,daemon.none;\
#        mail.none               -/var/log/messages
#*.emerg                         :omusrmsg:*

#you can use this command to automate:
sudo sed -i -E -e 's/^(auth,authpriv.*\/var\/log\/auth.log)/#\1/' \
-e 's/^(\*.*;auth,authpriv.none.*-\/var\/log\/syslog)/#\1/' \
-e 's/^(cron.*\/var\/log\/cron.log)/#\1/' \
-e 's/^(daemon.*-\/var\/log\/daemon.log)/#\1/' \
-e 's/^(kern.*-\/var\/log\/kern.log)/#\1/' \
-e 's/^(lpr.*-\/var\/log\/lpr.log)/#\1/' \
-e 's/^(mail.*-\/var\/log\/mail.log)/#\1/' \
-e 's/^(user.*-\/var\/log\/user.log)/#\1/' \
-e 's/^(mail.info.*-\/var\/log\/mail.info)/#\1/' \
-e 's/^(mail.warn.*-\/var\/log\/mail.warn)/#\1/' \
-e 's/^(mail.err.*\/var\/log\/mail.err)/#\1/' \
-e 's/^(\*=debug;.*auth,authpriv.none;.*mail.none.*-\/var\/log\/debug)/#\1/' \
-e 's/^(\*=info;\*=notice;\*=warn;.*auth,authpriv.none;.*cron,daemon.none;.*mail.none.*-\/var\/log\/messages)/#\1/' \
-e 's/^(\*.emerg.*:omusrmsg:.*)/#\1/' /etc/rsyslog.conf

sudo systemctl restart rsyslog

sudo nano /etc/systemd/journald.conf

#change line containing 'Storage=auto' to:
Storage=volatile

sudo systemctl restart systemd-journald


```
