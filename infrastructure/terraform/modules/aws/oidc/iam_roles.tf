## ---------------------------------------------------------------------------------------------------------------------
## LOCALS
## TFC identifiers are rendered into the trust policy as plain strings, so
## this module needs no reference to the TFC resources themselves.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  tfc_subs = [
    for ws in var.tfc_workspace_names :
    "organization:${var.tfc_organization}:project:${var.tfc_project_name}:workspace:${ws}:run_phase:*"
  ]
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES
## Role assumed by TFC workspaces via the OIDC provider above.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_role" "tfc_oidc_role" {
  name = "tfc-oidc-role"
  inline_policy {
    name = "my_inline_policy"

    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Action   = ["*"]
          Effect   = "Allow"
          Resource = "*"
        },
      ]
    })
  }

  assume_role_policy = <<EOF
{
 "Version": "2012-10-17",
 "Statement": [
   {
     "Effect": "Allow",
     "Principal": {
       "Federated": "${aws_iam_openid_connect_provider.tfc_provider.arn}"
     },
     "Action": "sts:AssumeRoleWithWebIdentity",
     "Condition": {
       "StringEquals": {
         "app.terraform.io:aud": "${one(aws_iam_openid_connect_provider.tfc_provider.client_id_list)}"
       },
       "StringLike": {
         "app.terraform.io:sub": [
          ${join(",\n          ", [for s in local.tfc_subs : jsonencode(s)])}
         ]
       }
     }
   }
 ]
}
EOF
}
