data "aws_organizations_organization" "management" {
  provider                 = aws.management
  return_organization_only = true
}

import {
  to = module.billing.aws_organizations_organization.current
  id = data.aws_organizations_organization.management.id
}
