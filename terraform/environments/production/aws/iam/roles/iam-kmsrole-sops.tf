module "iam_sops_role" {
  source             = "modules/aws/iam/roles"
  name               = "sops_role"
  assume_role_policy = <<POLICY
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "AWS": "${module.iam_role.itadmin.role_arn}"
        },
        "Action": "sts:assume",
        "Resource": "*"
      }
    ]
  }
POLICY
}
