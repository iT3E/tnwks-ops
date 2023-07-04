resource "aws_kms_key" "key" {
  description             = var.key_description
  policy                  = var.key_policy
}
