## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES: THE AUDIT ROLE OF THE MANAGEMENT ACCOUNT
## Roles Anywhere lives in the prod account, so the audit of the management
## account is a second hop: Prowler assumes this role from its Roles Anywhere
## session (rolesanywhere.tf). The role trusts that one role and nothing else,
## and carries the same read-only policies.
## ---------------------------------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "audit_management_trust" {
  statement {
    effect = "Allow"
    # SetSourceIdentity, TagSession: a Roles Anywhere session carries a source
    # identity and session tags into the role chain.
    actions = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    # The account principal with the role's ARN as a condition, and not the
    # role as the principal: IAM stores a role principal by its ID, which
    # would stop matching if the role is ever made again.
    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [local.audit_role_arn]
    }
  }
}

resource "aws_iam_role" "audit_management" {
  provider = aws.management

  name               = "tnwks-security-audit"
  description        = "Security audit (Prowler) of the management account: read-only, assumed from the prod account's Roles Anywhere audit role"
  assume_role_policy = data.aws_iam_policy_document.audit_management_trust.json
}

resource "aws_iam_role_policy_attachment" "audit_management" {
  provider = aws.management
  for_each = local.audit_managed_policy_arns

  role       = aws_iam_role.audit_management.name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "audit_management_additions" {
  provider = aws.management

  name   = "audit-additions"
  role   = aws_iam_role.audit_management.id
  policy = data.aws_iam_policy_document.audit_additions.json
}
