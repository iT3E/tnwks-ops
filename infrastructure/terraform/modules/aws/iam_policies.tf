## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICIES
## Policy documents and managed policies.
## ---------------------------------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "ses_sender" {
  statement {
    actions   = ["ses:SendRawEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "ses_sender" {
  name        = "ses_sender"
  description = "Allows sending of e-mails via Simple Email Service"
  policy      = data.aws_iam_policy_document.ses_sender.json
}

# Read/write on the homelab backup bucket only: the backup tools on the
# homelab-wsl cluster (barman-cloud, kopiur). No bucket-level admin, so the key
# can't change versioning or the lifecycle rules that protect history.
data "aws_iam_policy_document" "homelab_backups" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.homelab_backups.arn]
  }
  statement {
    sid = "Objects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.homelab_backups.arn}/*"]
  }
}

resource "aws_iam_policy" "homelab_backups" {
  name        = "homelab_backups"
  description = "Read/write objects in the homelab backup bucket"
  policy      = data.aws_iam_policy_document.homelab_backups.json
}
