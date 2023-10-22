# Bootstrap AWS Environment

## Manual Steps
  1. Create Organization owner AWS Account
  2. Create app.terraform.io VCS workspace called tnwks-ops-init
    - point this workspace at /infrastructure/terraform/tf-cloud/
  4. Run /tf-cloud/ to create workspaces
    - tnwks-ops-aws-init
      - point to /infrastructure/terraform/aws/init
    - tnwks-ops-aws-identity
      - point to /infrastructure/terraform/aws/accounts/identity
    - tnwks-ops-aws-prod
      - point to /infrastructure/terraform/aws/accounts/prod
  5. Set
  6. Run /aws/accounts/identity to set up:
    - IAM Identity center
    - Permission Set
    - Terraform OIDC setup
    - New Org OU
    - IAM Role for child accounts
  7.


