## ---------------------------------------------------------------------------------------------------------------------
## LOCALS
## Shared local values.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  tags = {
    Environment = "prod"
    # Add more tags as needed.
  }
}

locals {
  identity_store_id = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids)[0]
  instance_arn      = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.arns)[0]
}
