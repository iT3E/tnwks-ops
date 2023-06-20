resource "aws_kms_key" "key" {
  description             = var.description
  deletion_window_in_days = var.deletion_window_in_days
  enable_key_rotation     = var.enable_key_rotation

  policy = jsonencode({
    Version = "2012-10-17",
    Id = var.policy_id,
    Statement = [
      {
        Sid = "EnableIAMUserPermissions",
        Effect = "Allow",
        Principal = {
          AWS = var.principal
        },
        Action = "kms:*",
        Resource = "*"
      },
    ]
  })
}
