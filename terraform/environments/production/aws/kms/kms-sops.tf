
module "kms_sops" {
  source = "/modules/aws/kms"
  key_description = "KMS Key for SOPS"
  key_policy      = <<POLICY
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "EnableIAMUserPermissions",
        "Effect": "Allow",
        "Principal": {
          "AWS": "${module.iam_role.iam_sops_role.role_arn}"
        },
        "Action": "kms:*",
        "Resource": "*"
      }
    ]
  }
POLICY
}

