resource "aws_iam_policy" "policy" {
  name        = var.policy_name
  path        = "/"
  description = var.policy_description
  policy      = var.policy_json
}
