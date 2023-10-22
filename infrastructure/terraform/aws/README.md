# Bootstrap AWS Environment

## Manual Steps

### Terraform Cloud Init
  1. Create app.terraform.io VCS workspace called tnwks-ops-init
    - point this workspace at /infrastructure/terraform/tf-cloud/
  2. Run /tf-cloud/ to create workspaces

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

### AWS Init - Org Owner Account
  3. Create Org owner AWS Account
  4. Create AWS user with AdministratorAccess policy
  5. Generate programmatic Access Key and Secret Key for Admin User
  6. Add Admin user credentials to `tnwks-ops-aws-init` workspace.
  7. Run tf /aws/init/ to set up:
    - Terraform OIDC user in Org owner AWS Account
    - Wait for success, then
      - import Admin user to tf
      - delete Admin user
  6. Run /aws/accounts/identity to set up below in Org owner AWS Account:
    - IAM Identity center
    - Permission Set
    - New Org OU
    - New Org AWS Account - Prod
      - IAM Role for child accounts

### AWS Init - Prod Account
  7.



