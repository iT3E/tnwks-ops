data "aws_caller_identity" "management" {}

data "aws_organizations_organization" "current" {}

# An unfiltered budget in the management account measures consolidated spend.
resource "aws_budgets_budget" "monthly" {
  account_id   = data.aws_caller_identity.management.account_id
  name         = "tnwks-monthly-aws"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_unit   = "USD"
  limit_amount = tostring(var.monthly_budget_usd)

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold_type             = "PERCENTAGE"
    threshold                  = 80
    subscriber_email_addresses = [var.subscriber_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold_type             = "PERCENTAGE"
    threshold                  = 100
    subscriber_email_addresses = [var.subscriber_email]
  }
}

# Budgets owns the attachment and removes it when the action resets. Keep the
# policy itself in Terraform so the scope is reviewable before deployment.
resource "aws_organizations_policy" "restrict_provisioning" {
  name        = "tnwks-budget-restrict-provisioning"
  description = "Temporarily block selected new AWS capacity in prod when the monthly budget is exceeded."
  type        = "SERVICE_CONTROL_POLICY"
  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyNewCapacity"
      Effect = "Deny"
      Action = [
        "autoscaling:CreateAutoScalingGroup",
        "ec2:AllocateAddress",
        "ec2:CreateNatGateway",
        "ec2:CreateFleet",
        "ec2:CreateVolume",
        "ec2:RequestSpotFleet",
        "ec2:RequestSpotInstances",
        "ec2:RunInstances",
        "ec2:StartInstances",
        "ecs:CreateService",
        "ecs:RunTask",
        "eks:CreateCluster",
        "eks:CreateNodegroup",
        "elasticloadbalancing:CreateLoadBalancer",
        "es:CreateDomain",
        "lambda:CreateFunction",
        "rds:CreateDBCluster",
        "rds:CreateDBInstance",
        "rds:CreateDBInstanceReadReplica",
        "rds:StartDBCluster",
        "rds:StartDBInstance",
        "redshift:CreateCluster",
        "s3:CreateBucket",
        "sagemaker:CreateEndpoint",
      ]
      Resource = "*"
    }]
  })

  lifecycle {
    precondition {
      condition     = contains(data.aws_organizations_organization.current.enabled_policy_types, "SERVICE_CONTROL_POLICY")
      error_message = "AWS Organizations must have the service control policy type enabled for the budget action."
    }
  }
}

resource "aws_iam_role" "budget_action" {
  name = "tnwks-budget-action"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "budgets.amazonaws.com" }
      Condition = {
        ArnLike      = { "aws:SourceArn" = "arn:aws:budgets::${data.aws_caller_identity.management.account_id}:budget/*" }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.management.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "budget_action" {
  name = "tnwks-budget-provisioning-policy"
  role = aws_iam_role.budget_action.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["organizations:AttachPolicy", "organizations:DetachPolicy"]
        Resource = [
          aws_organizations_policy.restrict_provisioning.arn,
          "arn:aws:organizations::${data.aws_caller_identity.management.account_id}:account/${data.aws_organizations_organization.current.id}/${var.prod_account_id}",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["organizations:DescribePolicy", "organizations:ListTargetsForPolicy"]
        Resource = aws_organizations_policy.restrict_provisioning.arn
      },
    ]
  })
}

resource "aws_budgets_budget_action" "restrict_provisioning" {
  account_id         = aws_budgets_budget.monthly.account_id
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_SCP_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    scp_action_definition {
      policy_id  = aws_organizations_policy.restrict_provisioning.id
      target_ids = [var.prod_account_id]
    }
  }

  subscriber {
    address           = var.subscriber_email
    subscription_type = "EMAIL"
  }

  depends_on = [aws_iam_role_policy.budget_action]
}
