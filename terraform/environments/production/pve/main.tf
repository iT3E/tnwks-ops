terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    #look for PII leak here
    workspaces {
      name = "tnwks-pve-prod"
    }
  }
    required_version = ">= 1.2.2"
   required_providers {
     proxmox = {
       source = "Telmate/proxmox"
       version = ">= 2.9.10"
   }
     sops = {
       source  = "carlpett/sops"
       version = "0.7.2"
   }
 }
}

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}
