# Archived Infrastructure

This directory contains infrastructure-as-code that is no longer actively used
but preserved for reference.

## Contents

### `terraform-pve/`
Terraform modules for provisioning VMs on the Proxmox Virtual Environment (PVE)
cluster. Superseded by bare-metal Talos Linux on MS-01 hardware.

### `packer-proxmox/`
Packer templates for building VM images (Ubuntu, VyOS) on Proxmox.
No longer needed — Talos uses its own installer image.

## Why archived (not deleted)

- Preserves git history without cluttering the active tree
- Reference material if similar patterns are needed elsewhere
- Recovery path if migration needs to be rolled back (see `git tag legacy-pve`)

## Migration date

2026-05-12 (branch: restructure/multi-cluster)
