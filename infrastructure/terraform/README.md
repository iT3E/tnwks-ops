# Bootstrap AWS Environment

## Manual Steps

### Terraform Cloud Init
  1. Create app.terraform.io Organization called `tnwks-ops` CLI-driven workspace called `tnwks-ops-init`
  2. Run /infrastructure/terraform/tf-cloud/ to:
    - create workspaces:
      - `tnwks-ops-aws-init`
        - point to /infrastructure/terraform/aws/init
        - CLI run
      - `tnwks-ops-aws-identity`
        - point to /infrastructure/terraform/aws/accounts/identity
        - VCS run
        - Run trigger on tnwks-ops-aws-init
      - `tnwks-ops-aws-prod`
        - point to /infrastructure/terraform/aws/accounts/prod
        - VCS run
        - Run trigger on tnwks-ops-aws-identity

    - Set up Github OAUTH for VCS functionality.

### AWS Init - Org Owner Account
  3. Create Org owner AWS Account
  5. Generate programmatic Access Key and Secret Key for Root user
  6. Add root user credentials to encrypted /aws/init/secrets.sops.yaml file.
  7. Using `tnwks-ops-aws-init` workspace, run tf /aws/init/ as CLI run (this will run with root user credentials):
    - Terraform OIDC user in Org owner AWS Account
  8. Run /aws/accounts/identity to set up below in Org owner AWS Account:
    - Import root user and secure
      - import access_key and change status to Inactive
        - data block to pull access key ID for import
    - IAM Identity center
    - Permission Set
    - New Org OU
    - New Org AWS Account - Prod
      - IAM Role for child accounts

### AWS Init - Prod Account
  7.








# CLI Usage Instructions

##### Prerequisite:  `aws configure sso --profile sso`

1. `aws sso login --profile sso`
2. `terraform login`
3. `<terraform.io_token_value>`
4. `terraform init`
5. `terraform plan`
6. `terraform apply -auto-approve`
