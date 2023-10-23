
    # - `tnwks-ops-aws-init`
    #   - point to /infrastructure/terraform/aws/init
    #   - CLI run
    # - `tnwks-ops-aws-identity`
    #   - point to /infrastructure/terraform/aws/accounts/identity
    #   - VCS run
    #   - Run trigger on tnwks-ops-aws-init
    # - `tnwks-ops-aws-prod`
    #   - point to /infrastructure/terraform/aws/accounts/prod
    #   - VCS run
    #   - Run trigger on tnwks-ops-aws-identity

## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "tfe" {
  token                       = data.sops_file.secrets.data["tfe_token"]
}

## ---------------------------------------------------------------------------------------------------------------------
## TERRAFORM
## Contains the configuration for Terraform Cloud, along with the required providers.
##
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-ops-init"
    }
  }
    required_version = "~> 1.2.0"
   required_providers {
     aws = {
       source  = "hashicorp/aws"
       version = "~> 5.0"
     }
     sops = {
       source  = "carlpett/sops"
       version = "~> 1.0.0"
   }
 }
}
## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Contains any data blocks that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}



## ---------------------------------------------------------------------------------------------------------------------
## TF ORGANIZATION
## Contains the Terraform Organization and related configuration.
##
## ---------------------------------------------------------------------------------------------------------------------


import {
  to = tfe_organization.tnwks-ops
  id = "tnwks-ops"
}

resource "tfe_organization" "tnwks-ops" {
  name  = "tnwks-ops"
  email = data.sops_file.secrets.data["tfe_org_email"]
}


## ---------------------------------------------------------------------------------------------------------------------
## TF WORKSPACES - INIT
## Contains all Terraform Workspaces used to initialize Terraform. Manually created TF Org and Init workspace are imported.
##
## ---------------------------------------------------------------------------------------------------------------------

import {
  to = tfe_workspace.tnwks-ops-init
  id = "tnwks-ops"
}

resource "tfe_workspace" "tnwks-ops-init" {
  name           = "tnwks-ops-init"
  organization   = tfe_organization.tnwks-ops.name
  execution_mode = "local"
}


## ---------------------------------------------------------------------------------------------------------------------
## TF OAUTH
## Contains OAUTH configuration for connecting Terraform to VCS.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_oauth_client" "tfe-oauth-github" {
  name             = "tfe-oauth-github"
  organization     = tfe_organization.tnwks-ops.name
  api_url          = "https://api.github.com"
  http_url         = "https://github.com"
  oauth_token      = data.sops_file.secrets.data["tfe_oauth_github_token"]
  service_provider = "github"
}

## ---------------------------------------------------------------------------------------------------------------------
## TF WORKSPACES - AWS
## Contains all Terraform Workspaces for use with AWS.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_workspace" "tnwks-ops-aws-init" {
  name           = "tnwks-ops-aws-init"
  organization   = tfe_organization.tnwks-ops.name
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name           = "tnwks-ops-aws-identity"
  organization   = tfe_organization.tnwks-ops.name
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name           = "tnwks-ops-aws-prod"
  organization   = tfe_organization.tnwks-ops.name
  execution_mode = "remote"
  vcs_repo {
    identifier = "github/it3E/tnwks-ops"
    oauth_token_id = tfe_oauth_client.tfe-oauth-github.id
  }
}
