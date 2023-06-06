
Since neither Packer nor HCL in general have SOPS functionality, you must first run the below commands locally to get the secrets in environment variables.

```
export PROXMOX_API_URL=$(sops -d packer/proxmox/vyos/secretvars.sops.yaml | yq eval '.proxmox_url' -)
export PROXMOX_API_TOKEN_ID=$(sops -d packer/proxmox/vyos/secretvars.sops.yaml | yq eval '.proxmox_api_token_id' -)
export PROXMOX_API_TOKEN_SECRET=$(sops -d packer/proxmox/vyos/secretvars.sops.yaml | yq eval '.proxmox_api_token_secret' -)
export SSH_SECONDARY_USERNAME=$(sops -d packer/proxmox/vyos/secretvars.sops.yaml | yq eval '.ssh_secondary_username' -)
export SSH_SECONDARY_VYOSPUB=$(sops -d packer/proxmox/vyos/secretvars.sops.yaml | yq eval '.ssh_secondary_vyospub' -)
```

A port forward rule is configured on PFSense to forward 8802 traffic destined for 172.22.0.0/16 to the windows PC.

I have used [Wsl-IpHandler](https://github.com/wikiped/Wsl-IpHandler) to set a static IP on the WSL instance.  Currently using the 'dynamic' mode.

Run below command in Powershell to open Windows PC firewall
```
netsh advfirewall firewall add rule name="Packer HTTP Server" dir=in action=allow protocol=TCP localport=8802
```

to open a port proxy on Windows PC
```
netsh interface portproxy add v4tov4 listenport=8802 listenaddress=0.0.0.0 connectport=8802 connectaddress=172.22.96.2
```

VyOS really hates when you try to delete a login user that is actively being used.  Unable to resolve with the (too much) time i've already spent, so I'm taking it out in favor of adding it with the rest of the git config files and considering it another step of the gitops process.
