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
          "organization:tnwks-ops:project:${tfe_project.tfe_project_aws.name}:workspace:tnwks-ops-aws-identity:run_phase:*",
          "organization:tnwks-ops:project:${tfe_project.tfe_project_aws.name}:workspace:tnwks-ops-aws-prod:run_phase:*"
         ]
       }
     }
   }
 ]
}
EOF
}
