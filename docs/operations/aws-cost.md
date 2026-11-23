# AWS cost and budget

Grafana's **Observability → Cloud Cost** dashboard shows consolidated AWS
spend, the monthly limit, budget used and remaining, the AWS forecast,
and trends over the last 30 days. AWS Budgets supplies both the spend
and limit. Months begin at 00:00 UTC.

The [AWS budget metrics workflow](../../.github/workflows/aws-budget-metrics.yaml)
reads that budget every four hours from the homelab-wsl ops runner. Its
short-lived AWS role uses GitHub OIDC and can read the one budget. It
publishes the values to the in-cluster Pushgateway. Prometheus and Thanos
serve snapshots and history to Grafana. The job logs the result of the
collection without printing spend figures. The Global datasource includes
the WSL observation, and selecting only Site 2 excludes it.

## Budget and action

The [production AWS Terraform root](../../infrastructure/terraform/environments/prod/aws)
owns the recurring **$50 USD** monthly cost budget in the AWS Organizations
management account. Budget notifications use the existing SOPS-managed
admin email. AWS emails when actual spend exceeds $40, when projected
month-end spend exceeds $50, and when the automatic action runs.

At 100% actual spend, AWS Budgets attaches a service control policy to the
production member account. The policy denies selected operations that
provision or start capacity, including EC2, RDS, EKS, ECS, and new S3
buckets. AWS Budgets owns the attachment and resets the action at the next
budget period. Terraform imports the existing organization and enables
the service control policy type first. AWS attaches its default
`FullAWSAccess` policy when the policy type is enabled. The budget
action owns the attachment of the restrictive policy in prod.

AWS updates budget data up to three times daily, typically 8–12 hours
apart. Charges can exceed $50 before the action runs. Existing resources,
ongoing usage, and the management account can continue incurring charges.
See [AWS Budgets refresh behavior](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
and [SCP coverage](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html).

## Verify after deployment

1. Confirm the AWS Terraform Cloud apply and Flux's Pushgateway and
   dashboard reconciliations. The workflow may run before both are ready.
2. Check that `tnwks-monthly-aws` and its automatic SCP action are visible
   in [AWS Budgets](https://console.aws.amazon.com/billing/home#/budgets).
   Review the action's status and history there when the threshold is hit.
3. Run the AWS budget metrics workflow on `main` or wait for the next
   scheduled run. Open Cloud Cost and compare the last collection time,
   actual spend and limit against AWS Budgets. An unavailable AWS forecast
   can leave its two panels empty.

If the last collection is from a previous UTC month or is at least 14
hours old, current spend and limit panels show missing evidence. Check the
workflow for OIDC, budget read or Pushgateway errors, then check that
Prometheus scrapes the Pushgateway. A Pushgateway restart discards its
in-memory snapshot, so the next publisher run repopulates current metrics.

For recovery during a restriction, review and reverse the executed action
from AWS Budgets. Reversal lasts through the remainder of the current
period unless the action is reset. See
[AWS budget action history and reversal](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-action-review.html).
