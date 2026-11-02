## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## ---------------------------------------------------------------------------------------------------------------------

variable "unifi_url" {
  description = "Controller URL, without /api. Overrides unifi_url in secrets.sops.yaml. CI sets the in-cluster Service."
  type        = string
  default     = null
}
