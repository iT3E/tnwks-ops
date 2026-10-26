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
