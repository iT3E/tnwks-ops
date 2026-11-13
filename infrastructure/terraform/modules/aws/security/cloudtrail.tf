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

  # Every trail that either account lists, other than this one (the member
  # account lists the organization trail too, by name or by ARN).
  other_trails = sort([
    for id in setunion(data.awscc_cloudtrail_trails.management.ids, data.awscc_cloudtrail_trails.member.ids) :
    id if id != local.trail_name && !endswith(id, ":trail/${local.trail_name}")
  ])
}

# The trails of each account, through Cloud Control (the aws provider has no
# data source that lists trails). Read on every plan, for the guard below.
data "awscc_cloudtrail_trails" "management" {
  provider = awscc.management
}

data "awscc_cloudtrail_trails" "member" {}

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

  lifecycle {
    # Only the first copy of management events is free, and each further copy
    # is billed for each event. So a plan fails here, before anything is
    # made, while an account has a trail that this module does not manage.
    precondition {
      condition     = length(local.other_trails) == 0
      error_message = "An account of the organization lists a trail that this module does not manage: ${join(", ", local.other_trails)}. A second copy of management events is billed for each event. Remove that trail, or bring it into this module, and plan again."
    }
  }

  depends_on = [
    aws_s3_bucket_policy.trail,
    aws_organizations_aws_service_access.cloudtrail,
  ]
}
