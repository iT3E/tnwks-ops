## Execute
```
cd terraform/environments/production/pve
terraform plan
terraform apply -auto-approve
```

## Caveats
Currently the caveats to the PVE terraform config are:

## `agent = 1` and `agent = 0`
both options are failing currently and is fixed by disabling the option manually via gui.  The root cause being that the packer image has the qemu agent enabled, but terraform is unable to disable it if doing `agent = 0`.  If `agent = 1` is used, then terraform will fail due to no network connectivity.

So either disable the agent manually via GUI, or don't put a VM in a vlan that doesn't have DHCP enabled.


