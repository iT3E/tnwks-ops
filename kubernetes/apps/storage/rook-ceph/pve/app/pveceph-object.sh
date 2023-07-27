#!/bin/bash
pveceph pool create ".rgw.root" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 4 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.control" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 32 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.meta" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 32 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.log" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 4 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.otp" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 4 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.buckets.index" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 32 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.buckets.non-ec" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 32 --crush_rule hdd-hostfd
pveceph pool create "sce-pvecl01.rgw.buckets.data" --add_storages 0 --application rgw --size 3 --min_size 2 --pg_autoscale_mode on --pg_num 64 --crush_rule hdd-hostfd
