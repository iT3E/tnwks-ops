# Adopt the existing organization to manage its root's enabled policy types.
# Service integrations such as CloudTrail have their own Terraform resources.
resource "aws_organizations_organization" "current" {
  enabled_policy_types = ["SERVICE_CONTROL_POLICY"]

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [aws_service_access_principals, feature_set]
  }
}
