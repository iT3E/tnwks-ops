# A short-lived role for the workflow publishing the budget snapshot to Grafana.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "budget_reader" {
  name = "tnwks-grafana-budget-reader"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud"           = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub"           = "repo:iT3E/tnwks-ops:ref:refs/heads/main"
          "token.actions.githubusercontent.com:repository_id" = "636416946"
          "token.actions.githubusercontent.com:workflow"      = "AWS budget metrics"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "budget_reader" {
  name = "tnwks-grafana-budget-reader"
  role = aws_iam_role.budget_reader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "budgets:ViewBudget"
      Resource = aws_budgets_budget.monthly.arn
    }]
  })
}
