## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICIES
## What the two readers may do. Every action here reads.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  # The two AWS managed policies that Prowler asks for on a scanned account.
  audit_managed_policy_arns = {
    security-audit = "arn:aws:iam::aws:policy/SecurityAudit"
    view-only      = "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"
  }

  # Prowler's additions to those two (permissions/prowler-additions-policy.json
  # of Prowler 5.49.0, the release that security/catalog/prowler.json follows),
  # less its one action that writes: securityhub:BatchImportFindings. Move this
  # list with the catalogue when Prowler moves, and keep it read-only.
  audit_additions_actions = [
    "account:Get*",
    "amplify:ListApps",
    "amplify:ListBranches",
    "appstream:Describe*",
    "appstream:List*",
    "backup:List*",
    "backup:Get*",
    "bedrock-agentcore:GetAgentRuntime",
    "bedrock-agentcore:GetBrowser",
    "bedrock-agentcore:GetCodeInterpreter",
    "bedrock-agentcore:GetGateway",
    "bedrock-agentcore:GetGatewayTarget",
    "bedrock-agentcore:GetMemory",
    "bedrock-agentcore:GetTokenVault",
    "bedrock-agentcore:ListAgentRuntimes",
    "bedrock-agentcore:ListBrowsers",
    "bedrock-agentcore:ListCodeInterpreters",
    "bedrock-agentcore:ListGatewayTargets",
    "bedrock-agentcore:ListGateways",
    "bedrock-agentcore:ListMemories",
    "bedrock-agentcore:ListTagsForResource",
    "bedrock:List*",
    "bedrock:Get*",
    "cloudtrail:GetInsightSelectors",
    "codeartifact:List*",
    "codebuild:BatchGet*",
    "codebuild:ListReportGroups",
    "cognito-idp:GetUserPoolMfaConfig",
    "datapipeline:DescribePipelines",
    "datapipeline:GetPipelineDefinition",
    "datapipeline:ListPipelines",
    "dlm:Get*",
    "drs:Describe*",
    "ds:Get*",
    "ds:Describe*",
    "ds:List*",
    "dynamodb:GetResourcePolicy",
    "ec2:GetEbsEncryptionByDefault",
    "ec2:GetSnapshotBlockPublicAccessState",
    "ec2:GetInstanceMetadataDefaults",
    "ecr:Describe*",
    "ecr:GetRegistryScanningConfiguration",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "elasticfilesystem:DescribeBackupPolicy",
    "glue:GetConnections",
    "glue:GetSecurityConfiguration*",
    "glue:SearchTables",
    "glue:GetMLTransforms",
    "inspector2:BatchGetFindingDetails",
    "inspector2:SearchVulnerabilities",
    "lambda:GetFunction*",
    "lambda:GetLayerVersion",
    "logs:FilterLogEvents",
    "lightsail:GetRelationalDatabases",
    "macie2:GetMacieSession",
    "macie2:GetAutomatedDiscoveryConfiguration",
    "oam:GetSinkPolicy",
    "oam:ListTagsForResource",
    "rolesanywhere:ListProfiles",
    "rolesanywhere:ListTagsForResource",
    "rolesanywhere:ListTrustAnchors",
    "s3:GetAccountPublicAccessBlock",
    "s3:GetObjectAcl",
    "s3:ListBucket",
    "shield:DescribeProtection",
    "shield:GetSubscriptionState",
    "securityhub:GetFindings",
    "servicecatalog:Describe*",
    "servicecatalog:List*",
    "ssm:GetDocument",
    "ssm-incidents:List*",
    "states:ListTagsForResource",
    "support:Describe*",
    "tag:GetTagKeys",
    "wellarchitected:List*",
  ]
}

data "aws_iam_policy_document" "audit_additions" {
  statement {
    sid       = "AllowMoreReadOnly"
    actions   = local.audit_additions_actions
    resources = ["*"]
  }

  statement {
    sid     = "AllowAPIGatewayReadOnly"
    actions = ["apigateway:GET"]
    resources = [
      "arn:*:apigateway:*::/restapis/*",
      "arn:*:apigateway:*::/apis/*",
      "arn:*:apigateway:*::/domainnames",
      "arn:*:apigateway:*::/domainnames/*",
    ]
  }
}

# The trail copy job: list the trail bucket and read its objects. It can't
# write, delete or change a setting of the bucket.
data "aws_iam_policy_document" "trail_reader" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.trail.arn]
  }

  statement {
    sid       = "ReadObjects"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.trail.arn}/*"]
  }
}
