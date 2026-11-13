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

## ---------------------------------------------------------------------------------------------------------------------
## ONE TRAIL ONLY
## Only the first copy of management events is free, and each further copy is
## billed for each event. So each plan and each apply lists the trails of both
## accounts through Cloud Control (the aws provider has no data source that
## lists trails), and warns, with its name, while an account lists a trail
## other than the one above. The member account can list the organization
## trail too, by its name or by its ARN.
##
## These are checks and not preconditions for one reason: the data source
## fails on an empty list. Inside a check that failure is a warning ("Empty
## result") and the run goes on. A trail of the same name that Terraform does
## not know fails the apply at CreateTrail.
## ---------------------------------------------------------------------------------------------------------------------

check "one_trail_management" {
  data "awscc_cloudtrail_trails" "management" {
    provider = awscc.management
  }

  assert {
    condition = alltrue([
      for id in data.awscc_cloudtrail_trails.management.ids :
      id == local.trail_name || endswith(id, ":trail/${local.trail_name}")
    ])
    error_message = "The management account lists a trail that this module does not manage (all of its trails: ${join(", ", data.awscc_cloudtrail_trails.management.ids)}). A second copy of management events is billed for each event. Remove that trail, or bring it into this module."
  }
}

check "one_trail_member" {
  data "awscc_cloudtrail_trails" "member" {}

  assert {
    condition = alltrue([
      for id in data.awscc_cloudtrail_trails.member.ids :
      id == local.trail_name || endswith(id, ":trail/${local.trail_name}")
    ])
    error_message = "The prod account lists a trail that this module does not manage (all of its trails: ${join(", ", data.awscc_cloudtrail_trails.member.ids)}). A second copy of management events is billed for each event. Remove that trail, or bring it into this module."
  }
}
