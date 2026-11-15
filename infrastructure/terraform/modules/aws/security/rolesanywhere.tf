## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES ANYWHERE: THE TWO READERS OF THE SECURITY PLATFORM
## Same trust anchor and CA as the backups (modules/aws/rolesanywhere.tf), in
## the same form: one role and one profile for each reader, and each role
## trusts the certificate CNs of its own list. On homelab-wsl-02 a CronJob
## turns each certificate into a 4-hour session (kubernetes/apps/security).
##
##   audit  Prowler: read-only on the prod account, and it may assume the audit
##          role of the management account (iam_roles.tf)
##   trail  the trail copy job: read and list on the trail bucket only
##
## Removing a CN from its list and applying ends that certificate's access at
## once. An empty list removes the role and its profile.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  audit_enabled = length(var.rolesanywhere_audit_common_names) > 0
  trail_enabled = length(var.rolesanywhere_trail_common_names) > 0

  audit_role_name = "tnwks-rolesanywhere-security-audit"
  # Built here, so that the audit role of the management account can name it
  # in its trust policy whether or not the role exists.
  audit_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.audit_role_name}"
}

## The audit role (Prowler) -------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "rolesanywhere_audit_trust" {
  count = local.audit_enabled ? 1 : 0

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
      values   = [var.rolesanywhere_trust_anchor_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = var.rolesanywhere_audit_common_names
    }
  }
}

resource "aws_iam_role" "rolesanywhere_audit" {
  count = local.audit_enabled ? 1 : 0

  name                 = local.audit_role_name
  description          = "Roles Anywhere role for the security audit (Prowler): read-only on this account"
  assume_role_policy   = data.aws_iam_policy_document.rolesanywhere_audit_trust[0].json
  max_session_duration = 14400
}

resource "aws_iam_role_policy_attachment" "rolesanywhere_audit" {
  for_each = { for name, arn in local.audit_managed_policy_arns : name => arn if local.audit_enabled }

  role       = aws_iam_role.rolesanywhere_audit[0].name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "rolesanywhere_audit_additions" {
  count = local.audit_enabled ? 1 : 0

  name   = "audit-additions"
  role   = aws_iam_role.rolesanywhere_audit[0].id
  policy = data.aws_iam_policy_document.audit_additions.json
}

# The hop to the management account. A Roles Anywhere session carries a source
# identity and the certificate's session tags into the next role, so the hop
# needs sts:SetSourceIdentity and sts:TagSession too.
data "aws_iam_policy_document" "rolesanywhere_audit_assume" {
  statement {
    effect    = "Allow"
    actions   = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"]
    resources = [aws_iam_role.audit_management.arn]
  }
}

resource "aws_iam_role_policy" "rolesanywhere_audit_assume" {
  count = local.audit_enabled ? 1 : 0

  name   = "assume-management-audit-role"
  role   = aws_iam_role.rolesanywhere_audit[0].id
  policy = data.aws_iam_policy_document.rolesanywhere_audit_assume.json
}

# 4-hour sessions, like the backups.
resource "aws_rolesanywhere_profile" "audit" {
  count = local.audit_enabled ? 1 : 0

  name             = "tnwks-ops-security-audit"
  enabled          = true
  role_arns        = [aws_iam_role.rolesanywhere_audit[0].arn]
  duration_seconds = 14400
}

## The trail reader role (the trail copy job) ------------------------------------------------------------------------

data "aws_iam_policy_document" "rolesanywhere_trail_trust" {
  count = local.trail_enabled ? 1 : 0

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
      values   = [var.rolesanywhere_trust_anchor_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = var.rolesanywhere_trail_common_names
    }
  }
}

resource "aws_iam_role" "rolesanywhere_trail" {
  count = local.trail_enabled ? 1 : 0

  name                 = "tnwks-rolesanywhere-security-trail"
  description          = "Roles Anywhere role for the trail copy job: read and list on the trail bucket only"
  assume_role_policy   = data.aws_iam_policy_document.rolesanywhere_trail_trust[0].json
  max_session_duration = 14400
}

resource "aws_iam_role_policy" "rolesanywhere_trail" {
  count = local.trail_enabled ? 1 : 0

  name   = "trail-bucket-read"
  role   = aws_iam_role.rolesanywhere_trail[0].id
  policy = data.aws_iam_policy_document.trail_reader.json
}

resource "aws_rolesanywhere_profile" "trail" {
  count = local.trail_enabled ? 1 : 0

  name             = "tnwks-ops-security-trail"
  enabled          = true
  role_arns        = [aws_iam_role.rolesanywhere_trail[0].arn]
  duration_seconds = 14400
}
