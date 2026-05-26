
## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers. https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_accounts_access.html#orgs_manage_accounts_access-cross-account-role
## "The scope of access for this role includes all principals in the management account,"
## ---------------------------------------------------------------------------------------------------------------------
provider "aws" {
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}

# Cognito custom domains require the ACM cert to be in us-east-1 regardless
# of where the user pool actually lives (ours is us-west-2). Aliased provider
# scoped to us-east-1 just for that ACM resource.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
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
    #look for PII leak here
    workspaces {
      name = "tnwks-ops-aws-prod"
    }
  }
  required_version = ">= 1.2.2"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Contains any data blocks that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

## ---------------------------------------------------------------------------------------------------------------------
## SES
## Contains configuration for AWS SES.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_ses_domain_identity" "ses_domain_identity" {
  domain = data.sops_file.secrets.data["ses_domain"]
}

resource "aws_ses_domain_dkim" "ses_domain_dkim" {
  domain = aws_ses_domain_identity.ses_domain_identity.domain
}

resource "aws_iam_user" "smtp_user" {
  name = "smtp_user"
}

resource "aws_iam_access_key" "smtp_user" {
  user = aws_iam_user.smtp_user.name
}

data "aws_iam_policy_document" "ses_sender" {
  statement {
    actions   = ["ses:SendRawEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "ses_sender" {
  name        = "ses_sender"
  description = "Allows sending of e-mails via Simple Email Service"
  policy      = data.aws_iam_policy_document.ses_sender.json
}

resource "aws_iam_user_policy_attachment" "test-attach" {
  user       = aws_iam_user.smtp_user.name
  policy_arn = aws_iam_policy.ses_sender.arn
}

output "smtp_username" {
  value = aws_iam_access_key.smtp_user.id
}

output "smtp_password" {
  value     = aws_iam_access_key.smtp_user.ses_smtp_password_v4
  sensitive = true
}

