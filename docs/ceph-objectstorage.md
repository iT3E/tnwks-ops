sudo ceph-authtool --create-keyring /etc/pve/priv/ceph.client.radosgw.keyring

sudo ceph-authtool /etc/pve/priv/ceph.client.radosgw.keyring -n client.radosgw.sce-pve01 --gen-key
sudo ceph-authtool /etc/pve/priv/ceph.client.radosgw.keyring -n client.radosgw.sce-pve02 --gen-key
sudo ceph-authtool /etc/pve/priv/ceph.client.radosgw.keyring -n client.radosgw.sce-pve03 --gen-key

sudo ceph-authtool -n client.radosgw.sce-pve01 --cap osd 'allow rwx' --cap mon 'allow rwx' /etc/pve/priv/ceph.client.radosgw.keyring
sudo ceph-authtool -n client.radosgw.sce-pve02 --cap osd 'allow rwx' --cap mon 'allow rwx' /etc/pve/priv/ceph.client.radosgw.keyring
sudo ceph-authtool -n client.radosgw.sce-pve03 --cap osd 'allow rwx' --cap mon 'allow rwx' /etc/pve/priv/ceph.client.radosgw.keyring

sudo ceph -k /etc/pve/priv/ceph.client.admin.keyring auth add client.radosgw.sce-pve01 -i /etc/pve/priv/ceph.client.radosgw.keyring
sudo ceph -k /etc/pve/priv/ceph.client.admin.keyring auth add client.radosgw.sce-pve02 -i /etc/pve/priv/ceph.client.radosgw.keyring
sudo ceph -k /etc/pve/priv/ceph.client.admin.keyring auth add client.radosgw.sce-pve03 -i /etc/pve/priv/ceph.client.radosgw.keyring

### Add the following lines to /etc/ceph/ceph.conf:

[client.radosgw.sce-pve01]
        host = sce-pve01
        keyring = /etc/pve/priv/ceph.client.radosgw.keyring
        log file = /var/log/ceph/client.radosgw.$host.log
        rgw_dns_name = s3.tnwks.local

[client.radosgw.sce-pve02]
        host = sce-pve02
        keyring = /etc/pve/priv/ceph.client.radosgw.keyring
        log file = /var/log/ceph/client.radosgw.$host.log
        rgw_dns_name = s3.tnwks.local

[client.radosgw.sce-pve03]
        host = sce-pve03
        keyring = /etc/pve/priv/ceph.client.radosgw.keyring
        log file = /var/log/ceph/client.rados.$host.log
        rgw_dns_name = s3.tnwks.local

### run the following on each node:
apt install radosgw

### Create systemd service symlink on each node
mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve01
systemctl daemon-reload

mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve02
systemctl daemon-reload

mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve03
systemctl daemon-reload

### start gateway per node
systemctl start ceph-radosgw@radosgw.sce-pve01
systemctl start ceph-radosgw@radosgw.sce-pve02
systemctl start ceph-radosgw@radosgw.sce-pve03

