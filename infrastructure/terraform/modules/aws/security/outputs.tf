## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## Re-exported by the calling environment. The profile and role ARNs go to
## the credential refresher of homelab-wsl-02 (kubernetes/apps/security).
## ---------------------------------------------------------------------------------------------------------------------

output "trail_bucket" {
  description = "S3 bucket that the organization trail writes to."
  value       = aws_s3_bucket.trail.bucket
}

output "trail_arn" {
  description = "ARN of the organization trail."
  value       = aws_cloudtrail.organization.arn
}

output "rolesanywhere_audit_profile_arn" {
  description = "Roles Anywhere profile of the audit role (aws_signing_helper --profile-arn)."
  value       = one(aws_rolesanywhere_profile.audit[*].arn)
}

output "rolesanywhere_audit_role_arn" {
  description = "The audit role that a Roles Anywhere session assumes (aws_signing_helper --role-arn)."
  value       = one(aws_iam_role.rolesanywhere_audit[*].arn)
}

output "rolesanywhere_trail_profile_arn" {
  description = "Roles Anywhere profile of the trail reader role (aws_signing_helper --profile-arn)."
  value       = one(aws_rolesanywhere_profile.trail[*].arn)
}

output "rolesanywhere_trail_role_arn" {
  description = "The trail reader role that a Roles Anywhere session assumes (aws_signing_helper --role-arn)."
  value       = one(aws_iam_role.rolesanywhere_trail[*].arn)
}

output "audit_management_role_arn" {
  description = "The audit role of the management account, assumed from the audit role of the prod account."
  value       = aws_iam_role.audit_management.arn
}
