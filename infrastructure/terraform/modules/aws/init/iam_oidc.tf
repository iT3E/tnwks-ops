## ---------------------------------------------------------------------------------------------------------------------
## IAM OIDC PROVIDER
## Trusts Terraform Cloud so workspaces can assume a role with dynamic
## credentials instead of static keys.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "tfc_provider" {
  url             = data.tls_certificate.tfc_certificate.url
  client_id_list  = ["aws.workload.identity"]
  thumbprint_list = [data.tls_certificate.tfc_certificate.certificates[0].sha1_fingerprint]
}
