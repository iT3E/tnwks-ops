https://forum.proxmox.com/threads/activate-ceph-object-storage.41481/post-200630

```
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
```
### Add the following lines to /etc/ceph/ceph.conf:
```
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
```

### run the following on each node:
```
apt install radosgw
```

### Create systemd service symlink on each node
```
mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve01
systemctl daemon-reload

mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve02
systemctl daemon-reload

mkdir /etc/systemd/system/ceph-radosgw.target.wants
ln -s /lib/systemd/system/ceph-radosgw@.service /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve03
systemctl daemon-reload
```

### start gateway per node

```
systemctl start ceph-radosgw@radosgw.sce-pve01
systemctl start ceph-radosgw@radosgw.sce-pve02
systemctl start ceph-radosgw@radosgw.sce-pve03

```
### set Restart behavior on each host
`cat /etc/systemd/system/ceph-radosgw.target.wants/ceph-radosgw@radosgw.radosgw.sce-pve01`
```
[Unit]
Description=Ceph rados gateway
PartOf=ceph-radosgw.target
After=network-online.target local-fs.target time-sync.target
Before=ceph-radosgw.target
Wants=network-online.target local-fs.target time-sync.target ceph-radosgw.target

[Service]
Environment=CLUSTER=ceph
EnvironmentFile=-/etc/default/ceph
ExecStart=/usr/bin/radosgw -f --cluster ${CLUSTER} --name client.%i --setuser ceph --setgroup ceph
LimitNOFILE=1048576
LimitNPROC=1048576
LockPersonality=true
MemoryDenyWriteExecute=true
NoNewPrivileges=true
PrivateDevices=yes
PrivateTmp=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=full
Restart=always
RestrictSUIDSGID=true
StartLimitBurst=5
StartLimitInterval=30s
TasksMax=infinity

[Install]
WantedBy=ceph-radosgw.target
```
### restart and enable service

```
sudo systemctl daemon-reload
sudo systemctl start ceph-radosgw@radosgw.sce-pve01
sudo systemctl enable ceph-radosgw@radosgw.sce-pve01
```


### output is generated that contains access and secret
### add both access and secret to a k8s secret
```
radosgw-admin user create --uid=rgw-admin-ops-user --display-name="RGW Admin Ops User" --caps="buckets=*;users=*;usage=read;metadata=read;zone=read"

echo -n "<access_key>" | base64
```

To modify the RGW zone, you would use the radosgw-admin command line tool, for example:

bash
Copy code
`radosgw-admin zone get > zone.json`
This command will fetch the current configuration of the default zone and store it in a file named zone.json.

You can then modify zone.json to specify your custom pools. For example:

```json
{
    "id": "d4018b8d-8c0d-4072-8919-608726fa369e",
    "name": "default",
    "domain_root": ".rgw.root",
    "control_pool": "sce-pvecl01.rgw.control",
    "gc_pool": "default.rgw.gc",
    "log_pool": "sce-pvecl01.rgw.log",
    "intent_log_pool": "default.rgw.intent-log",
    "usage_log_pool": "default.rgw.usage",
    "user_keys_pool": "default.rgw.users.keys",
    "user_email_pool": "default.rgw.users.email",
    "user_swift_pool": "default.rgw.users.swift",
    "user_uid_pool": "default.rgw.users.uid",
    "system_key": {
        "access_key": "",
        "secret_key": ""
    },
    "placement_pools": [
        {
            "key": "default-placement",
            "val": {
                "index_pool": "sce-pvecl01.rgw.buckets.index",
                "data_pool": "sce-pvecl01.rgw.buckets.data",
                "data_extra_pool": "default.rgw.buckets.non-ec",
                "index_type": 0
            }
        }
    ],
    "realm_id": "4a367028-bd8a-4c4f-a10d-29b40513f971"
}
```
And then apply this new configuration using:

```bash
radosgw-admin zone set --infile=zone.json
```
Make sure to take a backup of your current configuration before making any changes and make sure to understand the implications of these changes.
