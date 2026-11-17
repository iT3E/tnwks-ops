## ---------------------------------------------------------------------------------------------------------------------
## S3: HOMELAB BACKUPS
## Off-site target for the homelab-wsl cluster's backups (docs/operations/backups.md):
##   cnpg/    CloudNativePG base backups + WAL, written by the barman-cloud plugin
##   kopiur/  Kopia repository for PVC snapshots, written by kopiur
## Both tools encrypt or compress client-side and manage their own retention.
## Writers authenticate through Roles Anywhere (rolesanywhere.tf), with no IAM
## user. The bucket adds versioning so a leaked or misused session can't destroy history:
## deleted or overwritten objects stay recoverable for 30 days.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_s3_bucket" "homelab_backups" {
  bucket = "tnwks-homelab-backups-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_ownership_controls" "homelab_backups" {
  bucket = aws_s3_bucket.homelab_backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "homelab_backups" {
  bucket                  = aws_s3_bucket.homelab_backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "homelab_backups" {
  bucket = aws_s3_bucket.homelab_backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "homelab_backups" {
  bucket = aws_s3_bucket.homelab_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "homelab_backups" {
  bucket = aws_s3_bucket.homelab_backups.id

  # Barman and kopia prune their own data; this only expires what they
  # deleted or overwrote, after the 30-day undo window.
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    expiration {
      expired_object_delete_marker = true
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.homelab_backups]
}

## ---------------------------------------------------------------------------------------------------------------------
## An exclusive prefix (rolesanywhere_backups_prefixed, exclusive = true): the bucket policy denies its objects, and a
## list under it, to every principal but its own role and the break-glass roles. So the shared backup role and the site
## roles, which read the whole bucket, no longer read it. The deny does not cover the bucket policy itself: the
## Terraform role, and the root user, can still change or remove it, which is the way back from a lock-out.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  backups_exclusive_prefixes = {
    for k, v in local.rolesanywhere_backups_prefixed : k => v if v.exclusive
  }
}

data "aws_iam_policy_document" "homelab_backups_bucket" {
  count = length(local.backups_exclusive_prefixes) > 0 ? 1 : 0

  dynamic "statement" {
    for_each = local.backups_exclusive_prefixes
    content {
      sid       = "DenyObjectsOutside${replace(title(replace(statement.key, "-", " ")), " ", "")}"
      effect    = "Deny"
      actions   = ["s3:*"]
      resources = ["${aws_s3_bucket.homelab_backups.arn}/${statement.value.prefix}*"]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = concat([aws_iam_role.rolesanywhere_backups_prefixed[statement.key].arn], var.backups_break_glass_role_arn_patterns)
      }
    }
  }

  # A list with no prefix, or with a prefix shorter than the exclusive prefix
  # without its final / (for kopiur-finance/, a prefix such as "kopiur" or
  # "kopiur-fin"), still shows the key names under it (content-addressed names
  # of Kopia, no data); a list with a prefix that starts with that name is
  # denied. Not
  # s3:ListBucketMultipartUploads: it does not take the s3:prefix condition key,
  # and S3 refuses the whole policy with it (MalformedPolicy). It shows only the
  # key names of uploads in progress; their parts are objects, denied above.
  dynamic "statement" {
    for_each = local.backups_exclusive_prefixes
    content {
      sid       = "DenyListOutside${replace(title(replace(statement.key, "-", " ")), " ", "")}"
      effect    = "Deny"
      actions   = ["s3:ListBucket", "s3:ListBucketVersions"]
      resources = [aws_s3_bucket.homelab_backups.arn]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["${trimsuffix(statement.value.prefix, "/")}*"]
      }
      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = concat([aws_iam_role.rolesanywhere_backups_prefixed[statement.key].arn], var.backups_break_glass_role_arn_patterns)
      }
    }
  }
}

resource "aws_s3_bucket_policy" "homelab_backups" {
  count = length(local.backups_exclusive_prefixes) > 0 ? 1 : 0

  bucket = aws_s3_bucket.homelab_backups.id
  policy = data.aws_iam_policy_document.homelab_backups_bucket[0].json

  lifecycle {
    precondition {
      condition     = length(var.backups_break_glass_role_arn_patterns) > 0
      error_message = "An exclusive backup prefix needs at least one break-glass role (backups_break_glass_role_arn_patterns)."
    }
  }

  depends_on = [aws_s3_bucket_public_access_block.homelab_backups]
}
