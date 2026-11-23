variable "monthly_budget_usd" {
  description = "Monthly consolidated AWS cost budget in USD."
  type        = number
}

variable "subscriber_email" {
  description = "Recipient of AWS budget and action notifications."
  type        = string
  sensitive   = true
}

variable "prod_account_id" {
  description = "Production member account where AWS Budgets restricts provisioning."
  type        = string
}
