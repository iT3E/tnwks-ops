# How to flash HP NICs with Mellanox firmware
[Reference from ServeTheHome](https://forums.servethehome.com/index.php?threads/flashing-stock-mellanox-firmware-to-oem-emc-connectx-3-ib-ethernet-dual-port-qsfp-adapter.20525/#post-198015)

```
##Download latest Mellanox Firmware tools and install them  + dependencies:

su -
sed -i 's|^deb https://enterprise.proxmox.com/debian/pve bullseye pve-enterprise|#&|' /etc/apt/sources.list.d/pve-enterprise.list
echo "deb http://download.proxmox.com/debian/pve bullseye pve-no-subscription" >> /etc/apt/sources.list
apt update && apt install -y gcc make dkms unzip pve-headers-$(uname -r)
wget https://www.mellanox.com/downloads/MFT/mft-4.24.0-72-x86_64-deb.tgz
tar -xvf mft-4.24.0-72-x86_64-deb.tgz && cd mft-4.24.0-72-x86_64-deb
./install.sh

mst start
mst status
#copy the dev address with cr0 in it, like: /dev/mst/mt4099_pci_cr0
#Use that address in the following commands

wget http://www.mellanox.com/downloads/firmware/fw-ConnectX3-rel-2_42_5000-MCX354A-FCB_A2-A5-FlexBoot-3.4.752.bin.zip
unzip fw-ConnectX3-rel-2_42_5000-MCX354A-FCB_A2-A5-FlexBoot-3.4.752.bin.zip

#Backup the cards current board definition file just in case
flint -d /dev/mst/mt4099_pci_cr0 dc orig_firmware.ini

flint -d /dev/mst/mt4099_pci_cr0 -i fw-ConnectX3-rel-2_42_5000-MCX354A-FCB_A2-A5-FlexBoot-3.4.752.bin -allow_psid_change burn

#reboot

su -
mst start

#get device ID again
mst status

#use it to see the cards current configuration:
mlxconfig -d /dev/mst/mt4099_pci_cr0 query

#turn both ports from VPI/Auto to Ethernet only:
mlxconfig -d /dev/mst/mt4099_pci_cr0 set LINK_TYPE_P1=2 LINK_TYPE_P2=2

#turn off bootrom crap
mlxconfig -d /dev/mst/mt4099_pci_cr0 set BOOT_OPTION_ROM_EN_P1=false
mlxconfig -d /dev/mst/mt4099_pci_cr0 set BOOT_OPTION_ROM_EN_P2=false
mlxconfig -d /dev/mst/mt4099_pci_cr0 set LEGACY_BOOT_PROTOCOL_P1=0
mlxconfig -d /dev/mst/mt4099_pci_cr0 set LEGACY_BOOT_PROTOCOL_P2=0

##optional: delete bootrom off the card, so it doesn't slow down boot by popping up crap
##this is safe to do and supported by mellanox
flint -d /dev/mst/mt4099_pci_cr0 --allow_rom_change drom


#add network config:
apt install openvswitch-switch -y
nano /etc/network/interfaces


auto ens1
iface ens1 inet manual
    ovs_type OVSPort
    ovs_bridge vmbr1
    ovs_options other_config:rstp-enable=true other_config:rstp-path-cost=150 other_config:rstp-port-admin-edge=false other_config:rstp-port-auto-edge=false other_config:rstp-port-mcheck=true vlan_mode=native-untagged
	ovs_mtu 9000

auto ens1d1
iface ens1d1 inet manual
    ovs_type OVSPort
    ovs_bridge vmbr1
    ovs_options other_config:rstp-enable=true other_config:rstp-path-cost=150 other_config:rstp-port-admin-edge=false other_config:rstp-port-auto-edge=false other_config:rstp-port-mcheck=true vlan_mode=native-untagged
	ovs_mtu 9000

auto vmbr1
iface vmbr1 inet static
    address 10.15.15.50/24
    ovs_type OVSBridge
    ovs_ports ens1d1 ens1
	ovs_mtu 9000
    up ovs-vsctl set Bridge ${IFACE} rstp_enable=true other_config:rstp-priority=32768 other_config:rstp-forward-delay=4 other_config:rstp-max-age=6
    post-up sleep 10
```
