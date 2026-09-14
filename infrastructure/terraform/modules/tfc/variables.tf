## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Supplied by the calling environment. Secrets are decrypted from SOPS in the
## environment root and passed in.
## ---------------------------------------------------------------------------------------------------------------------

variable "tfc_organization" {
  description = "Terraform Cloud organization name"
  type        = string
}

variable "project_name" {
  description = "Terraform Cloud project grouping the AWS workspaces"
  type        = string
}

variable "aws_oidc_role_arn" {
  description = "ARN of the AWS role workspaces assume via OIDC, from modules/aws/oidc"
  type        = string
}

variable "aws_account_prod_email" {
  description = "Root email for the prod AWS account, published as a TFC variable"
  type        = string
  sensitive   = true
}
