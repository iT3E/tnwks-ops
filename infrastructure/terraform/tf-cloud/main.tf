
## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "tfe" {
  token = data.sops_file.secrets.data["tfe_token"]
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
  name              = "tnwks-ops-aws-init"
  organization      = tfe_organization.tnwks-ops.name
  execution_mode    = "local"
  working_directory = "tnwks-ops/infrastructure/terraform/aws/init"
}

resource "tfe_workspace_settings" "tnwks-ops-aws-init_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-init.id
  execution_mode = "local"
}

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name              = "tnwks-ops-aws-identity"
  organization      = tfe_organization.tnwks-ops.name
  execution_mode    = "remote"
  working_directory = "tnwks-ops/infrastructure/terraform/aws/accounts/identity"
  vcs_repo {
    identifier     = "github/it3E/tnwks-ops"
    oauth_token_id = tfe_oauth_client.tfe-oauth-github.id
  }
}

resource "tfe_run_trigger" "run_trigger_aws_identity" {
  workspace_id  = tfe_workspace.tnwks-ops-aws-identity
  sourceable_id = tfe_workspace.tnwks-ops-aws-init
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name              = "tnwks-ops-aws-prod"
  organization      = tfe_organization.tnwks-ops.name
  execution_mode    = "remote"
  working_directory = "tnwks-ops/infrastructure/terraform/aws/accounts/prod"
  vcs_repo {
    identifier     = "github/it3E/tnwks-ops"
    oauth_token_id = tfe_oauth_client.tfe-oauth-github.id
  }
}

resource "tfe_run_trigger" "run_trigger_aws_identity" {
  workspace_id  = tfe_workspace.tnwks-ops-aws-prod
  sourceable_id = tfe_workspace.tnwks-ops-aws-identity
}
