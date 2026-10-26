## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Optional, and unset everywhere except CI.
## ---------------------------------------------------------------------------------------------------------------------

variable "routeros_url" {
  description = <<-EOT
    Overrides the REST endpoint from secrets.sops.yaml (the router's wg01 address).
    CI sets TF_VAR_routeros_url to its wireproxy tunnel (https://127.0.0.1:8443,
    which forwards over wg03 to the router), see docs/mikrotik-ci.md.
  EOT
  type        = string
  default     = null
}
