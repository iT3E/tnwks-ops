## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Account/region context and the Identity Center instance.
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_ssoadmin_instances" "ssoadmin_instance" {}
