# ceph troubleshooting:
### shows general ceph cluster detail. will show when pg's are broken.

```
ceph health detail
```

### shows PG placement per OSD [1,5,4]p1 means the pg is on 3 disks, osd1, osd5, and osd4, with osd1 being the 'primary'.

```
ceph pg ls-by-pool pool-hdd-hostfd
```
### shows OSD mapping on all nodes.
```
ceph osd tree
```

### shows recovery detail
```
ceph -s
```

# How to mount rbd pvc's


### postgres-2:
```
sudo rbd map ssd-pool/csi-vol-c278a428-263c-4ae3-bf70-845878d83e79
sudo mkdir /mnt/rbd-postgres

sudo mount /dev/rbd0 /mnt/rbd-postgres
```

### postgres-1:
```
sudo rbd map ssd-pool/csi-vol-bfff4923-c872-4037-bc72-572a767adfb8
sudo mkdir /mnt/rbd-postgres-1
sudo mount /dev/rbd1 /mnt/rbd-postgres-1

sudo mkdir /mnt/pve/cephfs/postgresbak_20230729/postgres-1
sudo cp -r /mnt/rbd-postgres-1/* /mnt/pve/cephfs/postgresbak_20230729/postgres-1
```

### postgres-3:
```
sudo rbd map ssd-pool/csi-vol-d9a95077-cd96-44f5-9285-bbd52b32cf89
sudo mkdir /mnt/rbd-postgres-3
sudo mount /dev/rbd2 /mnt/rbd-postgres-3

sudo mkdir /mnt/pve/cephfs/postgresbak_20230729/postgres-3
sudo cp -r /mnt/rbd-postgres-3/* /mnt/pve/cephfs/postgresbak_20230729/postgres-3

sudo umount /dev/rbd0
sudo umount /dev/rbd1
sudo umount /dev/rbd2

sudo rbd unmap ssd-pool/csi-vol-c278a428-263c-4ae3-bf70-845878d83e79
sudo rbd unmap ssd-pool/csi-vol-bfff4923-c872-4037-bc72-572a767adfb8
sudo rbd unmap ssd-pool/csi-vol-d9a95077-cd96-44f5-9285-bbd52b32cf89
```


-------------------------

### postgres-2:
```
sudo rbd map ssd-pool/csi-vol-c278a428-263c-4ae3-bf70-845878d83e79
sudo mount /dev/rbd0 /mnt/rbd-postgres
```

### postgres-1:
```
sudo rbd map ssd-pool/csi-vol-bfff4923-c872-4037-bc72-572a767adfb8
sudo mount /dev/rbd1 /mnt/rbd-postgres-1


sudo cp -r /mnt/rbd-postgres/* /mnt/rbd-postgres-1/

sudo umount /dev/rbd0
sudo umount /dev/rbd1

sudo rbd unmap ssd-pool/csi-vol-c278a428-263c-4ae3-bf70-845878d83e79
sudo rbd unmap ssd-pool/csi-vol-bfff4923-c872-4037-bc72-572a767adfb8
sudo rbd unmap ssd-pool/csi-vol-d9a95077-cd96-44f5-9285-bbd52b32cf89
```


------------------------

### postgres-1(new):
```
sudo rbd map ssd-pool/csi-vol-705f1c6a-c52d-4a75-94e5-2c3f1a38e731
sudo mkdir /mnt/rbd-postgres-1-new
sudo mount /dev/rbd0 /mnt/rbd-postgres-1-new

sudo cp -r /mnt/pve/cephfs/postgresbak_20230729/postgres-2/* /mnt/rbd-postgres-1-new/

sudo umount /dev/rbd0
sudo rbd unmap ssd-pool/csi-vol-705f1c6a-c52d-4a75-94e5-2c3f1a38e731
```

