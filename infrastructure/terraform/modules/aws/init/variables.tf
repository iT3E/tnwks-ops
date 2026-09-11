## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Supplied by the calling environment. Secrets are decrypted from SOPS in the
## environment root and passed in.
## ---------------------------------------------------------------------------------------------------------------------

variable "aws_account_prod_email" {
  description = "Root email for the prod AWS account, published as a TFC variable"
  type        = string
  sensitive   = true
}
