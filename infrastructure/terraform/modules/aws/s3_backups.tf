## ---------------------------------------------------------------------------------------------------------------------
## S3: HOMELAB BACKUPS
## Off-site target for the homelab-wsl cluster's backups (docs/operations/backups.md):
##   cnpg/    CloudNativePG base backups + WAL, written by the barman-cloud plugin
##   kopiur/  Kopia repository for PVC snapshots, written by kopiur
## Both tools encrypt or compress client-side and manage their own retention.
## The bucket adds versioning so a leaked or misused key can't destroy history:
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
