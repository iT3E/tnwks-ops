## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES ANYWHERE
## X.509 workstation credentials for SOPS. A certificate from the repo's own CA
## (no AWS Private CA, so no added cost) gets a short session as a role whose
## only permission is to assume the management account's iam-role-sops, which
## holds the KMS grant. A leaked certificate therefore reaches SOPS decryption
## and nothing else, and never authenticates into the management account
## directly. Disabled when rolesanywhere_ca_pem is null.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  rolesanywhere_enabled = var.rolesanywhere_ca_pem != null
}

resource "aws_rolesanywhere_trust_anchor" "tnwks" {
  count = local.rolesanywhere_enabled ? 1 : 0

  name    = "tnwks-ops-ca"
  enabled = true

  source {
    source_type = "CERTIFICATE_BUNDLE"
    source_data {
      x509_certificate_data = var.rolesanywhere_ca_pem
    }
  }
}

data "aws_iam_policy_document" "rolesanywhere_sops_trust" {
  count = local.rolesanywhere_enabled ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }
    # Only certificates presented through this trust anchor...
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_rolesanywhere_trust_anchor.tnwks[0].arn]
    }
    # ...and only the allowlisted certificate subjects.
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = var.rolesanywhere_sops_common_names
    }
  }
}

resource "aws_iam_role" "rolesanywhere_sops" {
  count = local.rolesanywhere_enabled ? 1 : 0

  name                 = "tnwks-rolesanywhere-sops"
  description          = "Roles Anywhere workstation role: may only assume iam-role-sops for SOPS KMS access"
  assume_role_policy   = data.aws_iam_policy_document.rolesanywhere_sops_trust[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "rolesanywhere_sops" {
  count = local.rolesanywhere_enabled ? 1 : 0

  # Roles Anywhere sessions carry a source identity and the certificate's
  # session tags, both of which role chaining passes on, so the hop also needs
  # sts:SetSourceIdentity and sts:TagSession.
  statement {
    effect    = "Allow"
    actions   = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"]
    resources = [var.sops_role_arn]
  }
}

resource "aws_iam_role_policy" "rolesanywhere_sops" {
  count = local.rolesanywhere_enabled ? 1 : 0

  name   = "assume-sops-role"
  role   = aws_iam_role.rolesanywhere_sops[0].id
  policy = data.aws_iam_policy_document.rolesanywhere_sops[0].json
}

resource "aws_rolesanywhere_profile" "sops" {
  count = local.rolesanywhere_enabled ? 1 : 0

  name             = "tnwks-ops-sops"
  enabled          = true
  role_arns        = [aws_iam_role.rolesanywhere_sops[0].arn]
  duration_seconds = 3600
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES ANYWHERE: HOMELAB BACKUPS
## Same trust anchor and CA, separate role and profile: a certificate whose CN is
## in rolesanywhere_backups_common_names gets a short session that can read and
## write the homelab backup bucket (s3_backups.tf) and nothing else. On the
## cluster, a CronJob turns the certificate into STS credentials for barman-cloud
## and kopiur (docs/operations/backups.md). No IAM user, no long-lived key.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  rolesanywhere_backups_enabled = local.rolesanywhere_enabled && length(var.rolesanywhere_backups_common_names) > 0
}

data "aws_iam_policy_document" "rolesanywhere_backups_trust" {
  count = local.rolesanywhere_backups_enabled ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_rolesanywhere_trust_anchor.tnwks[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = var.rolesanywhere_backups_common_names
    }
  }
}

resource "aws_iam_role" "rolesanywhere_backups" {
  count = local.rolesanywhere_backups_enabled ? 1 : 0

  name                 = "tnwks-rolesanywhere-backups"
  description          = "Roles Anywhere role for homelab backups: read/write on the backup bucket only"
  assume_role_policy   = data.aws_iam_policy_document.rolesanywhere_backups_trust[0].json
  max_session_duration = 14400
}

resource "aws_iam_role_policy" "rolesanywhere_backups" {
  count = local.rolesanywhere_backups_enabled ? 1 : 0

  name   = "homelab-backups-bucket"
  role   = aws_iam_role.rolesanywhere_backups[0].id
  policy = data.aws_iam_policy_document.homelab_backups.json
}

# 4-hour sessions, refreshed every 30 minutes on the cluster, so a refresher
# outage of a few hours doesn't interrupt WAL archiving.
resource "aws_rolesanywhere_profile" "backups" {
  count = local.rolesanywhere_backups_enabled ? 1 : 0

  name             = "tnwks-ops-backups"
  enabled          = true
  role_arns        = [aws_iam_role.rolesanywhere_backups[0].arn]
  duration_seconds = 14400
}

## ---------------------------------------------------------------------------------------------------------------------
## Backups for other sites (rolesanywhere_backups_sites): same trust anchor, one role and profile per site. A site
## reads the whole bucket (so it can restore or replay the other site's backups) but writes, overwrites and deletes
## only under its own prefixes: homelab-wsl-02 can't touch homelab-wsl's archives, and a standby promoted by mistake
## can't damage them. homelab-wsl keeps the role above (read/write on everything).
## ---------------------------------------------------------------------------------------------------------------------

locals {
  rolesanywhere_backups_sites = local.rolesanywhere_enabled ? var.rolesanywhere_backups_sites : {}
}

data "aws_iam_policy_document" "rolesanywhere_backups_site_trust" {
  for_each = local.rolesanywhere_backups_sites

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_rolesanywhere_trust_anchor.tnwks[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = each.value.common_names
    }
  }
}

data "aws_iam_policy_document" "rolesanywhere_backups_site" {
  for_each = local.rolesanywhere_backups_sites

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.homelab_backups.arn]
  }
  statement {
    sid       = "ReadEverything"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.homelab_backups.arn}/*"]
  }
  statement {
    sid = "WriteOwnPrefixes"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [for p in each.value.write_prefixes : "${aws_s3_bucket.homelab_backups.arn}/${p}"]
  }
}

resource "aws_iam_role" "rolesanywhere_backups_site" {
  for_each = local.rolesanywhere_backups_sites

  name                 = "tnwks-rolesanywhere-backups-${each.key}"
  description          = "Roles Anywhere role for ${each.key} backups: read the backup bucket, write its own prefixes"
  assume_role_policy   = data.aws_iam_policy_document.rolesanywhere_backups_site_trust[each.key].json
  max_session_duration = 14400
}

resource "aws_iam_role_policy" "rolesanywhere_backups_site" {
  for_each = local.rolesanywhere_backups_sites

  name   = "homelab-backups-bucket-${each.key}"
  role   = aws_iam_role.rolesanywhere_backups_site[each.key].id
  policy = data.aws_iam_policy_document.rolesanywhere_backups_site[each.key].json
}

# Same 4-hour sessions as the homelab-wsl profile.
resource "aws_rolesanywhere_profile" "backups_site" {
  for_each = local.rolesanywhere_backups_sites

  name             = "tnwks-ops-backups-${each.key}"
  enabled          = true
  role_arns        = [aws_iam_role.rolesanywhere_backups_site[each.key].arn]
  duration_seconds = 14400
}
