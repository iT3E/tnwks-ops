## ---------------------------------------------------------------------------------------------------------------------
## CLOUDTRAIL: THE ORGANIZATION TRAIL
## One trail in the management account for every account of the organization:
## management events, every Region, log file validation. It writes to the
## bucket of the prod account (s3_trail.tf). Nothing else: no data events, no
## Insights, no CloudWatch Logs, no KMS key. The first copy of management events
## is free, so the cost is the bucket's requests and storage.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  trail_name = "tnwks-organization-trail"

  # Built here and not read from the trail: the bucket policy names the trail,
  # and the trail needs the bucket policy before it can be made.
  trail_arn = "arn:aws:cloudtrail:${data.aws_region.management.region}:${data.aws_caller_identity.management.account_id}:trail/${local.trail_name}"
}

# An organization trail made through the API needs trusted access for
# CloudTrail in the organization first. CloudTrail makes its own
# service-linked role when the trail is made.
resource "aws_organizations_aws_service_access" "cloudtrail" {
  provider = aws.management

  service_principal = "cloudtrail.amazonaws.com"
}

resource "aws_cloudtrail" "organization" {
  provider = aws.management

  name           = local.trail_name
  s3_bucket_name = aws_s3_bucket.trail.id

  is_organization_trail         = true
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  depends_on = [
    aws_s3_bucket_policy.trail,
    aws_organizations_aws_service_access.cloudtrail,
  ]
}
